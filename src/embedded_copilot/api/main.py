from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import logging
import os
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from embedded_copilot.api.copilot_routes import (
    WorkspaceService,
    router as copilot_router,
)
from embedded_copilot.api.memory_routes import router as memory_router
from embedded_copilot.api.intelligence_routes import router as intelligence_router
from embedded_copilot.api.reasoning_routes import router as reasoning_layer_router
from embedded_copilot.api.autonomous_routes import router as autonomous_loop_router
from embedded_copilot.api.generation_routes import router as generation_router
from embedded_copilot.api.workspace_routes import router as workspace_router
from embedded_copilot.api.toolchain_routes import router as toolchain_router
from embedded_copilot.api.component_routes import router as component_router
from embedded_copilot.api.device_routes import router as device_router
from embedded_copilot.api.observation_routes import router as observation_router
from embedded_copilot.api.validation_routes import router as validation_router
from embedded_copilot.api.autonomous_v20_routes import router as autonomous_v20_router
from embedded_copilot.api.tool_adapter_routes import router as tool_adapter_router
from embedded_copilot.api.hardware_v22_routes import router as hardware_v22_router
from embedded_copilot.api.debug_v23_routes import router as debug_v23_router
from embedded_copilot.api.optimization_v23_routes import router as optimization_v23_router
from embedded_copilot.api.firmware_v24_routes import router as firmware_v24_router
from embedded_copilot.api.hil_v25_routes import router as hil_v25_router
from embedded_copilot.api.knowledge_v27_routes import router as knowledge_v27_router
from embedded_copilot.api.engineering_completion_v28_routes import (
    router as engineering_completion_v28_router,
)
from embedded_copilot.api.multimodal_v29_routes import router as multimodal_v29_router
from embedded_copilot.api.context_adapters import (
    CopilotContextReferenceResolver,
    CopilotDatasheetContextSource,
    CopilotFileContextSource,
    CopilotVisionContextSource,
    UnavailableEngineeringContextPort,
)
from embedded_copilot.api.experience_routes import (
    ExperienceService,
    router as experience_router,
)
from embedded_copilot.api.desktop_product_routes import router as desktop_product_router
from embedded_copilot.api.desktop_copilot_chat_routes import (
    router as desktop_copilot_chat_router,
)
from embedded_copilot.api.copilot_environment_routes import (
    router as copilot_environment_router,
)
from embedded_copilot.api.model_consumption_routes import (
    router as model_consumption_router,
)
from embedded_copilot.api.project_catalog_routes import router as project_catalog_router
from embedded_copilot.api.copilot_capability_routes import (
    router as copilot_capability_router,
)
from embedded_copilot.api.copilot_knowledge_routes import (
    router as copilot_knowledge_router,
)
from embedded_copilot.api.copilot_engineering_routes import (
    router as copilot_engineering_router,
)
from embedded_copilot.api.copilot_consistency_routes import (
    router as copilot_consistency_router,
)
from embedded_copilot.api.copilot_virtual_assembly_routes import (
    router as copilot_virtual_assembly_router,
)
from embedded_copilot.api.copilot_review_routes import (
    router as copilot_review_router,
)
from embedded_copilot.api.copilot_operation_routes import (
    router as copilot_operation_router,
)
from embedded_copilot.api.copilot_execution_routes import (
    router as copilot_execution_router,
)
from embedded_copilot.api.copilot_firmware_build_routes import (
    router as copilot_firmware_build_router,
)
from embedded_copilot.application.firmware_build_context_source import FirmwareBuildContextSource
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    FirmwareBuildExecutionBridge,
    ArtifactStore,
    ExecutionToolRegistry,
    FirmwareBuildToolAdapter,
    PCBExecutionBridge,
    FirmwareSourceWriteAdapter,
    PCBWriteAdapter,
    KiCadCliPCBDrCAdapter,
    KiCadCliPCBParseAdapter,
    KiCadFootprintAvailability,
    PCBProductionService,
    discover_kicad_footprint_root,
    discover_kicad_cli,
    discover_kicad_symbol_root,
    discover_kicad_pcb_drc_command,
    discover_kicad_pcb_parse_command,
    KiCadCliSchematicErcAdapter,
    SchematicWriteAdapter,
    SchematicSymbolLibraryValidator,
    UnavailableSchematicErcAdapter,
    UnavailableSchematicSymbolLibraryValidator,
    OperationStatus,
    OperationType,
    probe_kicad_cli_version,
    probe_trusted_tool_version,
    SQLiteExecutionStore,
    SQLiteTargetRegistry,
    TrustedToolDiscovery,
)
from embedded_copilot.manufacturing import (
    CanonicalManufacturingEntryGate,
    FabricationReadinessService,
    FabricationStore,
    KiCadManufacturingAdapter,
    ManufacturingPipeline,
)
from embedded_copilot.api.fabrication_routes import router as fabrication_router
from embedded_copilot.execution_worker.native_move import NativeMoveProductionAdapter, NativeMoveProductionSetup
from embedded_copilot.execution_worker.schematic_xml import KiCadCliSchematicXmlExporter, SchematicXmlAdapter
from embedded_copilot.engineering_copilot.schematic_parameter_readers import ProjectMappedParameterPort
from embedded_copilot.schematic_execution import SchematicExecutionBridge
from embedded_copilot.engineering_workspace_apply import ProjectCatalogWorkspaceTargetResolver
from embedded_copilot.api.copilot_validation_proposal_routes import (
    router as copilot_validation_proposal_router,
)
from embedded_copilot.api.copilot_workspace_routes import (
    router as copilot_workspace_router,
)
from embedded_copilot.api.desktop_workspace_projection_routes import (
    router as desktop_workspace_projection_router,
)
from embedded_copilot.api.v70_release_routes import router as v70_release_router
from embedded_copilot.api.desktop_update_routes import router as desktop_update_router
from embedded_copilot.api.desktop_identity import (
    DesktopSessionCookieMiddleware,
    ProductionLocalSessionIdentityAdapter,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.production_release.desktop_update import DesktopUpdateService
from embedded_copilot.production_release.paths import versioned_database_path
from embedded_copilot.production_release.credential_save_diagnostic import (
    ConfigurationDispatchDiagnosticMiddleware,
)
from embedded_copilot.production_release.test_connection_diagnostic import (
    TestConnectionDispatchDiagnosticMiddleware,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    WebViewSessionDiagnosticMiddleware,
)
from embedded_copilot.production_release.provider import (
    ProviderConfigurationCapabilityProbeAdapter,
    ProviderConfigurationService,
    ProviderVerificationReceiptStore,
    TransmissionApprovalService,
)
from embedded_copilot.production_release.model_discovery import (
    OfficialModelDiscovery,
    OfficialModelProfileDiscovery,
)
from embedded_copilot.production_release.model_pool import ProviderModelPool
from embedded_copilot.api.file_reference_catalog import (
    CopilotFileReferenceCatalog,
)
from embedded_copilot.api.reasoning_adapters import ContextBackedReasoningService
from embedded_copilot.api.routes import ChatService, ProductAnalysisService, router
from embedded_copilot.datasheet_runtime import (
    DatasheetIntelligencePort,
    create_datasheet_runtime,
)
from embedded_copilot.context_runtime import (
    EngineeringContextPort,
    create_engineering_context_runtime,
)
from embedded_copilot.file_runtime import (
    FileIntelligencePort,
    create_file_runtime,
)
from embedded_copilot.model_runtime import create_model_runtime
from embedded_copilot.model_status import UnavailableStatusPort
from embedded_copilot.model_gateway import (
    ModelProviderConfigurationError,
    ModelProviderSettings,
    ModelGatewayManagementService,
    ManagedModelSelectionResolver,
    ModelInvocationService,
    ModelSelectionError,
    ModelSelectionTaskProfile,
    EngineeringStage,
    SQLiteModelGatewayStore,
    AccountUsagePort,
    provider_presets,
)
from embedded_copilot.api.model_gateway_routes import router as model_gateway_router
from embedded_copilot.reasoning_runtime import (
    ReasoningPort,
    create_reasoning_runtime,
)
from embedded_copilot.reasoning import ReasoningService
from embedded_copilot.multimodal.context import (
    ProcessLocalAttachmentBindingRepository,
)
from embedded_copilot.memory_automation import (
    MemoryApplicationService,
    MemoryServicePort,
)
from embedded_copilot.schemas.api import ChatResponse
from embedded_copilot.schemas.result import ErrorCode, ErrorDetail
from embedded_copilot.services.config import Settings
from embedded_copilot.services.experience_runtime import build_experience_runtime
from embedded_copilot.services.legacy_runtime import build_legacy_runtime
from embedded_copilot.services.canonical_runtime import (
    build_canonical_runtime,
)
from embedded_copilot.vision_runtime import (
    VisionPort,
    create_vision_runtime,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogError,
    ProjectCatalogRuntime,
    ProjectCatalogUnavailable,
)
from embedded_copilot.application.desktop_composition import (
    build_project_first_chat_runtime,
)
from embedded_copilot.application.project_knowledge_search import (
    ProjectCatalogKnowledgeSearchPort,
)
from embedded_copilot.component_recommendation.catalog import load_approved_component_source
from embedded_copilot.rag.embedding import (
    EmbeddingModelUnavailable,
    EmbeddingProvider,
    EmbeddingProviderError,
    OllamaEmbeddingAdapter,
)
from embedded_copilot.application.project_firmware_analysis import (
    ProjectFirmwareAnalysisPort,
)
from embedded_copilot.desktop_workspace_projection import DesktopWorkspaceProjectionService
from embedded_copilot.v63_memory_governance import (
    SQLiteV63Store,
    V63MemoryGovernanceService,
)
from embedded_copilot.api.copilot_v63_routes import router as copilot_v63_router
from embedded_copilot.engineering_validation_proposal import (
    DeterministicValidationRules,
    ValidationProposalService,
)
from embedded_copilot.engineering_validation_proposal.adapters import (
    SQLiteV64Store,
    V63ApprovedContextSource,
    V63EvidenceSource,
)
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceApplyPort,
    SQLiteWorkspaceApplyStore,
    SystemWorkspaceClock,
    WorkspaceApplyService,
)

logger = logging.getLogger(__name__)


class _ExecutionWorkerWorkspaceResolver:
    """Bind generic worker snapshots to the canonical project workspace."""

    def __init__(self, workspace: WorkspaceApplyService, target_resolver: object) -> None:
        self._workspace = workspace
        self._target_resolver = target_resolver

    def resolve(self, *, tenant_id: str, project_id: str, snapshot_id: str, snapshot_fingerprint: str) -> Path:
        snapshot = self._workspace.snapshot(
            tenant_id=tenant_id,
            project_id=project_id,
            target_id="project-root",
        )
        # Snapshot fingerprints include capture time and therefore are not a
        # stable identity for the same workspace contents. Bind execution to
        # the deterministic project fingerprint instead.
        expected_id = "workspace-snapshot-" + hashlib.sha256(snapshot.project_fingerprint.encode("utf-8")).hexdigest()[:24]
        if snapshot_id != expected_id or snapshot.project_fingerprint != snapshot_fingerprint:
            raise ValueError("workspace snapshot binding mismatch")
        target = self._target_resolver.resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            target_id="project-root",
        )
        return target.root


def _build_desktop_workspace_projection(
    *,
    project_catalog: object | None,
    memory: object | None,
    validation: object | None,
    workspace: object | None,
    execution: object | None,
    capabilities: object | None,
    audit: object | None = None,
) -> DesktopWorkspaceProjectionService | None:
    """Create the canonical read-only Desktop aggregation only from real ports."""

    if project_catalog is None:
        return None
    try:
        return DesktopWorkspaceProjectionService(
            catalog=project_catalog,
            memory=memory,
            validation=validation,
            workspace=workspace,
            execution=execution,
            capabilities=capabilities,
            audit=audit,
        )
    except TypeError:
        logger.warning(
            "desktop_workspace_projection_unavailable",
            extra={"event_name": "desktop_workspace_projection_unavailable"},
        )
        return None


def _desktop_capability_values(application: FastAPI, project_id: str | None = None) -> tuple[dict[str, str], ...]:
    statuses = getattr(application.state, "runtime_capability_statuses", {})
    selection = getattr(application.state, "model_selection_resolver", None)
    governed_chat_model: dict[str, str] | None = None
    if project_id is not None and selection is not None:
        try:
            choice = selection.select_governed_model(
                ModelSelectionTaskProfile(
                    request_id="desktop-capability", project_id=project_id,
                    stage=EngineeringStage.REQUIREMENT_ANALYSIS,
                    task_type="REQUIREMENT_ANALYSIS",
                )
            )
            pending = choice.selection_reason in {
                "AUTO", "AUTO_ROUTER_PENDING", "ALLOWED_POOL_ROUTER_INPUT",
                "CANDIDATE_GROUP", "CANDIDATE_GROUP_PENDING",
            }
            insufficient = choice.selection_reason == "AUTO_INSUFFICIENT_EVIDENCE"
            ready = choice.selected_model is not None and not pending and not insufficient
            if ready:
                status, reason, detail = (
                    "AVAILABLE", "GOVERNED_SELECTION_READY",
                    "Requirement analysis model selection gates passed; no live provider probe was made.",
                )
            elif pending or insufficient:
                status, reason, detail = (
                    "UNVERIFIED", "MODEL_SELECTION_PENDING_ROUTER" if pending else "INSUFFICIENT_EVIDENCE",
                    "Requirement analysis model selection is not yet verified.",
                )
            else:
                status, reason, detail = (
                    "UNAVAILABLE", "MODEL_SELECTION_FAILED",
                    "Requirement analysis model selection has no eligible model.",
                )
            governed_chat_model = {
                "capability": "CHAT_MODEL", "status": status,
                "reason_code": reason, "detail": detail,
            }
        except ModelSelectionError as error:
            governed_chat_model = {
                "capability": "CHAT_MODEL",
                "status": "UNVERIFIED" if error.insufficient else "UNAVAILABLE",
                "reason_code": error.code,
                "detail": "Requirement analysis model selection is not verified." if error.insufficient else "Requirement analysis model selection is unavailable under the current policy.",
            }
        except (TypeError, ValueError, RuntimeError):
            governed_chat_model = {
                "capability": "CHAT_MODEL", "status": "UNAVAILABLE",
                "reason_code": "MODEL_SELECTION_UNAVAILABLE",
                "detail": "Requirement analysis model selection cannot be read.",
            }
    values: list[dict[str, str]] = []
    for name, key in (
        ("PROJECT_CATALOG", "project_catalog"),
        ("CHAT_ANALYSIS", "context_provider"),
        ("PROVIDER_CONFIGURATION", "provider_configuration"),
        ("CHAT_MODEL", "chat_model"),
        ("ENGINEERING_ANALYSIS_COMPLETION", "engineering_analysis_completion"),
        ("KNOWLEDGE_EMBEDDING", "embedding_provider"),
        ("KNOWLEDGE_ANALYSIS", "knowledge_search"),
        ("ENGINEERING_MEMORY", "v63_memory_governance"),
    ):
        if name == "CHAT_MODEL" and governed_chat_model is not None:
            values.append(governed_chat_model)
            continue
        raw = str(statuses.get(key, "unavailable")).upper()
        status = {"AVAILABLE": "AVAILABLE", "DEGRADED": "DEGRADED", "UNVERIFIED": "UNVERIFIED"}.get(raw, "UNAVAILABLE")
        values.append({"capability": name, "status": status, "reason_code": "CONFIGURED" if status == "AVAILABLE" else f"{name}_UNAVAILABLE", "detail": f"{name} capability is {status.lower()}."})
    values.extend(
        {
            "capability": name,
            "status": "AVAILABLE" if getattr(application.state, attr, None) is not None else "UNAVAILABLE",
            "reason_code": "CONFIGURED" if getattr(application.state, attr, None) is not None else reason,
            "detail": detail,
        }
        for name, attr, reason, detail in (
            ("WORKSPACE_APPLY", "workspace_apply_port", "WORKSPACE_APPLY_UNAVAILABLE", "Controlled Workspace Apply is unavailable."),
            ("EXECUTION_WORKER", "execution_operation_port", "EXECUTION_WORKER_UNAVAILABLE", "The Execution Worker projection is unavailable."),
        )
    )
    controlled_build_status = getattr(application.state, "controlled_build_status", "UNAVAILABLE")
    for name, reason, detail in (
        ("CONTROLLED_BUILD", "TRUSTED_TOOLCHAIN_UNAVAILABLE", "Trusted toolchain capability is unavailable."),
        ("CONTROLLED_FLASH", "VERIFIED_TARGET_UNAVAILABLE", "Verified target capability is unavailable."),
        ("UART_OBSERVATION", "VERIFIED_TARGET_UNAVAILABLE", "Verified target capability is unavailable."),
        ("HIL_EXECUTION", "VERIFIED_TARGET_UNAVAILABLE", "Verified target capability is unavailable."),
    ):
        values.append({"capability": name, "status": controlled_build_status if name == "CONTROLLED_BUILD" else "UNAVAILABLE", "reason_code": "CONFIGURED" if name == "CONTROLLED_BUILD" and controlled_build_status == "AVAILABLE" else reason, "detail": detail})
    return tuple(values)


def _load_optional_optimization_router() -> Any | None:
    """Load the legacy optimization router without blocking core startup."""

    try:
        module = importlib.import_module(
            "embedded_copilot.api.optimization_v26_routes"
        )
        return module.router
    except (ImportError, ModuleNotFoundError) as error:
        logger.warning(
            "optional_router_unavailable",
            extra={
                "event_name": "optional_router_unavailable",
                "router": "optimization_v26",
                "error_type": type(error).__name__,
            },
        )
        return None


def _model_gateway_status(provider_service: ProviderConfigurationService | None = None) -> str:
    """Report configuration availability without exposing provider secrets."""

    if provider_service is not None:
        return "available" if provider_service.binding.is_configured else "unavailable"
    try:
        ModelProviderSettings.from_environment()
    except ModelProviderConfigurationError:
        return "unavailable"
    return "available"


def _record_runtime_component_status(
    application: FastAPI,
    *,
    project_catalog: object | None,
    chat_runtime: object | None,
    approved_memory_store: object | None,
    v63_memory_governance: object | None = None,
    embedding_provider: object | None,
    knowledge_search_port: object | None,
    embedding_status: str | None = None,
    embedding_reason_code: str | None = None,
    embedding_provider_configured: str | None = None,
    embedding_runtime_status: str | None = None,
    embedding_model_status: str | None = None,
    provider_service: ProviderConfigurationService | None = None,
) -> None:
    statuses = {
        "project_catalog": (
            "available" if isinstance(project_catalog, ProjectCatalogRuntime) else "unavailable"
        ),
        "model_gateway": _model_gateway_status(provider_service),
        "context_provider": "available" if chat_runtime is not None else "unavailable",
        "memory_provider": "available" if approved_memory_store is not None else "unavailable",
    }
    embedding_provider_name = embedding_provider_configured or "unconfigured"
    application.state.runtime_component_statuses = statuses
    application.state.runtime_capability_statuses = {
        **statuses,
        "v63_memory_governance": "available" if v63_memory_governance is not None else "unavailable",
        "embedding_provider": embedding_status or (
            "available" if embedding_provider is not None else "unavailable"
        ),
        "knowledge_search": "unverified"
        if knowledge_search_port is not None
        and not (
            embedding_provider_configured == "ollama"
            and (embedding_status or "unavailable") != "available"
        )
        else "unavailable",
        "embedding_provider_configured": embedding_provider_name,
        "embedding_runtime_status": embedding_runtime_status,
        "embedding_model_status": embedding_model_status,
    }
    if provider_service is not None:
        projection = provider_service.projection()
        application.state.provider_projection = projection
        application.state.runtime_capability_statuses.update(
            {
                "provider_configuration": (
                    "available" if projection.credential_status == "CONFIGURED" else "unavailable"
                ),
                "chat_model": (
                    "available"
                    if projection.connection_status.value == "AVAILABLE"
                    else "unavailable"
                ),
                "engineering_analysis_completion": (
                    "available"
                    if chat_runtime is not None
                    and projection.connection_status.value == "AVAILABLE"
                    else "unavailable"
                ),
            }
        )
    if embedding_reason_code is not None:
        application.state.runtime_capability_statuses["embedding_reason_code"] = embedding_reason_code
    logger.info(
        "runtime_component_status",
        extra={"event_name": "runtime_component_status", **statuses},
    )


class _UnsetService:
    pass


_UNSET_SERVICE = _UnsetService()


class _LazyVisionPort:
    __slots__ = ("_factory", "_port")

    def __init__(self, factory: Callable[[], VisionPort]) -> None:
        self._factory = factory
        self._port: VisionPort | None = None

    async def analyze(self, request: Any) -> Any:
        if self._port is None:
            self._port = self._factory()
        return await self._port.analyze(request)


class _LazyReasoningPort:
    __slots__ = ("_factory", "_port")

    def __init__(self, factory: Callable[[], ReasoningPort]) -> None:
        self._factory = factory
        self._port: ReasoningPort | None = None

    async def analyze(self, request: Any) -> Any:
        if self._port is None:
            self._port = self._factory()
        return await self._port.analyze(request)


def _include_desktop_composition(application: FastAPI) -> None:
    """Register only the Project-first, read-only Desktop surface."""

    for route in (
        desktop_copilot_chat_router,
        copilot_environment_router,
        project_catalog_router,
        model_consumption_router,
        copilot_capability_router,
        copilot_knowledge_router,
        copilot_engineering_router,
        copilot_consistency_router,
        copilot_virtual_assembly_router,
        copilot_review_router,
        copilot_v63_router,
        copilot_validation_proposal_router,
        copilot_operation_router,
        copilot_execution_router,
        copilot_firmware_build_router,
        copilot_workspace_router,
        desktop_workspace_projection_router,
        v70_release_router,
        desktop_update_router,
        fabrication_router,
        model_gateway_router,
    ):
        application.include_router(route)


def _include_legacy_composition(application: FastAPI) -> None:
    """Keep legacy Desktop/Console routes visibly outside Desktop composition."""

    application.include_router(desktop_product_router)


def _include_execution_composition(application: FastAPI, optimization_v26_router: Any) -> None:
    """Register legacy analysis routes only.

    v6.6 execution is composed through the typed operation API and local worker.
    The following routes are retained as compatibility projections for existing
    public contracts. They are not used by the v6.6 Desktop composition and
    remain dependent on their explicitly injected legacy ports.
    """

    for route in (
        router,
        copilot_router,
        experience_router,
        memory_router,
        intelligence_router,
        reasoning_layer_router,
        autonomous_loop_router,
        generation_router,
        workspace_router,
        toolchain_router,
        component_router,
        device_router,
        observation_router,
        validation_router,
        autonomous_v20_router,
        debug_v23_router,
        tool_adapter_router,
        hardware_v22_router,
        optimization_v23_router,
        firmware_v24_router,
        hil_v25_router,
        knowledge_v27_router,
        engineering_completion_v28_router,
        multimodal_v29_router,
    ):
        application.include_router(route)
    if optimization_v26_router is not None:
        application.include_router(optimization_v26_router)


def _error_response(
    *,
    trace_id: str,
    code: ErrorCode,
    message: str,
    status_code: int,
) -> JSONResponse:
    response = ChatResponse(
        answer="",
        trace_id=trace_id,
        error=ErrorDetail(code=code, message=message, retryable=False),
    )
    return JSONResponse(
        status_code=status_code, content=response.model_dump(mode="json")
    )


def create_app(
    *,
    settings: Settings | None = None,
    service: ChatService | None = None,
    analysis_service: ProductAnalysisService | None = None,
    workspace_service: WorkspaceService | None | _UnsetService = _UNSET_SERVICE,
    experience_service: ExperienceService | None | _UnsetService = _UNSET_SERVICE,
    vision_port: VisionPort | None | _UnsetService = _UNSET_SERVICE,
    file_port: FileIntelligencePort | None | _UnsetService = _UNSET_SERVICE,
    datasheet_port: DatasheetIntelligencePort | None | _UnsetService = _UNSET_SERVICE,
    context_port: EngineeringContextPort | None | _UnsetService = _UNSET_SERVICE,
    reasoning_port: ReasoningPort | None | _UnsetService = _UNSET_SERVICE,
    file_reference_paths: Mapping[tuple[str, str], str | Path] | None = None,
    memory_port: object | None = None,
    memory_writer: object | None = None,
    approved_memory_store: object | None = None,
    intelligence_port: object | None = None,
    intelligence_context_port: object | None = None,
    reasoning_layer_port: object | None = None,
    reasoning_input_resolver: object | None = None,
    autonomous_loop_port: object | None = None,
    generation_port: object | None = None,
    workspace_snapshot_port: object | None = None,
    toolchain_snapshot_port: object | None = None,
    component_recommendation_port: object | None = None,
    hardware_component_source: object | None = None,
    trusted_budget_source: object | None = None,
    trusted_budget_bundle_source: object | None = None,
    trusted_account_authority_source: object | None = None,
    trusted_counter_fee_source: object | None = None,
    trusted_output_cap_source: object | None = None,
    trusted_generation_tariff_source: object | None = None,
    trusted_transport_semantics_source: object | None = None,
    device_snapshot_port: object | None = None,
    flash_port: object | None = None,
    observation_snapshot_port: object | None = None,
    validation_loop_port: object | None = None,
    loop_coordinator_port: object | None = None,
    approval_gate_port: object | None = None,
    loop_state_port: object | None = None,
    memory_automation_port: object | None = None,
    tool_adapter_status_port: object | None = None,
    tool_adapter_build_port: object | None = None,
    tool_adapter_flash_port: object | None = None,
    tool_adapter_device_port: object | None = None,
    hardware_design_port: object | None = None,
    hardware_review_port: object | None = None,
    debug_analysis_port: object | None = None,
    optimization_port: object | None = None,
    optimization_approval_port: object | None = None,
    firmware_engineering_port: object | None = None,
    firmware_build_port: object | None = None,
    firmware_debug_port: object | None = None,
    hil_validation_port: object | None = None,
    device_observation_port: object | None = None,
    hardware_capability_port: object | None = None,
    digital_twin_port: object | None = None,
    optimization_analysis_port: object | None = None,
    knowledge_port: object | None = None,
    retrieval_port: object | None = None,
    engineering_completion_port: object | None = None,
    desktop_product_runtime: object | None = None,
    copilot_environment_runtime: object | None = None,
    desktop_copilot_chat_runtime: object | None = None,
    project_catalog_runtime: object | None = None,
    desktop_identity_provider: object | None = None,
    copilot_knowledge_search_port: object | None = None,
    project_knowledge_chroma_client: object | None = None,
    copilot_firmware_analysis_port: object | None = None,
    copilot_debug_analysis_port: object | None = None,
    copilot_review_port: object | None = None,
    copilot_operation_port: object | None = None,
    execution_operation_port: object | None = None,
    schematic_parse_adapter: object | None = None,
    schematic_readback_adapter: object | None = None,
    schematic_erc_port: object | None = None,
    schematic_parameter_port: object | None = None,
    workspace_apply_port: object | None = None,
    native_move_setup: NativeMoveProductionSetup | None = None,
    drc_baseline_collector: object | None = None,
    v63_memory_governance: V63MemoryGovernanceService | None = None,
    engineering_validation_proposal_service: ValidationProposalService | None = None,
    production_release_runtime: ProductionReleaseRuntime | None = None,
    account_usage_port: AccountUsagePort | None = None,
) -> FastAPI:
    active_settings = settings or Settings()
    active_production_release = production_release_runtime
    if active_production_release is None and active_settings.environment.lower() in {"production", "prod"}:
        # A production state boundary is core infrastructure.  Failure here is
        # deliberately fatal rather than a Fake/InMemory fallback.
        active_production_release = ProductionReleaseRuntime.create(active_settings)
    if active_production_release is not None:
        active_settings = active_production_release.settings
    model_gateway_management = ModelGatewayManagementService(
        store=(
            SQLiteModelGatewayStore(
                active_production_release.layout.database / "model-gateway.sqlite3"
            )
            if active_production_release is not None
            else None
        )
    )
    if not model_gateway_management.persistent:
        for preset in provider_presets():
            model_gateway_management.add_provider(preset)
    else:
        if not model_gateway_management.restore_from_store():
            for preset in provider_presets():
                model_gateway_management.add_provider(preset)
    if account_usage_port is not None:
        model_gateway_management.set_account_usage_port(account_usage_port)
    active_provider_service: ProviderConfigurationService | None = None
    active_model_discovery: OfficialModelDiscovery | None = None
    if active_production_release is not None:
        def credential_revision(provider_id: str) -> str | None:
            if active_provider_service is None:
                return None
            return active_provider_service.credential_revision_for_provider(provider_id)

        active_model_discovery = OfficialModelDiscovery(
            active_production_release.credentials,
            credential_revision_resolver=credential_revision,
        )
        active_provider_service = ProviderConfigurationService(
            configuration_path=active_production_release.layout.production_config,
            credentials=active_production_release.credentials,
            model_gateway_management=model_gateway_management,
            verification_store=ProviderVerificationReceiptStore(
                active_production_release.layout.database
                / "provider-verification.sqlite3"
            ),
            model_discovery=active_model_discovery,
        )
        model_gateway_management.set_capability_probe_port(
            ProviderConfigurationCapabilityProbeAdapter(active_provider_service)
        )
        model_gateway_management.set_discovery_port(
            OfficialModelProfileDiscovery(active_model_discovery)
        )
    model_invocation_service = None
    model_consumption_service = None
    model_selection_resolver = None
    if active_provider_service is not None:
        receipt_store = model_gateway_management.receipt_store
        if receipt_store is None or not all(callable(getattr(receipt_store, name, None)) for name in (
            "begin_attempt", "get_attempt_for_project", "mark_dispatch_intent",
            "mark_attempt_unknown", "complete_attempt",
        )):
            raise RuntimeError("production model invocation attempt store is unavailable")
        model_selection_resolver = ManagedModelSelectionResolver(
            model_gateway_management,
            active_provider_service.available_credential_refs,
            active_provider_service.credential_revision_for_connection,
            ProviderModelPool.endpoint_revision,
        )
        from embedded_copilot.application.model_consumption import ModelConsumptionService
        from embedded_copilot.model_gateway.invocation.consumption_source import (
            AccountAuthorityPort,
            ConsumptionGrantSource,
        )
        from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
            QualifiedInvocationBudgetProducer,
        )
        from embedded_copilot.model_gateway.invocation.qualified_bundle_producer import (
            QualifiedCounterBundleProducer,
        )
        grant_source = ConsumptionGrantSource(receipt_store, account_authority_source=cast(AccountAuthorityPort | None, trusted_account_authority_source))
        model_consumption_service = ModelConsumptionService(receipt_store, account_authority_source=cast(AccountAuthorityPort | None, trusted_account_authority_source))
        active_budget_bundle = trusted_budget_bundle_source if trusted_budget_bundle_source is not None else QualifiedCounterBundleProducer(
            grant_source=grant_source,
            counter_fee_source=trusted_counter_fee_source,
            cap_source=trusted_output_cap_source,
            tariff_source=trusted_generation_tariff_source,
            transport_source=trusted_transport_semantics_source,
        ) if trusted_budget_source is None else None
        active_budget_source = trusted_budget_source if trusted_budget_source is not None else QualifiedInvocationBudgetProducer()
        model_invocation_service = ModelInvocationService(
            selection_resolver=model_selection_resolver,
            gateway=active_provider_service.binding,
            receipt_store=receipt_store,
            trusted_budget_source=active_budget_source,
            trusted_budget_bundle_source=active_budget_bundle,
        )
    if active_production_release is not None and model_invocation_service is None:
        raise RuntimeError("production model invocation service is unavailable")
    active_transmission_approval = (
        TransmissionApprovalService(active_provider_service)
        if active_provider_service is not None
        else None
    )
    # ``model_runtime`` is retained only for the legacy development
    # composition. A production release must never instantiate its provider
    # registry/router or use its gateway as a second execution authority.
    model_runtime = (
        None
        if active_production_release is not None
        else create_model_runtime(active_settings)
    )
    optimization_v26_router = _load_optional_optimization_router()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if active_production_release is not None:
            active_production_release.start()
        default_attachment_repository = (
            ProcessLocalAttachmentBindingRepository()
            if isinstance(workspace_service, _UnsetService)
            else None
        )
        active_manifest_store = None
        try:
            manifest_type = getattr(
                importlib.import_module("embedded_copilot.knowledge.manifest"),
                "SQLiteKnowledgeManifestStore",
            )
            active_manifest_store = manifest_type(
                active_settings.desktop_state_database
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            logger.warning(
                "knowledge_manifest_unavailable",
                extra={"event_name": "knowledge_manifest_unavailable", "error_type": type(error).__name__},
            )
        active_v63_memory_governance = v63_memory_governance
        if active_v63_memory_governance is None and active_settings.project_root is not None:
            try:
                state_database = Path(active_settings.desktop_state_database)
                v63_database = versioned_database_path(state_database, 63)
                active_v63_memory_governance = V63MemoryGovernanceService(
                    SQLiteV63Store(v63_database)
                )
            except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as error:
                # Fail closed: a production composition failure never becomes
                # an InMemory/Fake store.
                logger.warning(
                    "v63_memory_governance_unavailable",
                    extra={"event_name": "v63_memory_governance_unavailable", "error_type": type(error).__name__},
                )
                active_v63_memory_governance = None
        active_v64_validation_proposal = engineering_validation_proposal_service
        if (
            active_v64_validation_proposal is not None
            and active_settings.environment.lower() in {"production", "prod"}
            and not isinstance(getattr(active_v64_validation_proposal, "_store", None), SQLiteV64Store)
        ):
            # Production composition must never accept Fake/InMemory persistence.
            logger.warning(
                "v64_validation_proposal_rejected_non_production_store",
                extra={"event_name": "v64_validation_proposal_rejected_non_production_store"},
            )
            active_v64_validation_proposal = None
        if active_v64_validation_proposal is None and active_v63_memory_governance is not None and active_settings.project_root is not None:
            try:
                state_database = Path(active_settings.desktop_state_database)
                v64_database = versioned_database_path(state_database, 64)
                v63_store = getattr(active_v63_memory_governance, "store", None)
                if v63_store is None:
                    raise RuntimeError("v6.3 store is unavailable")
                active_v64_validation_proposal = ValidationProposalService(
                    evidence=V63EvidenceSource(v63_store),
                    context=V63ApprovedContextSource(v63_store),
                    rules=DeterministicValidationRules(),
                    store=SQLiteV64Store(v64_database),
                )
            except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as error:
                logger.warning(
                    "v64_validation_proposal_unavailable",
                    extra={"event_name": "v64_validation_proposal_unavailable", "error_type": type(error).__name__},
                )
                active_v64_validation_proposal = None
        active_artifact_store = None
        if isinstance(execution_operation_port, ExecutionOperationService):
            active_artifact_store = execution_operation_port.artifact_store
        elif execution_operation_port is None:
            try:
                active_artifact_store = ArtifactStore(
                    Path(active_settings.desktop_state_database).with_name("v66-artifacts")
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.warning(
                    "v66_artifact_store_unavailable",
                    extra={"event_name": "v66_artifact_store_unavailable"},
                )
        active_execution_port = execution_operation_port
        if active_execution_port is None:
            try:
                state_database = Path(active_settings.desktop_state_database)
                execution_database = state_database.with_name(f"{state_database.stem}-v66.sqlite3")
                active_execution_port = ExecutionOperationService(
                    store=SQLiteExecutionStore(execution_database),
                    artifact_store=active_artifact_store,
                )
            except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as error:
                logger.warning(
                    "v66_execution_store_unavailable",
                    extra={"event_name": "v66_execution_store_unavailable", "error_type": type(error).__name__},
                )
        active_firmware_build_execution_bridge = (
            FirmwareBuildExecutionBridge(active_execution_port)
            if isinstance(active_execution_port, ExecutionOperationService)
            else None
        )
        active_fabrication_store = None
        active_fabrication_service = None
        try:
            fabrication_root = Path(active_settings.desktop_state_database).with_name("v1-fabrication")
            active_fabrication_store = FabricationStore(fabrication_root)
            active_fabrication_service = FabricationReadinessService(store=active_fabrication_store)
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning(
                "fabrication_store_unavailable",
                extra={"event_name": "fabrication_store_unavailable"},
            )
        active_hardware_component_source = hardware_component_source
        if (
            active_hardware_component_source is None
            and callable(getattr(component_recommendation_port, "search", None))
        ):
            active_hardware_component_source = component_recommendation_port
        if active_hardware_component_source is None:
            try:
                active_hardware_component_source = load_approved_component_source()
            except (OSError, TypeError, ValueError) as error:
                logger.warning(
                    "approved_component_source_unavailable",
                    extra={
                        "event_name": "approved_component_source_unavailable",
                        "error_type": type(error).__name__,
                    },
                )
        active_project_catalog = project_catalog_runtime
        if active_project_catalog is None and (
            active_settings.project_root is not None
            or active_settings.environment.lower() in {"production", "prod"}
        ):
            try:
                active_project_catalog = ProjectCatalogRuntime(
                    project_root=active_settings.project_root,
                    state_database=active_settings.desktop_state_database,
                    metadata_directory=active_settings.desktop_project_metadata_directory,
                    location_label=active_settings.project_location_label,
                    location_allow_list=active_settings.project_location_allow_list,
                    production_mode=active_settings.environment == "production",
                    approved_memory_store=approved_memory_store,
                    knowledge_manifest_store=active_manifest_store,
                    v63_memory_governance=active_v63_memory_governance,
                    hardware_component_source=active_hardware_component_source,
                )
            except (ProjectCatalogUnavailable, OSError, ValueError, sqlite3.Error) as error:
                logger.warning(
                    "project_catalog_unavailable",
                    extra={
                        "event_name": "project_catalog_unavailable",
                        "error_type": type(error).__name__,
                    },
                )
                active_project_catalog = None
        if active_project_catalog is not None and active_hardware_component_source is not None:
            set_source = getattr(active_project_catalog, "set_hardware_component_source", None)
            if callable(set_source):
                set_source(active_hardware_component_source)
        active_source_publication_host = None
        if (isinstance(active_project_catalog, ProjectCatalogRuntime) and project_catalog_runtime is None
                and hardware_component_source is None and component_recommendation_port is None):
            from embedded_copilot.project_catalog.source_publication_host import CatalogSourcePublicationHost
            active_source_publication_host = CatalogSourcePublicationHost(active_project_catalog)
            active_project_catalog.set_source_publication_host(active_source_publication_host)
        active_schematic_parameter_port = schematic_parameter_port
        if isinstance(active_project_catalog, ProjectCatalogRuntime):
            if active_schematic_parameter_port is None:
                active_schematic_parameter_port = ProjectMappedParameterPort(
                    root_resolver=lambda tenant_id, project_id: active_project_catalog.knowledge_snapshot_root(
                        project_id, tenant_id=tenant_id,
                    ),
                )
            active_project_catalog.set_schematic_evidence_context(
                execution=active_execution_port, parameter_port=active_schematic_parameter_port,
            )
            if isinstance(active_execution_port, ExecutionOperationService):
                active_execution_port.set_pcb_write_authorizer(
                    active_project_catalog.generic_pcb_write_target_allowed,
                )
        active_workspace_apply_port = workspace_apply_port
        workspace_service_runtime = getattr(active_workspace_apply_port, "_service", None)
        if (
            active_workspace_apply_port is not None
            and active_settings.environment.lower() in {"production", "prod"}
            and not isinstance(active_workspace_apply_port, ProjectCatalogWorkspaceApplyPort)
        ):
            logger.warning(
                "v65_workspace_apply_rejected_non_production_binding",
                extra={"event_name": "v65_workspace_apply_rejected_non_production_binding"},
            )
            active_workspace_apply_port = None
        if (
            active_workspace_apply_port is None
            and isinstance(active_project_catalog, ProjectCatalogRuntime)
            and active_settings.project_root is not None
        ):
            try:
                workspace_database = Path(active_settings.desktop_state_database).with_name(
                    f"{Path(active_settings.desktop_state_database).stem}-v65.sqlite3"
                )
                workspace_state_root = Path(active_settings.desktop_state_database).parent / "v65-workspace"
                workspace_service_runtime = WorkspaceApplyService(
                    resolver=ProjectCatalogWorkspaceTargetResolver(active_project_catalog),
                    store=SQLiteWorkspaceApplyStore(workspace_database),
                    state_root=workspace_state_root,
                    clock=SystemWorkspaceClock(),
                )
                if active_v64_validation_proposal is not None:
                    active_workspace_apply_port = ProjectCatalogWorkspaceApplyPort(
                        service=workspace_service_runtime,
                        proposals=active_v64_validation_proposal,
                    )
            except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as error:
                logger.warning(
                    "v65_workspace_apply_unavailable",
                    extra={"event_name": "v65_workspace_apply_unavailable", "error_type": type(error).__name__},
                )
                active_workspace_apply_port = None
        if (
            isinstance(active_project_catalog, ProjectCatalogRuntime)
            and isinstance(workspace_service_runtime, WorkspaceApplyService)
        ):
            active_project_catalog.set_workspace_snapshot_source(workspace_service_runtime)
        active_native_move_setup = None
        if native_move_setup is not None:
            if (
                not isinstance(native_move_setup, NativeMoveProductionSetup)
                or not isinstance(active_project_catalog, ProjectCatalogRuntime)
                or not isinstance(workspace_service_runtime, WorkspaceApplyService)
                or not isinstance(active_execution_port, ExecutionOperationService)
                or native_move_setup.workspace is not workspace_service_runtime
                or native_move_setup.artifact_store is not active_execution_port.artifact_store
                or not native_move_setup.available()
            ):
                raise ValueError("native move production composition is unavailable")
            from embedded_copilot.execution_worker.native_move_pcbnew_reader import (
                NativePcbnewReader,
            )
            from embedded_copilot.execution_worker.native_move_projection import (
                READER_ID,
            )

            if native_move_setup.reader_policy.reader_id == READER_ID:
                reader = native_move_setup.reader
                if (
                    type(reader) is not NativePcbnewReader
                    or reader.store is not active_execution_port.store
                    or reader.catalog is not active_project_catalog
                    or reader.workspace is not workspace_service_runtime
                    or reader.artifact_store is not active_execution_port.artifact_store
                    or reader.authorize_phase is not native_move_setup.authorize_phase
                    or not reader.ready_for(native_move_setup.reader_policy)
                ):
                    raise ValueError("native reader canonical composition is unavailable")
            from embedded_copilot.execution_worker.native_move_drc_collector import (
                NativeMoveDrcCollector,
            )

            drc = native_move_setup.drc
            if isinstance(drc, NativeMoveDrcCollector):
                if (
                    type(drc) is not NativeMoveDrcCollector
                    or drc.store is not active_execution_port.store
                    or drc.catalog is not active_project_catalog
                    or drc.workspace is not workspace_service_runtime
                    or drc.artifact_store is not active_execution_port.artifact_store
                    or drc.authorize_phase is not native_move_setup.authorize_phase
                    or not drc.ready_for(native_move_setup.drc_policy)
                ):
                    raise ValueError("native DRC canonical composition is unavailable")
            active_project_catalog.set_native_move_production_setup(native_move_setup)
            active_execution_port.set_native_move_authorizer(
                active_project_catalog.revalidate_native_move_operation,
            )
            active_native_move_setup = native_move_setup
        active_drc_baseline_collector = None
        if drc_baseline_collector is not None:
            from embedded_copilot.execution_worker.drc_baseline import PcbDrcBaselineCollector
            if (not isinstance(drc_baseline_collector, PcbDrcBaselineCollector)
                or not isinstance(active_project_catalog, ProjectCatalogRuntime)
                or not isinstance(active_execution_port, ExecutionOperationService)
                or drc_baseline_collector.workspace is not workspace_service_runtime
                or active_execution_port.artifact_store is None or not drc_baseline_collector.available()):
                raise ValueError("DRC baseline canonical composition unavailable")
            active_project_catalog.set_drc_baseline_collector(drc_baseline_collector)
            active_execution_port.set_drc_baseline_revalidator(active_project_catalog.revalidate_drc_baseline_operation)
            active_drc_baseline_collector = drc_baseline_collector
        active_project_knowledge_client = project_knowledge_chroma_client
        if (
            active_project_knowledge_client is None
            and isinstance(active_project_catalog, ProjectCatalogRuntime)
        ):
            try:
                active_project_knowledge_client = chromadb.PersistentClient(
                    path=str(active_settings.chroma_path),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            except Exception as error:
                logger.warning(
                    "project_knowledge_store_unavailable",
                    extra={"event_name": "project_knowledge_store_unavailable", "error_type": type(error).__name__},
                )
        active_embedding_provider: EmbeddingProvider | None = None
        active_embedding_status = "unavailable"
        active_embedding_reason_code = "EMBEDDING_PROVIDER_UNAVAILABLE"
        active_embedding_provider_configured = active_settings.embedding_provider
        active_embedding_runtime_status = "not_checked"
        active_embedding_model_status = "not_checked"
        if active_settings.embedding_provider == "ollama":
            active_embedding_runtime_status = "unavailable"
            active_embedding_model_status = "unavailable"
            if not active_settings.embedding_model:
                active_embedding_reason_code = "MODEL_UNAVAILABLE"
        if (
            active_settings.embedding_provider == "ollama"
            and active_settings.embedding_model
        ):
            try:
                ollama_embedding = OllamaEmbeddingAdapter(
                    model=active_settings.embedding_model,
                    base_url=active_settings.ollama_base_url,
                    timeout_seconds=active_settings.request_timeout_seconds,
                )
                ollama_embedding.probe()
                active_embedding_provider = ollama_embedding
                active_embedding_status = "available"
                active_embedding_runtime_status = "available"
                active_embedding_model_status = "available"
            except EmbeddingModelUnavailable as error:
                active_embedding_status = "unavailable"
                active_embedding_reason_code = "MODEL_UNAVAILABLE"
                active_embedding_runtime_status = "available"
                active_embedding_model_status = "unavailable"
                logger.warning(
                    "knowledge_embedding_model_unavailable",
                    extra={"event_name": "knowledge_embedding_model_unavailable", "error_type": type(error).__name__},
                )
            except (EmbeddingProviderError, ImportError, OSError, RuntimeError, ValueError) as error:
                active_embedding_status = "unavailable"
                active_embedding_reason_code = (
                    "MODEL_UNAVAILABLE"
                    if not active_settings.embedding_model
                    else "EMBEDDING_RUNTIME_UNAVAILABLE"
                )
                logger.warning(
                    "knowledge_embedding_unavailable",
                    extra={"event_name": "knowledge_embedding_unavailable", "error_type": type(error).__name__},
                )
        elif (
            active_settings.embedding_provider == "openai_compatible"
            and active_settings.embedding_model
            and active_settings.openai_api_key
        ):
            try:
                from langchain_openai import OpenAIEmbeddings

                active_embedding_provider = OpenAIEmbeddings(
                    model=active_settings.embedding_model,
                    api_key=active_settings.openai_api_key,
                    base_url=(
                        str(active_settings.openai_base_url)
                        if active_settings.openai_base_url
                        else None
                    ),
                )
                active_embedding_status = "available"
                active_embedding_reason_code = "CONFIGURED"
                active_embedding_runtime_status = "available"
                active_embedding_model_status = "available"
            except (ImportError, OSError, RuntimeError, ValueError) as error:
                active_embedding_status = "unavailable"
                active_embedding_runtime_status = "unavailable"
                active_embedding_model_status = "unavailable"
                logger.warning(
                    "knowledge_embedding_unavailable",
                    extra={"event_name": "knowledge_embedding_unavailable", "error_type": type(error).__name__},
                )
        active_knowledge_search_port = copilot_knowledge_search_port
        if (
            active_knowledge_search_port is None
            and isinstance(active_project_catalog, ProjectCatalogRuntime)
            and active_project_knowledge_client is not None
            and active_embedding_provider is not None
            and active_manifest_store is not None
        ):
            active_knowledge_search_port = ProjectCatalogKnowledgeSearchPort(
                project_catalog=active_project_catalog,
                chroma_client=active_project_knowledge_client,
                embedding=active_embedding_provider,
                manifest_store=active_manifest_store,
                embedding_provider_name=active_settings.embedding_provider,
                embedding_model=active_settings.embedding_model or "configured",
                embedding_identity=str(
                    getattr(
                        active_embedding_provider,
                        "identity",
                        f"{active_settings.embedding_provider}|{active_settings.embedding_model or 'configured'}",
                    )
                ),
                chunk_size=active_settings.chunk_size,
                chunk_overlap=active_settings.chunk_overlap,
                top_k=active_settings.retrieval_top_k,
                score_threshold=active_settings.retrieval_score_threshold,
            )
        active_chat_runtime = desktop_copilot_chat_runtime
        if (
            active_chat_runtime is None
            and isinstance(active_project_catalog, ProjectCatalogRuntime)
            and default_attachment_repository is not None
        ):
            active_chat_runtime = build_project_first_chat_runtime(
                database=active_settings.desktop_state_database,
                project_catalog=active_project_catalog,
                approved_memory_store=approved_memory_store,
                v63_memory_governance=active_v63_memory_governance,
                environment=copilot_environment_runtime,
                attachments=default_attachment_repository,
                knowledge_search_port=active_knowledge_search_port,
                provider_environment=(
                    {}
                    if active_production_release is not None
                    else None
                ),
                # Production chat is governed exclusively by the canonical
                # invocation service. The provider binding remains a
                # compatibility input for non-production callers only.
                provider_binding=None,
                provider_policy=None,
                model_invocation_service=model_invocation_service,
                governed=active_production_release is not None,
                transmission_approval=active_transmission_approval,
                execution_port=active_execution_port,
                firmware_build_execution_bridge=active_firmware_build_execution_bridge,
            )
        if isinstance(active_project_catalog, ProjectCatalogRuntime):
            bind_manifest = getattr(active_project_catalog, "set_knowledge_manifest_store", None)
            if callable(bind_manifest):
                bind_manifest(active_manifest_store)
            bind_v63 = getattr(active_project_catalog, "set_v63_memory_governance", None)
            if callable(bind_v63):
                bind_v63(active_v63_memory_governance)
            active_project_catalog.set_chat_runtime(active_chat_runtime)
            bind_conversations = getattr(active_chat_runtime, "set_conversation_port", None)
            if callable(bind_conversations):
                bind_conversations(active_project_catalog)
            active_project_catalog.start()
        if isinstance(vision_port, _UnsetService):
            active_vision_port: VisionPort | None = (
                _LazyVisionPort(
                    lambda: create_vision_runtime(
                        active_settings,
                        default_attachment_repository,
                    ).vision_port()
                )
                if default_attachment_repository is not None
                else None
            )
        else:
            active_vision_port = vision_port
        default_file_runtime = None
        if default_attachment_repository is not None and (
            isinstance(file_port, _UnsetService)
            or isinstance(datasheet_port, _UnsetService)
        ):
            default_file_runtime = create_file_runtime(
                active_settings,
                CopilotFileReferenceCatalog(
                    default_attachment_repository,
                    file_reference_paths or {},
                ),
            )
        if isinstance(file_port, _UnsetService):
            active_file_port: FileIntelligencePort | None = (
                default_file_runtime.file_port()
                if default_file_runtime is not None
                else None
            )
        else:
            active_file_port = file_port
        if isinstance(datasheet_port, _UnsetService):
            active_datasheet_port: DatasheetIntelligencePort | None = (
                create_datasheet_runtime(
                    default_file_runtime.extraction_port()
                ).datasheet_port()
                if default_file_runtime is not None
                else None
            )
        else:
            active_datasheet_port = datasheet_port
        if isinstance(context_port, _UnsetService):
            active_context_port: EngineeringContextPort | None = (
                create_engineering_context_runtime(
                    file_port=CopilotFileContextSource(active_file_port),
                    datasheet_port=CopilotDatasheetContextSource(active_datasheet_port),
                    vision_port=CopilotVisionContextSource(active_vision_port),
                    reference_resolver=CopilotContextReferenceResolver(
                        default_attachment_repository
                    ),
                ).context_port()
                if default_attachment_repository is not None
                and active_file_port is not None
                and active_datasheet_port is not None
                and active_vision_port is not None
                else None
            )
        else:
            active_context_port = context_port
        if isinstance(reasoning_port, _UnsetService):
            if active_context_port is None:
                active_reasoning_port = None
            elif active_production_release is not None:
                # The deterministic reasoning port is a read-only analysis
                # projection. The legacy conversation contract does not
                # carry the project/stage/identity tuple required to build a
                # governed model invocation safely, so keep this path model
                # free rather than inventing those fields.
                active_reasoning_port = create_reasoning_runtime().reasoning_port()
            elif model_runtime is not None:
                active_reasoning_port = _LazyReasoningPort(
                    lambda: model_runtime.enhance_reasoning_port(
                        create_reasoning_runtime().reasoning_port()
                    )
                )
            else:
                active_reasoning_port = None
        else:
            active_reasoning_port = reasoning_port
        active_reasoning_service = None
        if (
            active_context_port is not None
            and active_reasoning_port is not None
            and isinstance(active_reasoning_port, ReasoningPort)
        ):
            active_reasoning_service = ContextBackedReasoningService(
                active_context_port,
                active_reasoning_port,
            )
        default_experience_runtime = None
        if (
            isinstance(workspace_service, _UnsetService)
            and active_production_release is None
        ):
            if default_attachment_repository is None:
                raise RuntimeError("attachment repository composition failed")
            default_experience_runtime = build_experience_runtime(
                reasoning=model_runtime.reasoning_port(),
                context_port=(
                    active_context_port
                    if active_context_port is not None
                    else UnavailableEngineeringContextPort()
                ),
                attachment_repository=default_attachment_repository,
            )
            active_workspace_service: WorkspaceService | None = (
                default_experience_runtime.workspace_service
            )
        else:
            active_workspace_service = workspace_service
        if isinstance(experience_service, _UnsetService):
            active_experience_service: ExperienceService | None = (
                default_experience_runtime.experience_service
                if default_experience_runtime is not None
                else None
            )
        else:
            active_experience_service = experience_service
        application.state.settings = active_settings
        application.state.desktop_identity_provider = (
            desktop_identity_provider
            if desktop_identity_provider is not None
            else (
                ProductionLocalSessionIdentityAdapter(active_production_release.sessions)
                if active_production_release is not None
                else None
            )
        )
        application.state.production_release_runtime = active_production_release
        application.state.desktop_update_service = (
            DesktopUpdateService.for_runtime(active_production_release)
            if active_production_release is not None else None
        )
        application.state.provider_configuration_service = active_provider_service
        application.state.transmission_approval_service = active_transmission_approval
        # Status is a read-only projection. In production it must not probe a
        # provider through the legacy runtime or create a second model path.
        application.state.model_status_port = (
            model_runtime.status_port()
            if model_runtime is not None
            else UnavailableStatusPort()
        )
        application.state.model_gateway_management_service = model_gateway_management
        application.state.model_invocation_service = model_invocation_service
        application.state.model_consumption_service = model_consumption_service
        application.state.model_selection_resolver = model_selection_resolver
        application.state.vision_port = active_vision_port
        application.state.file_port = active_file_port
        application.state.datasheet_port = active_datasheet_port
        application.state.context_port = active_context_port
        application.state.reasoning_port = active_reasoning_port
        application.state.reasoning_service = active_reasoning_service
        application.state.workspace_service = active_workspace_service
        application.state.experience_service = active_experience_service
        if service is not None:
            application.state.copilot_service = service
            application.state.health_status = "ok"
        elif active_settings.runtime_mode == "llm":
            # Legacy chat has no Model Gateway invocation binding. Never
            # construct its direct provider runtime in LLM mode.
            application.state.copilot_service = None
            application.state.health_status = "ok"
            application.state.ingestion_errors = ()
        else:
            try:
                runtime = await asyncio.to_thread(
                    build_canonical_runtime,
                    active_settings,
                    allow_unavailable_embedding=(
                        active_settings.embedding_provider == "ollama"
                    ),
                )
                application.state.copilot_service = runtime.service
                application.state.health_status = runtime.health_status
                application.state.ingestion_errors = runtime.ingestion_errors
            except Exception as error:
                # Runtime initialization failures are an unavailable
                # capability, never a hidden Fake/InMemory fallback.
                logger.warning(
                    "canonical_runtime_unavailable",
                    extra={"event_name": "canonical_runtime_unavailable", "error_type": type(error).__name__},
                )
                application.state.copilot_service = None
                application.state.health_status = "unavailable"
                application.state.ingestion_errors = ("canonical_runtime_unavailable",)
        active_analysis = analysis_service or build_legacy_runtime(active_settings)
        active_memory_service: MemoryServicePort | None = None
        if isinstance(memory_port, MemoryServicePort):
            active_memory_service = memory_port
        elif (
            memory_port is not None
            and memory_automation_port is not None
            and memory_writer is not None
        ):
            try:
                active_memory_service = MemoryApplicationService(
                    candidates=memory_port,
                    promotion=memory_automation_port,
                    writer=memory_writer,
                )
            except TypeError:
                active_memory_service = None
        application.state.analysis_service = active_analysis
        application.state.memory_service = active_memory_service
        application.state.memory_port = memory_port
        application.state.memory_writer = memory_writer
        application.state.intelligence_port = intelligence_port
        application.state.intelligence_context_port = intelligence_context_port
        application.state.reasoning_input_resolver = reasoning_input_resolver
        application.state.autonomous_loop_port = autonomous_loop_port
        application.state.generation_port = generation_port
        application.state.workspace_snapshot_port = workspace_snapshot_port
        application.state.toolchain_snapshot_port = toolchain_snapshot_port
        application.state.component_recommendation_port = component_recommendation_port
        application.state.source_publication_host = active_source_publication_host
        application.state.hardware_component_source = active_hardware_component_source
        application.state.device_snapshot_port = device_snapshot_port
        application.state.flash_port = flash_port
        application.state.observation_snapshot_port = observation_snapshot_port
        application.state.validation_loop_port = validation_loop_port
        application.state.loop_coordinator_port = loop_coordinator_port
        application.state.approval_gate_port = approval_gate_port
        application.state.loop_state_port = loop_state_port
        application.state.memory_automation_port = memory_automation_port
        application.state.tool_adapter_status_port = tool_adapter_status_port
        application.state.tool_adapter_build_port = tool_adapter_build_port
        application.state.tool_adapter_flash_port = tool_adapter_flash_port
        application.state.tool_adapter_device_port = tool_adapter_device_port
        application.state.hardware_design_port = hardware_design_port
        application.state.hardware_review_port = hardware_review_port
        application.state.debug_analysis_port = debug_analysis_port
        application.state.optimization_port = optimization_port
        application.state.optimization_approval_port = optimization_approval_port
        application.state.firmware_engineering_port = firmware_engineering_port
        application.state.firmware_build_port = firmware_build_port
        application.state.firmware_debug_port = firmware_debug_port
        application.state.hil_validation_port = hil_validation_port
        application.state.device_observation_port = device_observation_port
        application.state.hardware_capability_port = hardware_capability_port
        application.state.digital_twin_port = digital_twin_port
        application.state.optimization_analysis_port = optimization_analysis_port
        application.state.knowledge_port = knowledge_port
        application.state.retrieval_port = retrieval_port
        application.state.engineering_completion_port = engineering_completion_port
        application.state.desktop_product_runtime = desktop_product_runtime
        application.state.copilot_environment_runtime = copilot_environment_runtime
        application.state.desktop_copilot_chat_runtime = active_chat_runtime
        application.state.project_catalog_runtime = active_project_catalog
        application.state.copilot_knowledge_search_port = active_knowledge_search_port
        application.state.copilot_firmware_analysis_port = copilot_firmware_analysis_port
        application.state.copilot_debug_analysis_port = copilot_debug_analysis_port
        application.state.copilot_review_port = copilot_review_port
        application.state.copilot_operation_port = copilot_operation_port
        application.state.execution_operation_port = active_execution_port
        application.state.firmware_build_execution_bridge = active_firmware_build_execution_bridge
        application.state.workspace_apply_port = active_workspace_apply_port
        application.state.native_move_production_available = active_native_move_setup is not None
        application.state.drc_baseline_available = active_drc_baseline_collector is not None
        application.state.drc_baseline_revalidator = active_project_catalog.revalidate_drc_baseline_operation if active_drc_baseline_collector is not None else None
        application.state.fabrication_store = active_fabrication_store
        application.state.fabrication_readiness_service = active_fabrication_service
        application.state.fabrication_pipeline = None
        firmware_source_worker = None
        firmware_source_worker_task = None
        if (
            isinstance(active_execution_port, ExecutionOperationService)
            and isinstance(workspace_service_runtime, WorkspaceApplyService)
        ):
            firmware_bridge_type = getattr(
                importlib.import_module("embedded_copilot.firmware_source_execution"),
                "FirmwareSourceExecutionBridge",
            )
            application.state.firmware_source_execution_bridge = firmware_bridge_type(
                workspace=workspace_service_runtime,
                execution=active_execution_port,
            )
            application.state.firmware_build_execution_bridge = (
                active_firmware_build_execution_bridge
            )
            kicad_resolver = None
            symbol_binding_validator = UnavailableSchematicSymbolLibraryValidator()
            if isinstance(active_project_catalog, ProjectCatalogRuntime):
                try:
                    kicad_resolver = ProjectCatalogWorkspaceTargetResolver(active_project_catalog)
                    symbol_binding_validator = SchematicSymbolLibraryValidator(
                        lambda *, tenant_id, project_id, target_id: kicad_resolver.resolve(
                            tenant_id=tenant_id,
                            project_id=project_id,
                            target_id=target_id,
                        )
                    )
                except (TypeError, ValueError, OSError):
                    logger.warning(
                        "kicad_symbol_library_validator_unavailable",
                        extra={"event_name": "kicad_symbol_library_validator_unavailable"},
            )
            application.state.schematic_execution_bridge = SchematicExecutionBridge(
                workspace=workspace_service_runtime,
                execution=active_execution_port,
                symbol_binding_validator=symbol_binding_validator,
                private_definition_validator=(active_project_catalog._validate_private_schematic_definitions
                    if isinstance(active_project_catalog, ProjectCatalogRuntime) else None),
            )
            active_schematic_parse_adapter = schematic_parse_adapter
            active_schematic_readback_adapter = schematic_readback_adapter
            schematic_erc_adapter = schematic_erc_port or UnavailableSchematicErcAdapter()
            pcb_drc_adapter = None
            pcb_parse_adapter = None
            kicad_cli_path = None if any(port is not None for port in (schematic_parse_adapter, schematic_readback_adapter, schematic_erc_port)) else discover_kicad_cli(
                configured_path=(
                    None
                    if active_settings.kicad_cli_path is None
                    else str(active_settings.kicad_cli_path)
                ),
                known_install_roots=active_settings.kicad_install_roots,
            )
            kicad_cli = None if kicad_cli_path is None else str(kicad_cli_path)
            kicad_version: str | None = None
            footprint_root = None
            symbol_root = None
            configured_symbol_dir = os.environ.get("KICAD_SYMBOL_DIR", "").strip() or None
            manufacturing_adapter = None
            if isinstance(active_project_catalog, ProjectCatalogRuntime) and kicad_cli:
                footprint_root = discover_kicad_footprint_root(
                    kicad_cli,
                    configured_root=os.environ.get("EMBRIX_KICAD_FOOTPRINT_ROOT", "").strip() or None,
                )
                symbol_root = discover_kicad_symbol_root(
                    kicad_cli,
                    configured_root=configured_symbol_dir,
                )
                if footprint_root is not None:
                    try:
                        active_project_catalog.set_pcb_production_service(
                            PCBProductionService(footprints=KiCadFootprintAvailability(footprint_root))
                        )
                    except (TypeError, ValueError, OSError):
                        logger.warning(
                            "pcb_production_service_unavailable",
                            extra={"event_name": "pcb_production_service_unavailable"},
                        )
            if kicad_cli and kicad_resolver is not None:
                try:
                    kicad_version = probe_kicad_cli_version(kicad_cli, cwd=Path.cwd())
                    schematic_erc_adapter = KiCadCliSchematicErcAdapter(
                        executable=kicad_cli,
                        workspace_resolver=lambda operation: kicad_resolver.resolve(
                            tenant_id=operation.tenant_id,
                            project_id=operation.project_id,
                            target_id=operation.target_id,
                        ).root,
                        kicad_version=kicad_version,
                        library_variables={
                            "KICAD_SYMBOL_DIR": str(symbol_root),
                        }
                        if symbol_root is not None
                        else None,
                    )
                    if active_schematic_parse_adapter is None or active_schematic_readback_adapter is None:
                        xml_exporter = KiCadCliSchematicXmlExporter(
                            executable=kicad_cli, tool_version=kicad_version,
                        )
                        xml_adapter = SchematicXmlAdapter(
                            read_input=active_project_catalog.read_schematic_operation_input,
                            exporter=xml_exporter,
                            tool_executable_sha256=xml_exporter.executable_sha256,
                            tool_version=xml_exporter.tool_version,
                            library_observer=active_project_catalog.collect_schematic_library_observation,
                        )
                        if active_schematic_parse_adapter is None:
                            active_schematic_parse_adapter = xml_adapter
                        if active_schematic_readback_adapter is None:
                            active_schematic_readback_adapter = xml_adapter
                    drc_command = discover_kicad_pcb_drc_command(kicad_cli, cwd=Path.cwd())
                    parse_command = discover_kicad_pcb_parse_command(kicad_cli, cwd=Path.cwd())
                    if drc_command is not None:
                        pcb_drc_adapter = KiCadCliPCBDrCAdapter(
                            executable=kicad_cli,
                            workspace_resolver=lambda operation: kicad_resolver.resolve(
                                tenant_id=operation.tenant_id,
                                project_id=operation.project_id,
                                target_id=operation.target_id,
                            ).root,
                            command_builder=drc_command,
                            kicad_version=kicad_version,
                        )
                    if parse_command is not None:
                        pcb_parse_adapter = KiCadCliPCBParseAdapter(
                            executable=kicad_cli,
                            workspace_resolver=lambda operation: kicad_resolver.resolve(
                                tenant_id=operation.tenant_id,
                                project_id=operation.project_id,
                                target_id=operation.target_id,
                            ).root,
                            command_builder=parse_command,
                            kicad_version=kicad_version,
                        )
                except (TypeError, ValueError, OSError):
                    logger.warning(
                        "kicad_erc_adapter_unavailable",
                        extra={"event_name": "kicad_erc_adapter_unavailable"},
                    )
            application.state.pcb_execution_bridge = PCBExecutionBridge(
                workspace=workspace_service_runtime,
                execution=active_execution_port,
                footprint_root=footprint_root,
            )
            build_registry = None
            build_workspace_resolver = None
            build_targets = None
            build_artifact_store = active_artifact_store
            if active_native_move_setup is not None:
                build_allowed_operations_native = True
            else:
                build_allowed_operations_native = False
            build_allowed_operations = [
                OperationType.FIRMWARE_SOURCE_WRITE,
                OperationType.KICAD_SCHEMATIC_WRITE,
                OperationType.KICAD_SCHEMATIC_ERC,
                OperationType.KICAD_SCHEMATIC_PARSE,
                OperationType.KICAD_SCHEMATIC_READ_BACK,
                OperationType.KICAD_PCB_WRITE,
                OperationType.KICAD_PCB_DRC,
            ]
            if active_drc_baseline_collector is not None:
                build_allowed_operations.append(OperationType.KICAD_PCB_DRC_BASELINE)
            if build_allowed_operations_native:
                build_allowed_operations.append(OperationType.KICAD_NATIVE_FOOTPRINT_MOVE)
            if isinstance(active_project_catalog, ProjectCatalogRuntime):
                try:
                    if build_artifact_store is None:
                        raise RuntimeError("artifact store unavailable")
                    build_registry = ExecutionToolRegistry(
                        TrustedToolDiscovery(
                            trusted_roots=(Path(kicad_cli).parent,) if kicad_cli and kicad_version else (),
                            explicit_paths=(
                                {
                                    "KICAD_GERBER_EXPORT": Path(kicad_cli),
                                    "KICAD_DRILL_EXPORT": Path(kicad_cli),
                                    "KICAD_POSITION_EXPORT": Path(kicad_cli),
                                }
                                if kicad_cli and kicad_version
                                else {}
                            ),
                            allow_environment_paths=True,
                            version_probe=probe_trusted_tool_version,
                            trusted_versions={
                                "ESP_IDF_BUILD": "v5.3",
                                "CMAKE_NINJA_BUILD": "3",
                                "ARM_NONE_EABI_GCC": "15.3.1",
                                "KICAD_GERBER_EXPORT": kicad_version or "",
                                "KICAD_DRILL_EXPORT": kicad_version or "",
                                "KICAD_POSITION_EXPORT": kicad_version or "",
                            },
                        )
                    )
                    build_workspace_resolver = _ExecutionWorkerWorkspaceResolver(
                        workspace_service_runtime,
                        ProjectCatalogWorkspaceTargetResolver(active_project_catalog),
                    )
                    build_targets = SQLiteTargetRegistry(active_execution_port.store)
                    build_allowed_operations.append(OperationType.BUILD)
                    if kicad_cli and kicad_version == "10.0.5":
                        manufacturing_adapter = KiCadManufacturingAdapter()
                        build_allowed_operations.extend(
                            (
                                OperationType.KICAD_GERBER_EXPORT,
                                OperationType.KICAD_DRILL_EXPORT,
                                OperationType.KICAD_POSITION_EXPORT,
                                OperationType.MANUFACTURING_PACKAGE_BUILD,
                            )
                        )
                    application.state.controlled_build_status = (
                        "AVAILABLE"
                        if any(
                            build_registry.resolve(
                                item.tool_id,
                                target_kind=item.supported_targets[0],
                                operation=OperationType.BUILD,
                            ).status == "AVAILABLE"
                            for item in (
                                build_registry.definition("ESP_IDF_BUILD"),
                                build_registry.definition("CMAKE_NINJA_BUILD"),
                                build_registry.definition("ARM_NONE_EABI_GCC"),
                            )
                        )
                        else "UNAVAILABLE"
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    build_registry = None
                    build_workspace_resolver = None
                    build_targets = None
                    build_artifact_store = active_artifact_store
                    application.state.controlled_build_status = "UNAVAILABLE"
            else:
                application.state.controlled_build_status = "UNAVAILABLE"
            application.state.firmware_build_context_source = (
                FirmwareBuildContextSource(
                    catalog=active_project_catalog,
                    workspace_apply=workspace_service_runtime,
                    execution=active_execution_port,
                    targets=build_targets,
                    tools=build_registry,
                )
                if build_targets is not None and build_registry is not None
                else None
            )
            firmware_source_worker = ExecutionWorker(
                worker_id="firmware-source-worker",
                store=active_execution_port.store,
                registry=build_registry,
                workspace_resolver=build_workspace_resolver,
                targets=build_targets,
                artifact_store=build_artifact_store,
                build=FirmwareBuildToolAdapter(),
                firmware_source_write=FirmwareSourceWriteAdapter(workspace_service_runtime),
                schematic_write=SchematicWriteAdapter(workspace_service_runtime),
                schematic_erc=schematic_erc_adapter,
                schematic_parse=active_schematic_parse_adapter,
                schematic_readback=active_schematic_readback_adapter,
                pcb_write=PCBWriteAdapter(workspace_service_runtime),
                recovery_workspace=workspace_service_runtime,
                native_move_adapter=(
                    NativeMoveProductionAdapter(active_native_move_setup, clock=lambda: datetime.now(UTC))
                    if active_native_move_setup is not None else None
                ),
                native_move_revalidator=(
                    active_project_catalog.revalidate_native_move_operation
                    if active_native_move_setup is not None else None
                ),
                drc_baseline_collector=active_drc_baseline_collector,
                drc_baseline_revalidator=active_project_catalog.revalidate_drc_baseline_operation if active_drc_baseline_collector is not None else None,
                pcb_drc=pcb_drc_adapter,
                manufacturing=manufacturing_adapter,
                allowed_operation_types=tuple(build_allowed_operations),
                build_context_revalidator=(
                    application.state.firmware_build_context_source.revalidate_operation
                    if application.state.firmware_build_context_source is not None
                    else None
                ),
                design_context_revalidator=(
                    active_project_catalog.revalidate_circuit_bound_operation
                    if isinstance(active_project_catalog, ProjectCatalogRuntime)
                    else lambda operation: False
                ),
                library_observation_authorizer=(
                    active_project_catalog.revalidate_library_observation_payload
                    if isinstance(active_project_catalog, ProjectCatalogRuntime) else None
                ),
                build_sandbox_root=Path(active_settings.desktop_state_database).with_name(
                    "v66-build-operations"
                ),
            )
        else:
            application.state.firmware_source_execution_bridge = None
            application.state.firmware_build_execution_bridge = None
            application.state.schematic_execution_bridge = None
            application.state.pcb_execution_bridge = None
            application.state.firmware_build_context_source = None
        if (
            active_fabrication_service is not None
            and isinstance(active_execution_port, ExecutionOperationService)
            and firmware_source_worker is not None
            and manufacturing_adapter is not None
        ):
            application.state.fabrication_pipeline = ManufacturingPipeline(
                operation_service=active_execution_port,
                worker=firmware_source_worker,
                readiness=active_fabrication_service,
                artifact_store=build_artifact_store,
                entry_gate=CanonicalManufacturingEntryGate(active_project_catalog),
            )
        application.state.v63_memory_governance = active_v63_memory_governance
        application.state.engineering_validation_proposal_service = active_v64_validation_proposal
        application.state.desktop_workspace_projection = _build_desktop_workspace_projection(
            project_catalog=active_project_catalog,
            memory=active_v63_memory_governance,
            validation=active_v64_validation_proposal,
            workspace=active_workspace_apply_port,
            execution=active_execution_port,
            capabilities=lambda project_id: _desktop_capability_values(application, project_id),
            audit=active_workspace_apply_port,
        )
        if (
            application.state.copilot_firmware_analysis_port is None
            and isinstance(active_project_catalog, ProjectCatalogRuntime)
        ):
            application.state.copilot_firmware_analysis_port = ProjectFirmwareAnalysisPort(
                project_catalog=active_project_catalog
            )
        _record_runtime_component_status(
            application,
            project_catalog=active_project_catalog,
            chat_runtime=active_chat_runtime,
            approved_memory_store=approved_memory_store,
            v63_memory_governance=active_v63_memory_governance,
            embedding_provider=active_embedding_provider,
            knowledge_search_port=active_knowledge_search_port,
            embedding_status=active_embedding_status,
            embedding_reason_code=active_embedding_reason_code,
            embedding_provider_configured=active_embedding_provider_configured,
            embedding_runtime_status=active_embedding_runtime_status,
            embedding_model_status=active_embedding_model_status,
            provider_service=active_provider_service,
        )
        application.state.multimodal_vision_port = (
            vision_port if not isinstance(vision_port, _UnsetService) else None
        )
        application.state.multimodal_reasoning_port = (
            reasoning_port if not isinstance(reasoning_port, _UnsetService) else None
        )
        application.state.reasoning_layer_service = (
            ReasoningService(reasoning_layer_port)
            if reasoning_layer_port is not None
            else None
        )
        await active_analysis.start()
        if firmware_source_worker is not None:
            await asyncio.to_thread(firmware_source_worker.recover_after_restart)
            if isinstance(active_project_catalog, ProjectCatalogRuntime):
                await asyncio.to_thread(active_project_catalog.reconcile_schematic_terminal_receipts)
            async def run_firmware_source_worker() -> None:
                while True:
                    status = await asyncio.to_thread(firmware_source_worker.run_once)
                    if status is None:
                        await asyncio.sleep(0.2)
                        continue
                    if (
                        status.status in {OperationStatus.SUCCEEDED, OperationStatus.FAILED}
                        and isinstance(active_project_catalog, ProjectCatalogRuntime)
                    ):
                        operation = active_execution_port.get_operation(
                            tenant_id=status.tenant_id,
                            project_id=status.project_id,
                            operation_id=status.operation_id,
                        )
                        if operation is None:
                            continue
                        if operation.operation_type in {OperationType.KICAD_SCHEMATIC_PARSE, OperationType.KICAD_SCHEMATIC_READ_BACK}:
                            await asyncio.to_thread(active_project_catalog.reconcile_schematic_terminal_receipts)
                            continue
                        if operation.operation_type is OperationType.BUILD:
                            try:
                                receipt = active_execution_port.get_receipt(
                                    tenant_id=status.tenant_id,
                                    project_id=status.project_id,
                                    operation_id=status.operation_id,
                                )
                                if receipt is not None:
                                    active_project_catalog.record_firmware_build(
                                        operation=operation,
                                        receipt=receipt,
                                    )
                            except (AttributeError, OSError, RuntimeError, ValueError, ProjectCatalogError):
                                logger.warning(
                                    "firmware_build_receipt_registration_unavailable",
                                    extra={"event_name": "firmware_build_receipt_registration_unavailable"},
                                )
                            continue
                        if operation.operation_type is OperationType.KICAD_SCHEMATIC_ERC:
                            if status.receipt_fingerprint:
                                try:
                                    receipt = active_execution_port.get_receipt(
                                        tenant_id=status.tenant_id,
                                        project_id=status.project_id,
                                        operation_id=status.operation_id,
                                    )
                                    if receipt is not None:
                                        active_project_catalog.record_schematic_erc_validation(
                                            operation=operation,
                                            receipt=receipt,
                                        )
                                except (AttributeError, OSError, RuntimeError, ValueError, ProjectCatalogError):
                                    logger.warning(
                                        "schematic_erc_receipt_registration_unavailable",
                                        extra={"event_name": "schematic_erc_receipt_registration_unavailable"},
                                    )
                            continue
                        if status.status is not OperationStatus.SUCCEEDED:
                            continue
                        binding = getattr(operation, "authorized_execution", None)
                        conversation_id = getattr(binding, "conversation_id", None) or getattr(operation, "conversation_id", None)
                        if conversation_id is None:
                            raise ValueError("operation conversation binding unavailable")
                        if operation.operation_type in {
                            OperationType.FIRMWARE_SOURCE_WRITE,
                            OperationType.KICAD_SCHEMATIC_WRITE,
                        }:
                            try:
                                active_project_catalog.scan_artifact_intelligence(
                                    status.project_id,
                                    conversation_id,
                                    tenant_id=status.tenant_id,
                                    requester=operation.requester_id,
                                    source_operation_id=(
                                        status.operation_id
                                        if operation.operation_type is OperationType.KICAD_SCHEMATIC_WRITE
                                        and status.receipt_fingerprint
                                        else None
                                    ),
                                    source_proposal_fingerprint=(
                                        operation.proposal_fingerprint
                                        if operation.operation_type is OperationType.KICAD_SCHEMATIC_WRITE
                                        and status.receipt_fingerprint
                                        else None
                                    ),
                                )
                            except (AttributeError, OSError, RuntimeError, ValueError, ProjectCatalogError):
                                logger.warning(
                                    "firmware_source_artifact_registration_unavailable",
                                    extra={"event_name": "firmware_source_artifact_registration_unavailable"},
                                )
                        if operation.operation_type is OperationType.KICAD_SCHEMATIC_WRITE and status.receipt_fingerprint:
                            try:
                                receipt = active_execution_port.get_receipt(
                                    tenant_id=status.tenant_id,
                                    project_id=status.project_id,
                                    operation_id=status.operation_id,
                                )
                                if receipt is not None:
                                    active_project_catalog.record_schematic_generation(
                                        operation=operation,
                                        receipt=receipt,
                                        external_validation_status="UNAVAILABLE",
                                    )
                                    active_project_catalog.create_schematic_evidence_operation(operation.project_id, operation.conversation_id,
                                        tenant_id=operation.tenant_id, requester=operation.requester_id, kind="PARSE")
                            except (AttributeError, OSError, RuntimeError, ValueError, ProjectCatalogError):
                                logger.warning(
                                    "schematic_generation_receipt_unavailable",
                                    extra={"event_name": "schematic_generation_receipt_unavailable"},
                                )
                        if operation.operation_type is OperationType.KICAD_PCB_WRITE and status.receipt_fingerprint:
                            receipt = active_execution_port.get_receipt(
                                tenant_id=status.tenant_id,
                                project_id=status.project_id,
                                operation_id=status.operation_id,
                            )
                            if receipt is None or pcb_parse_adapter is None:
                                logger.warning(
                                    "pcb_completion_unavailable",
                                    extra={"event_name": "pcb_completion_unavailable"},
                                )
                                continue
                            try:
                                active_project_catalog.record_pcb_generation(
                                    operation=operation,
                                    receipt=receipt,
                                )
                                parse_evidence = await asyncio.to_thread(
                                    pcb_parse_adapter.execute,
                                    operation,
                                )
                                active_project_catalog.record_pcb_parse_validation(
                                    evidence=parse_evidence,
                                )
                                if parse_evidence.validation_status.value != "PARSE_VERIFIED":
                                    continue
                                bridge = getattr(application.state, "pcb_execution_bridge", None)
                                enqueue_drc = getattr(bridge, "enqueue_drc", None)
                                if not callable(enqueue_drc):
                                    logger.warning(
                                        "pcb_drc_enqueue_unavailable",
                                        extra={"event_name": "pcb_drc_enqueue_unavailable"},
                                    )
                                    continue
                                enqueue_drc(
                                    write_operation=operation,
                                    receipt_fingerprint=receipt.fingerprint,
                                    reviewer_id=operation.requester_id,
                                    parse_evidence=parse_evidence,
                                )
                            except (AttributeError, OSError, RuntimeError, ValueError, ProjectCatalogError):
                                logger.warning(
                                    "pcb_completion_failed_closed",
                                    extra={"event_name": "pcb_completion_failed_closed"},
                                )
                            continue
                        if operation.operation_type is OperationType.KICAD_PCB_DRC and status.receipt_fingerprint:
                            receipt = active_execution_port.get_receipt(
                                tenant_id=status.tenant_id,
                                project_id=status.project_id,
                                operation_id=status.operation_id,
                            )
                            if receipt is None:
                                logger.warning(
                                    "pcb_drc_receipt_unavailable",
                                    extra={"event_name": "pcb_drc_receipt_unavailable"},
                                )
                                continue
                            try:
                                active_project_catalog.record_pcb_drc_validation(
                                    operation=operation,
                                    receipt=receipt,
                                )
                            except (AttributeError, OSError, RuntimeError, ValueError, ProjectCatalogError):
                                logger.warning(
                                    "pcb_drc_completion_failed_closed",
                                    extra={"event_name": "pcb_drc_completion_failed_closed"},
                                )
                            continue

            firmware_source_worker_task = asyncio.create_task(run_firmware_source_worker())
        try:
            yield
        finally:
            if firmware_source_worker_task is not None:
                firmware_source_worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await firmware_source_worker_task
            await active_analysis.close()
            if isinstance(active_project_catalog, ProjectCatalogRuntime):
                active_project_catalog.close()

    application = FastAPI(
        title=active_settings.app_name,
        version=active_settings.version,
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def trace_middleware(request: Request, call_next: Any):
        request.state.trace_id = str(uuid4())
        response = await call_next(request)
        response.headers["x-trace-id"] = request.state.trace_id
        return response

    application.add_middleware(ConfigurationDispatchDiagnosticMiddleware)
    application.add_middleware(TestConnectionDispatchDiagnosticMiddleware)
    application.add_middleware(DesktopSessionCookieMiddleware)
    application.add_middleware(WebViewSessionDiagnosticMiddleware)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        if request.url.path.endswith("/context"):
            return JSONResponse(
                status_code=422,
                content={
                    "error": "context_unavailable",
                    "trace_id": request.state.trace_id,
                },
            )
        if request.url.path.endswith("/reasoning"):
            return JSONResponse(
                status_code=422,
                content={
                    "error": "reasoning_unavailable",
                    "trace_id": request.state.trace_id,
                },
            )
        if request.url.path.endswith("/reasoning/query"):
            return JSONResponse(
                status_code=422,
                content={"error": "REASONING_REQUEST_REJECTED"},
            )
        if request.url.path.endswith("/intelligence/query"):
            return JSONResponse(
                status_code=422,
                content={"error": "INTELLIGENCE_QUERY_REJECTED"},
            )
        if "/api/optimization/v23/" in request.url.path:
            return JSONResponse(
                status_code=422,
                content={"error": "PROPOSAL_REJECTED"},
            )
        if "/api/firmware/v24/" in request.url.path:
            return JSONResponse(
                status_code=422,
                content={"error": "FIRMWARE_REJECTED"},
            )
        if "/api/hil/v25/" in request.url.path:
            return JSONResponse(
                status_code=422,
                content={"error": "HIL_REJECTED"},
            )
        if "/api/digital-twin/v26/" in request.url.path or "/api/optimization/v26/" in request.url.path:
            return JSONResponse(status_code=422, content={"error": "OPTIMIZATION_REJECTED"})
        if "/api/knowledge/v27/" in request.url.path:
            return JSONResponse(status_code=422, content={"error": "QUERY_REJECTED"})
        if "/api/engineering/v28/" in request.url.path:
            return JSONResponse(status_code=422, content={"error": "QUERY_REJECTED"})
        if "/api/multimodal/v29/" in request.url.path:
            return JSONResponse(status_code=422, content={"error": "QUERY_REJECTED"})
        if request.url.path.endswith("/datasheets/analyze"):
            return JSONResponse(
                status_code=422,
                content={
                    "error": "datasheet_unavailable",
                    "trace_id": request.state.trace_id,
                },
            )
        if request.url.path.startswith("/api/v1/copilot/"):
            return JSONResponse(
                status_code=422,
                content={
                    "detail": "Request validation failed.",
                    "trace_id": request.state.trace_id,
                },
            )
        return _error_response(
            trace_id=request.state.trace_id,
            code=ErrorCode.VALIDATION_ERROR,
            message="Request validation failed.",
            status_code=422,
        )

    @application.exception_handler(Exception)
    async def internal_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "error_occurred",
            extra={
                "event_name": "error_occurred",
                "trace_id": request.state.trace_id,
                "error_category": "internal_error",
            },
        )
        return _error_response(
            trace_id=request.state.trace_id,
            code=ErrorCode.INTERNAL_ERROR,
            message="Internal server error.",
            status_code=500,
        )

    _include_execution_composition(application, optimization_v26_router)
    _include_legacy_composition(application)
    _include_desktop_composition(application)
    if active_production_release is not None:
        static_assets = active_settings.desktop_static_assets
        if static_assets is None or not static_assets.is_dir():
            raise RuntimeError("production desktop static assets are unavailable")
        application.mount("/desktop", StaticFiles(directory=str(static_assets), html=True), name="desktop")
    return application


app = create_app()
