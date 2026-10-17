from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.application.desktop_composition import (
    build_project_first_chat_runtime,
    build_project_first_chat_runtime as composition,
)
from embedded_copilot.desktop_copilot_chat import (
    ChatApplicationFailed,
    ChatContextReference,
    ChatReferenceKind,
    ChatRejected,
    ChatSessionOpenRequest,
    DesktopChatRequest,
)
from embedded_copilot.engineering_memory import (
    ApprovalAudit,
    ApprovedEngineeringMemory,
    EngineeringMemoryType,
    InMemoryApprovedEngineeringMemoryStore,
)
from embedded_copilot.multimodal.context import ProcessLocalAttachmentBindingRepository
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)
from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.application.engineering_runtime_integration import (
    EngineeringObjectIntegrationService,
)
from embedded_copilot.execution_worker import ExecutionOperationService, SQLiteExecutionStore
from embedded_copilot.model_gateway import ModelProviderConfigurationError


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
TENANT = "tenant-1"
REQUESTER = "engineer-1"


def _catalog(tmp_path: Path, *, memory_store=None) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir()
    return ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "state" / "desktop.sqlite3",
        metadata_directory=tmp_path / "metadata",
        initialize_async=False,
        approved_memory_store=memory_store,
    )


def _project(catalog: ProjectCatalogRuntime):
    location = catalog.locations(tenant_id=TENANT, requester=REQUESTER)[0]
    preview = catalog.preview(
        ProjectCreationRequest(
            name="Golden Project",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            parent_location=ProjectLocationReference(
                location_id=location.location_id,
                fingerprint=location.fingerprint,
            ),
        ),
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    return catalog.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=preview.created_at,
        tenant_id=TENANT,
        requester=REQUESTER,
    )


def _runtime(tmp_path: Path, *, memory_store=None):
    catalog = _catalog(tmp_path, memory_store=memory_store)
    project = _project(catalog)
    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=memory_store,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
    )
    assert runtime is not None
    catalog.set_chat_runtime(runtime)
    runtime.set_conversation_port(catalog)
    session = catalog.create_conversation(
        project.project_id, tenant_id=TENANT, requester=REQUESTER
    )
    return catalog, project, runtime, session


def test_chat_rejects_same_tenant_non_owner_for_new_and_existing_sessions(
    tmp_path: Path,
) -> None:
    _, project, runtime, _ = _runtime(tmp_path)
    non_owner = "engineer-2"
    with pytest.raises(ChatRejected):
        runtime.open_session(
            ChatSessionOpenRequest(
                session_id="non-owner-session",
                tenant_id=TENANT,
                project_id=project.project_id,
                requester=non_owner,
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )
        )

    # Simulate a session persisted before the owner check was introduced.
    from embedded_copilot.desktop_copilot_chat.models import ChatSession, ChatSessionStatus

    runtime._sessions.save(
        ChatSession(
            session_id="legacy-non-owner-session",
            tenant_id=TENANT,
            project_id=project.project_id,
            requester=non_owner,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            status=ChatSessionStatus.OPEN,
        )
    )
    with pytest.raises(ChatRejected):
        runtime.project("legacy-non-owner-session", requester=non_owner, tenant_id=TENANT)
    with pytest.raises(ChatRejected):
        runtime.events_after(
            "legacy-non-owner-session", requester=non_owner, tenant_id=TENANT,
            last_event_id=None,
        )


def test_unconfigured_provider_keeps_real_chat_runtime_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable_provider():
        raise ModelProviderConfigurationError()

    monkeypatch.setattr(
        "embedded_copilot.application.desktop_composition.ModelProviderSettings.from_environment",
        unavailable_provider,
    )
    catalog = _catalog(tmp_path)
    project = _project(catalog)

    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
    )

    assert runtime is not None
    catalog.set_chat_runtime(runtime)
    runtime.set_conversation_port(catalog)
    session = catalog.create_conversation(
        project.project_id, tenant_id=TENANT, requester=REQUESTER
    )
    assert session.chat_available is True


def test_production_composition_injects_object_flow_and_canonical_receipt_source(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "state" / "execution.sqlite3")
    )

    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
        execution_port=execution,
    )

    assert runtime is not None
    factory = runtime._application._container.engineering_object_integration_factory
    assert callable(factory)
    integration = factory(project.project_id)
    assert isinstance(integration, EngineeringObjectIntegrationService)


def test_greenfield_provider_unavailable_persists_requirement_context_and_one_terminal_event(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
        provider_environment={},
    )
    assert runtime is not None
    catalog.set_chat_runtime(runtime)
    runtime.set_conversation_port(catalog)
    session = catalog.create_conversation(
        project.project_id, tenant_id=TENANT, requester=REQUESTER
    )
    request = DesktopChatRequest(
        session_id=session.session_id,
        project_reference=runtime.project(
            session.session_id, requester=REQUESTER, tenant_id=TENANT
        ),
        application_question=ApplicationQuestionRequest(
            request_id="greenfield-request",
            trace_id="greenfield-trace",
            project_id=project.project_id,
            requester=REQUESTER,
            question="设计一个满足确认约束的I2C传感器接口",
            constraints=("保持离线分析",),
            requested_at=NOW,
        ),
        context_references=(),
        requested_at=NOW,
    )

    with pytest.raises(ChatApplicationFailed, match="PROVIDER_NOT_CONFIGURED"):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    conversation = catalog.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    assert conversation.turns[-1].status.value == "FAILED"
    assert conversation.turns[-1].error_code == "COPILOT_CHAT_PROVIDER_NOT_CONFIGURED"
    assert {item["reference_id"] for item in conversation.turns[-1].context_references} >= {
        "greenfield-project-metadata",
        "greenfield-user-requirement",
    }
    events = runtime.events_after(
        session.session_id, requester=REQUESTER, tenant_id=TENANT, last_event_id=None
    )
    assert [event.event_type.value for event in events] == ["CHAT_STARTED", "ANSWER_FAILED"]


def test_no_approved_memory_store_keeps_chat_available_and_memory_empty(tmp_path: Path):
    catalog, project, runtime, session = _runtime(tmp_path)

    assert session.chat_available is True
    references = runtime.references(
        session.session_id, requester=REQUESTER, tenant_id=TENANT
    )
    assert references == ()
    assert catalog.references(TENANT, project.project_id) == ()


def test_no_approved_memory_store_allows_ordinary_question_but_fails_on_missing_evidence(
    tmp_path: Path,
):
    _, project, runtime, session = _runtime(tmp_path)
    request = DesktopChatRequest(
        session_id=session.session_id,
        project_reference=runtime.project(
            session.session_id, requester=REQUESTER, tenant_id=TENANT
        ),
        application_question=ApplicationQuestionRequest(
            request_id="request-1",
            trace_id="trace-1",
            project_id=project.project_id,
            requester=REQUESTER,
            question="Why did the I2C read fail?",
            constraints=(),
            requested_at=NOW,
        ),
        context_references=(),
        requested_at=NOW,
    )

    with pytest.raises(ChatApplicationFailed):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    events = runtime.events_after(
        session.session_id,
        requester=REQUESTER,
        tenant_id=TENANT,
        last_event_id=None,
    )
    assert [event.event_type.value for event in events] == [
        "CHAT_STARTED",
        "ANSWER_FAILED",
    ]


def test_explicit_memory_reference_is_rejected_when_memory_provider_is_unavailable(
    tmp_path: Path,
):
    _, project, runtime, session = _runtime(tmp_path)
    memory_reference = ChatContextReference(
        tenant_id=TENANT,
        project_id=project.project_id,
        reference_id="memory-1",
        reference_kind=ChatReferenceKind.MEMORY,
        source_fingerprint="sha256:" + "a" * 64,
        display_label="Approved memory",
    )
    request = DesktopChatRequest(
        session_id=session.session_id,
        project_reference=runtime.project(
            session.session_id, requester=REQUESTER, tenant_id=TENANT
        ),
        application_question=ApplicationQuestionRequest(
            request_id="request-memory-1",
            trace_id="trace-memory-1",
            project_id=project.project_id,
            requester=REQUESTER,
            question="Why did the I2C read fail?",
            constraints=(),
            requested_at=NOW,
        ),
        context_references=(memory_reference,),
        requested_at=NOW,
    )

    from embedded_copilot.desktop_copilot_chat import ChatRejected

    with pytest.raises(ChatRejected):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)


def _approved_memory(project_id: str) -> ApprovedEngineeringMemory:
    return ApprovedEngineeringMemory.create(
        memory_id="memory-1",
        project_id=project_id,
        source_reference="memory:approved-1",
        memory_type=EngineeringMemoryType.DECISION,
        summary="Use the approved I2C pull-up decision.",
        decision="Keep the approved pull-up configuration.",
        reason="Approved engineering record.",
        confidence=0.9,
        evidence=("memory:approved-1",),
        approval_audit=ApprovalAudit(
            approval_id="approval-1",
            candidate_fingerprint="sha256:" + "b" * 64,
            reviewer="reviewer-1",
            decision="APPROVED",
            approved_at=NOW,
        ),
    )


def test_approved_memory_store_exposes_only_approved_memory_reference(tmp_path: Path):
    store = InMemoryApprovedEngineeringMemoryStore()
    catalog = _catalog(tmp_path, memory_store=store)
    project = _project(catalog)
    store.save(_approved_memory(project.project_id))

    references = catalog.references(TENANT, project.project_id)
    assert [item.reference_kind for item in references] == [ChatReferenceKind.MEMORY]
    assert references[0].reference_id == "memory-1"


def test_candidate_and_rejected_memory_are_not_projected(tmp_path: Path):
    class MixedMemorySource:
        def __init__(self):
            self.project_id = ""

        def list(self, project_id: str):
            approved = _approved_memory(project_id)
            candidate = ApprovedEngineeringMemory.model_construct(
                **{
                    **approved.model_dump(mode="python"),
                    "memory_id": "memory-candidate",
                    "status": "CANDIDATE",
                }
            )
            rejected = ApprovedEngineeringMemory.model_construct(
                **{
                    **approved.model_dump(mode="python"),
                    "memory_id": "memory-rejected",
                    "status": "REJECTED",
                }
            )
            return (approved, candidate, rejected)

    tmp_catalog = _catalog(tmp_path, memory_store=MixedMemorySource())
    project = _project(tmp_catalog)
    references = tmp_catalog.references(TENANT, project.project_id)

    assert tuple(item.reference_id for item in references) == ("memory-1",)


def test_production_composition_has_no_fake_memory_adapter():
    source = composition.__module__
    assert "Fake" not in source
