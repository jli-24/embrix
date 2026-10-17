from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Mapping

from embedded_copilot.desktop_copilot_chat import (
    ChatContextReference,
    ChatMemoryStatus,
    ChatObservationStatus,
    ChatProjectReference,
    ChatReferenceKind,
    ChatRejected,
    ChatRuntimeHealth,
    ChatRuntimePanelView,
    DesktopCopilotChatRuntime,
)
from embedded_copilot.desktop_copilot_chat.adapters import (
    SQLiteApplicationAuditSink,
    SQLiteChatAuditSink,
    SQLiteChatEventStore,
    SQLiteChatStore,
    SQLiteChatTraceStore,
    SystemChatClock,
)
from embedded_copilot.engineering_context import (
    EngineeringContextService,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_context.models import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
)
from embedded_copilot.engineering_context.adapters import (
    ExternalKnowledgeEvidenceAdapter,
    ProjectEngineeringEvidenceAdapter,
    ProjectKnowledgeEvidenceAdapter,
)
from embedded_copilot.knowledge.external import OfficialWebSearchSettings
from embedded_copilot.engineering_knowledge import (
    EngineeringKnowledgeGraphProjectionService,
)
from embedded_copilot.engineering_memory import ApprovedMemoryProjection
from embedded_copilot.engineering_memory.foundation_repository import (
    EngineeringMemoryRepositoryPort,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryContextProjectionAdapter,
    EngineeringMemoryService,
)
from embedded_copilot.engineering_memory.foundation_sqlite import (
    FoundationMemoryMigrationError,
    SQLiteEngineeringMemoryRepository,
)
from embedded_copilot.engineering_runtime import EngineeringObjectRuntime
from embedded_copilot.knowledge_evolution.adapters.memory import (
    ApprovedEngineeringMemoryKnowledgeAdapter,
)
from embedded_copilot.model_gateway import (
    ModelGatewayService,
    ModelGatewayUnavailable,
    ModelInvocationPort,
    ModelPolicy,
    ModelProviderConfigurationError,
    ModelProviderSettings,
    create_model_provider,
)
from embedded_copilot.project_catalog import ProjectCatalogForbidden, ProjectCatalogNotFound
from .bindings import CanonicalSupervisorBinding
from .container import ApplicationDependencyContainer
from embedded_copilot.firmware_agent_adapter import (
    FirmwareAgentApplicationAdapter,
    FirmwarePromptAdapter,
)
from embedded_copilot.debug_agent_adapter import (
    DebugAgentApplicationAdapter,
    DebugPromptAdapter,
)
from embedded_copilot.firmware_intelligence import (
    FirmwareIntelligenceService,
    FirmwareSourceProvider,
    FirmwareSourceUnavailable,
)
from .runtime import ApplicationRuntime
from .engineering_runtime_integration import (
    EngineeringObjectIntegrationService,
    OperationReceiptEvidenceResolver,
)
from embedded_copilot.v63_memory_governance import V63MemoryGovernanceService


class _UnavailableModelGateway:
    """Production-safe model boundary for an unconfigured provider."""

    is_configured = False

    def generate(self, request: object):
        del request
        raise ModelGatewayUnavailable()

    async def agenerate(self, request: object):
        del request
        raise ModelGatewayUnavailable()


def _foundation_database_path(database: object) -> object:
    """Return the opt-in sibling path without a filesystem-module import."""

    raw = str(database)
    separator = max(raw.rfind("/"), raw.rfind("\\"))
    prefix, name = raw[: separator + 1], raw[separator + 1 :]
    if "." in name and not name.startswith("."):
        stem, extension = name.rsplit(".", 1)
        suffix = "." + extension
    else:
        stem, suffix = name, ".sqlite3"
    if not stem:
        raise TypeError("database path is invalid")
    return f"{prefix}{stem}-engineering-memory{suffix}"


class _V63ContextProjectionAdapter:
    __slots__ = ("_service",)

    def __init__(self, service: V63MemoryGovernanceService) -> None:
        self._service = service

    def list_memory_context(
        self, tenant_id: str, project_id: str
    ) -> tuple[EngineeringContextEvidence, ...]:
        return tuple(
            EngineeringContextEvidence.create(
                evidence_id=f"v63-memory-{item.memory_id}",
                tenant_id=tenant_id,
                project_id=project_id,
                source_type=ContextSourceType.ENGINEERING_MEMORY,
                subject=item.subject,
                summary=item.statement,
                source_reference=item.source_references[0],
                source_fingerprint=item.fingerprint,
                confidence=item.confidence,
                verification_status=ContextVerificationStatus.APPROVED,
                provenance=(item.fingerprint,),
            )
            for item in self._service.project_memories(tenant_id, project_id)
        )

    def list_knowledge_context(
        self, tenant_id: str, project_id: str
    ) -> tuple[EngineeringContextEvidence, ...]:
        return tuple(
            EngineeringContextEvidence.create(
                evidence_id=f"v63-knowledge-{item.knowledge_id}",
                tenant_id=tenant_id,
                project_id=project_id,
                source_type=ContextSourceType.KNOWLEDGE_EVOLUTION,
                subject=item.subject,
                summary=f"{item.predicate}: {item.value}",
                source_reference=item.source_references[0],
                source_fingerprint=item.fingerprint,
                confidence=1.0,
                verification_status=ContextVerificationStatus.APPROVED,
                provenance=(item.fingerprint,),
            )
            for item in self._service.project_knowledge(tenant_id, project_id)
        )


class _ApprovedMemoryProjectionAdapter:
    """Adapt approved memory records to the existing context projection Port."""

    __slots__ = ("_source",)

    def __init__(self, source: object) -> None:
        self._source = source

    @staticmethod
    def _projection(value: object) -> ApprovedMemoryProjection:
        if type(value) is ApprovedMemoryProjection:
            return ApprovedMemoryProjection.model_validate(
                copy.deepcopy(value.model_dump(mode="python"))
            )
        memory_id = getattr(value, "memory_id", None)
        project_id = getattr(value, "project_id", None)
        if not isinstance(memory_id, str) or not isinstance(project_id, str):
            raise TypeError("approved memory projection is invalid")
        source_reference = getattr(value, "source_reference", None)
        summary = getattr(value, "summary", None)
        evidence = getattr(value, "evidence", ())
        memory_type = getattr(getattr(value, "memory_type", None), "value", None)
        approval = getattr(value, "approval_audit", None)
        candidate_fingerprint = getattr(approval, "candidate_fingerprint", None)
        if not all(
            isinstance(item, str)
            for item in (source_reference, summary, memory_type, candidate_fingerprint)
        ):
            raise TypeError("approved memory projection is invalid")
        material = {
            "project_id": project_id,
            "memory_id": memory_id,
            "record_id": memory_id,
            "memory_type": memory_type,
            "status": "APPROVED",
            "source_reference": source_reference,
            "source_revision": candidate_fingerprint,
            "title": memory_type,
            "summary": summary,
            "evidence_references": tuple(evidence),
        }
        provisional = ApprovedMemoryProjection.model_construct(
            **material, fingerprint="sha256:" + "0" * 64
        )
        payload = json.dumps(
            provisional.model_dump(mode="json", exclude={"fingerprint"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ApprovedMemoryProjection.model_validate(
            {**material, "fingerprint": "sha256:" + hashlib.sha256(payload).hexdigest()}
        )

    def list_approved(self, project_id: str) -> tuple[ApprovedMemoryProjection, ...]:
        values = self._source.list(project_id)
        if not isinstance(values, tuple):
            raise TypeError("approved memory source must return a tuple")
        projections = tuple(
            self._projection(value)
            for value in values
            if getattr(value, "status", None) == "APPROVED"
        )
        if any(value.project_id != project_id for value in projections):
            raise ValueError("approved memory project binding is invalid")
        return projections


class _ChatEnvironmentProjection:
    __slots__ = ("_clock", "_environment")

    def __init__(self, environment: object | None, clock: SystemChatClock) -> None:
        self._environment = environment
        self._clock = clock

    def panel(self, tenant_id: str, project_id: str) -> ChatRuntimePanelView:
        generated_at = self._clock.now()
        if self._environment is None:
            return ChatRuntimePanelView(
                hil_status=ChatObservationStatus.UNAVAILABLE,
                eda_status=ChatObservationStatus.UNAVAILABLE,
                memory_statuses=(),
                runtime_health=(),
                generated_at=generated_at,
            )
        try:
            health = self._environment.runtime_health(tenant_id)
            memories = self._environment.memory_views(tenant_id, project_id=project_id)
            runtime_health = tuple(
                ChatRuntimeHealth(
                    component=item.component.value,
                    status=item.status.value,
                    observed_at=item.observed_at,
                )
                for item in health
            )
            statuses = tuple(
                ChatMemoryStatus(item.status.value)
                for item in memories
                if item.project_id == project_id
                and item.status.value
                in {
                    ChatMemoryStatus.REVIEW_REQUIRED.value,
                    ChatMemoryStatus.APPROVED.value,
                }
            )
            return ChatRuntimePanelView(
                hil_status=ChatObservationStatus.UNAVAILABLE,
                eda_status=ChatObservationStatus.UNAVAILABLE,
                memory_statuses=statuses,
                runtime_health=runtime_health,
                generated_at=generated_at,
            )
        except Exception as error:
            raise RuntimeError("environment projection is unavailable") from error


class _FirmwareChatProjectProjection:
    """Add verified parser references to the existing chat projection port.

    This adapter deliberately exposes only parser/index facts through the
    existing safe ChatContextReference contract. It never exposes source text,
    absolute paths, or a filename-only reference.
    """

    __slots__ = ("_firmware", "_projects")

    def __init__(self, projects: object, firmware: FirmwareIntelligenceService) -> None:
        if not callable(getattr(projects, "project", None)) or not callable(
            getattr(projects, "references", None)
        ):
            raise TypeError("chat project projection is invalid")
        if not isinstance(firmware, FirmwareIntelligenceService):
            raise TypeError("firmware intelligence service is invalid")
        self._projects = projects
        self._firmware = firmware

    def project(self, tenant_id: str, project_id: str) -> ChatProjectReference:
        value = self._projects.project(tenant_id, project_id)
        if type(value) is not ChatProjectReference:
            raise TypeError("chat project projection is invalid")
        return ChatProjectReference.model_validate(value.model_dump(mode="python"))

    def project_for_requester(
        self, tenant_id: str, project_id: str, requester: str
    ) -> ChatProjectReference:
        try:
            authorized = self._projects.get_project_for_requester(
                project_id, tenant_id=tenant_id, requester=requester
            )
        except (ProjectCatalogForbidden, ProjectCatalogNotFound) as error:
            raise ChatRejected("chat project access rejected") from error
        if authorized.tenant_id != tenant_id or authorized.project_id != project_id:
            raise ChatRejected("chat project projection mismatch")
        return ChatProjectReference(
            tenant_id=tenant_id,
            project_id=authorized.project_id,
            project_name_reference=authorized.name,
            workspace_reference=f"project:{authorized.project_id}",
        )

    @staticmethod
    def _firmware_reference(
        tenant_id: str, project_id: str, symbol: object
    ) -> ChatContextReference | None:
        symbol_project = getattr(symbol, "project_id", None)
        file_reference = getattr(symbol, "file_reference", None)
        symbol_name = getattr(symbol, "symbol_name", None)
        line_start = getattr(symbol, "line_start", None)
        fingerprint = getattr(symbol, "fingerprint", None)
        if (
            symbol_project != project_id
            or not isinstance(file_reference, str)
            or not isinstance(symbol_name, str)
            or type(line_start) is not int
            or line_start < 1
            or not isinstance(fingerprint, str)
        ):
            return None
        reference_id = (
            "firmware:" + hashlib.sha256(fingerprint.encode("ascii")).hexdigest()[:48]
        )
        try:
            return ChatContextReference(
                tenant_id=tenant_id,
                project_id=project_id,
                reference_id=reference_id,
                reference_kind=ChatReferenceKind.EVIDENCE,
                source_fingerprint=fingerprint,
                display_label=(
                    f"FIRMWARE_SOURCE · {file_reference} · {symbol_name} · L{line_start}"
                ),
            )
        except (TypeError, ValueError, UnicodeError):
            return None

    def references(
        self, tenant_id: str, project_id: str
    ) -> tuple[ChatContextReference, ...]:
        base = self._projects.references(tenant_id, project_id)
        references = tuple(
            ChatContextReference.model_validate(item.model_dump(mode="python"))
            for item in base
            if type(item) is ChatContextReference
        )
        if len(references) != len(base):
            raise TypeError("chat context projection is invalid")
        # Project Catalog remains the authority for tenant/project binding.
        self.project(tenant_id, project_id)
        try:
            index = self._firmware.index(project_id)
        except FirmwareSourceUnavailable:
            return references
        if index.project_id != project_id:
            return references
        additions = tuple(
            item
            for symbol in index.symbols
            if (item := self._firmware_reference(tenant_id, project_id, symbol))
            is not None
        )
        seen = {item.fingerprint for item in references}
        return references + tuple(
            item for item in additions if item.fingerprint not in seen
        )


def build_project_first_chat_runtime(
    *,
    database: object,
    project_catalog: object,
    approved_memory_store: object | None,
    v63_memory_governance: V63MemoryGovernanceService | None = None,
    environment: object | None,
    attachments: object,
    knowledge_search_port: object | None = None,
    provider_environment: Mapping[str, str] | None = None,
    provider_binding: object | None = None,
    provider_policy: ModelPolicy | None = None,
    model_invocation_service: ModelInvocationPort | None = None,
    transmission_approval: object | None = None,
    governed: bool = False,
    engineering_memory_repository: EngineeringMemoryRepositoryPort | None = None,
    persistent_engineering_memory: bool = False,
    execution_port: object | None = None,
    firmware_build_execution_bridge: object | None = None,
) -> DesktopCopilotChatRuntime | None:
    """Compose existing Project Catalog, Application and Chat Ports only."""

    try:
        if type(persistent_engineering_memory) is not bool:
            raise ValueError("persistent engineering memory flag is invalid")
        if persistent_engineering_memory and engineering_memory_repository is not None:
            raise ValueError("persistent engineering memory dependencies conflict")
        if persistent_engineering_memory:
            engineering_memory_repository = SQLiteEngineeringMemoryRepository(
                _foundation_database_path(database)
            )
        memory_projection = None
        graph_projection = None
        if approved_memory_store is not None:
            memory_projection = _ApprovedMemoryProjectionAdapter(approved_memory_store)
            graph_projection = EngineeringKnowledgeGraphProjectionService(
                ApprovedEngineeringMemoryKnowledgeAdapter(approved_memory_store)
            )
        v63_projection = (
            _V63ContextProjectionAdapter(v63_memory_governance)
            if v63_memory_governance is not None
            else None
        )
        knowledge_projection = ProjectKnowledgeEvidenceAdapter(
            project_catalog.knowledge_snapshot_root
        )
        if knowledge_search_port is not None:
            knowledge_projection.bind_knowledge_agent(
                getattr(
                    importlib.import_module("embedded_copilot.agents.knowledge"),
                    "KnowledgeAgent",
                )(retrieval_port=knowledge_search_port)
            )
        external_knowledge_projection = ExternalKnowledgeEvidenceAdapter(
            settings=OfficialWebSearchSettings.from_environment()
        )
        engineering_projection = ProjectEngineeringEvidenceAdapter(
            project_catalog.knowledge_snapshot_root,
            lambda project_id, tenant_id: project_catalog.get_project(
                project_id, tenant_id=tenant_id
            ),
        )
        context_source = ReadOnlyProjectionSource(
            memory_port=memory_projection,
            graph_port=graph_projection,
            knowledge_port=knowledge_projection,
            external_knowledge_port=external_knowledge_projection,
            engineering_port=engineering_projection,
            v63_memory_port=v63_projection,
            v63_knowledge_port=v63_projection,
        )
        if engineering_memory_repository is not None:
            context_source = EngineeringMemoryContextProjectionAdapter(
                service=EngineeringMemoryService(engineering_memory_repository),
                source=context_source,
            )
        context_provider = EngineeringContextService(source_port=context_source)
        if firmware_build_execution_bridge is None and execution_port is not None:
            if all(
                callable(getattr(execution_port, name, None))
                for name in ("create_operation", "issue_approval", "enqueue")
            ):
                from embedded_copilot.execution_worker import FirmwareBuildExecutionBridge

                firmware_build_execution_bridge = FirmwareBuildExecutionBridge(
                    execution_port
                )
        engineering_object_integration_factory = None
        if execution_port is not None:
            if not callable(getattr(execution_port, "get_receipt", None)) or not callable(
                getattr(execution_port, "get_operation", None)
            ):
                raise TypeError("canonical execution receipt source is invalid")

            def engineering_object_integration_factory(project_id: str):
                return EngineeringObjectIntegrationService(
                    EngineeringObjectRuntime(project_id=project_id),
                    evidence_port=OperationReceiptEvidenceResolver(execution_port),
                    context_provider=context_provider,
                )

        try:
            # Production composition passes an explicit mapping.  This keeps
            # dotenv/environment discovery out of the packaged secret path;
            # an absent controlled configuration remains fail-closed.
            if governed or model_invocation_service is not None:
                if model_invocation_service is None:
                    raise ValueError("production invocation service is required")
                gateway = None
                model_policy = None
            elif provider_binding is not None:
                from embedded_copilot.model_gateway import ModelGatewayPort

                if not isinstance(provider_binding, ModelGatewayPort):
                    raise TypeError("production provider binding is invalid")
                gateway = provider_binding
                model_policy = (
                    ModelPolicy.model_validate(
                        copy.deepcopy(provider_policy.model_dump(mode="python"))
                    )
                    if provider_policy is not None
                    else ModelPolicy(
                        primary_provider="unavailable", model_name="unavailable"
                    )
                )
            else:
                provider_settings = (
                    ModelProviderSettings.from_environment()
                    if provider_environment is None
                    else ModelProviderSettings.from_environment(provider_environment)
                )
                provider = create_model_provider(settings=provider_settings)
                gateway = ModelGatewayService(
                    providers={provider_settings.provider: provider}
                )
                model_policy = provider_settings.to_model_policy()
        except ModelProviderConfigurationError:
            gateway = _UnavailableModelGateway()
            model_policy = ModelPolicy(
                primary_provider="unavailable",
                model_name="unavailable",
            )
        clock = SystemChatClock()
        audit_store = SQLiteApplicationAuditSink(database)
        firmware_intelligence = FirmwareIntelligenceService(
            source_provider=FirmwareSourceProvider(
                project_catalog.knowledge_snapshot_root
            )
        )
        chat_projects = _FirmwareChatProjectProjection(
            project_catalog, firmware_intelligence
        )
        application = ApplicationRuntime(
            ApplicationDependencyContainer(
                agent_runtime=CanonicalSupervisorBinding(
                    project_mode=lambda project_id, tenant_id: (
                        project_catalog.get_project(project_id, tenant_id=tenant_id)
                    )
                ),
                context_provider=context_provider,
                mode="GOVERNED" if governed or model_invocation_service is not None else "LEGACY",
                model_gateway=gateway,
                model_invocation_service=model_invocation_service,
                audit_sink=audit_store,
                clock=clock,
                model_policy=model_policy,
                firmware_agent=FirmwareAgentApplicationAdapter(firmware_intelligence),
                firmware_prompt=FirmwarePromptAdapter(),
                debug_agent=DebugAgentApplicationAdapter(),
                debug_prompt=DebugPromptAdapter(),
                research_service=getattr(project_catalog, "research_service", None),
                engineering_object_integration_factory=engineering_object_integration_factory,
                firmware_build_execution_bridge=firmware_build_execution_bridge,
            )
        )
        chat_store = SQLiteChatStore(database)
        return DesktopCopilotChatRuntime(
            application=application,
            projects=chat_projects,
            environment=_ChatEnvironmentProjection(environment, clock),
            sessions=chat_store,
            audit=SQLiteChatAuditSink(chat_store),
            events=SQLiteChatEventStore(chat_store),
            traces=SQLiteChatTraceStore(chat_store),
            clock=clock,
            attachments=attachments,
            transmission_approval=transmission_approval,
        )
    except (
        FoundationMemoryMigrationError,
        ModelProviderConfigurationError,
        TypeError,
        ValueError,
        OSError,
    ):
        return None


__all__ = ("build_project_first_chat_runtime",)
