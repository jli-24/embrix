from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity
from embedded_copilot.engineering_eda.production_capability import EDAValidationEvidence

from .copilot_capability_models import CapabilityStatus, CapabilityStatusView


router = APIRouter(prefix="/api/v1/copilot", tags=["copilot-capabilities"])


def _identity(identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> TrustedDesktopIdentity:
    return identity


def _status(
    capability: str,
    status: CapabilityStatus,
    reason_code: str,
    detail: str,
    provider_configured: str | None = None,
    runtime_status: str | None = None,
    model_status: str | None = None,
) -> CapabilityStatusView:
    return CapabilityStatusView(
        capability=capability,
        status=status,
        reason_code=reason_code,
        detail=detail,
        provider_configured=provider_configured,
        runtime_status=runtime_status,
        model_status=model_status,
    )


@router.get("/capabilities", response_model=tuple[CapabilityStatusView, ...])
def capabilities(
    request: Request,
    identity: TrustedDesktopIdentity = Depends(_identity),
) -> tuple[CapabilityStatusView, ...]:
    """Report the current read-only Desktop capability boundary.

    Identity is intentionally resolved even though the projection has no
    tenant-specific payload today. This prevents an unauthenticated caller
    from using capability state as a substitute for the Desktop trust boundary.
    """

    del identity
    component_statuses = getattr(
        request.app.state,
        "runtime_capability_statuses",
        getattr(request.app.state, "runtime_component_statuses", {}),
    )
    project_catalog = component_statuses.get("project_catalog")
    model_gateway = component_statuses.get("model_gateway")
    provider_configuration = component_statuses.get("provider_configuration", "unavailable")
    chat_model = component_statuses.get("chat_model", "unavailable")
    engineering_analysis_completion = component_statuses.get(
        "engineering_analysis_completion", "unavailable"
    )
    provider_projection = getattr(request.app.state, "provider_projection", None)
    provider_failure_code = (
        "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE"
        if getattr(provider_projection, "credential_status", None) == "UNAVAILABLE"
        else str(
            getattr(provider_projection, "connection_status", None).value
            if getattr(provider_projection, "connection_status", None) is not None
            else "PROVIDER_CONNECTION_UNVERIFIED"
        )
    )
    provider_configuration_reason = (
        "CONFIGURED"
        if provider_configuration == "available"
        else "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE"
        if getattr(provider_projection, "credential_status", None) == "UNAVAILABLE"
        else "PROVIDER_NOT_CONFIGURED"
    )
    embedding_provider = component_statuses.get("embedding_provider")
    embedding_reason_code = component_statuses.get(
        "embedding_reason_code", "EMBEDDING_PROVIDER_UNAVAILABLE"
    )
    embedding_provider_configured = component_statuses.get(
        "embedding_provider_configured"
    )
    embedding_runtime_status = component_statuses.get("embedding_runtime_status")
    embedding_model_status = component_statuses.get("embedding_model_status")
    knowledge_search_status = component_statuses.get("knowledge_search")
    chat_runtime = getattr(request.app.state, "desktop_copilot_chat_runtime", None)
    v63_ready = component_statuses.get("v63_memory_governance") == "available"
    v64_ready = getattr(request.app.state, "engineering_validation_proposal_service", None) is not None
    knowledge_search_port = getattr(
        request.app.state, "copilot_knowledge_search_port", None
    )
    workspace_apply_port = getattr(request.app.state, "workspace_apply_port", None)
    execution_port = getattr(request.app.state, "execution_operation_port", None)
    worker_health = getattr(request.app.state, "execution_worker_health", "unavailable")
    execution_runtime_ready = execution_port is not None and worker_health == "available"
    if worker_health == "available":
        worker_capability = CapabilityStatus.AVAILABLE
        worker_reason = "WORKER_HEALTHY"
        worker_detail = "The independent Local Execution Worker is healthy."
    elif execution_port is not None:
        worker_capability = CapabilityStatus.UNVERIFIED
        worker_reason = "CODE_RUNTIME_VERIFIED"
        worker_detail = "Offline/runtime software tests verify the Worker boundary; live Worker health is not active."
    else:
        worker_capability = CapabilityStatus.UNAVAILABLE
        worker_reason = "EXECUTION_STORE_UNAVAILABLE"
        worker_detail = "The v6.6 SQLite Operation Service is not composed."

    domain_ports = {
        "DEBUG_INTELLIGENCE": getattr(request.app.state, "copilot_debug_analysis_port", None),
        "HARDWARE_INTELLIGENCE": getattr(request.app.state, "copilot_hardware_intelligence_port", None),
        "PCB_INTELLIGENCE": getattr(request.app.state, "copilot_pcb_intelligence_port", None),
        "REQUIREMENT_INTELLIGENCE": getattr(request.app.state, "copilot_requirement_intelligence_port", None),
        "ARCHITECTURE_INTELLIGENCE": getattr(request.app.state, "copilot_architecture_intelligence_port", None),
        "MULTIMODAL_INTELLIGENCE": getattr(request.app.state, "copilot_multimodal_intelligence_port", None),
    }

    if project_catalog == "available":
        project_status = CapabilityStatus.AVAILABLE
        project_reason = "CONFIGURED"
        project_detail = "Project Catalog is configured for the controlled project root."
    else:
        project_status = CapabilityStatus.UNAVAILABLE
        project_reason = "PROJECT_CATALOG_UNAVAILABLE"
        project_detail = "A controlled project root and catalog store are required."

    if chat_runtime is not None and engineering_analysis_completion == "available":
        analysis_status = CapabilityStatus.AVAILABLE
        analysis_reason = "CONFIGURED"
        analysis_detail = "The analysis composition passed the provider connection gate; each external request still requires a single-use approval receipt."
    elif chat_runtime is not None:
        analysis_status = CapabilityStatus.DEGRADED
        analysis_reason = "MODEL_GATEWAY_UNAVAILABLE"
        analysis_detail = "The chat projection exists, but no verified model provider is configured."
    else:
        analysis_status = CapabilityStatus.UNAVAILABLE
        analysis_reason = "CHAT_RUNTIME_UNAVAILABLE"
        analysis_detail = "Read-only chat requires a verified application runtime."

    knowledge_search_available = (
        knowledge_search_port is not None
        and not (
            embedding_provider_configured == "ollama"
            and embedding_provider != "available"
        )
    )

    workspace_status = CapabilityStatus.AVAILABLE if workspace_apply_port is not None else CapabilityStatus.UNAVAILABLE
    workspace_reason = "CONFIGURED" if workspace_apply_port is not None else "WORKSPACE_APPLY_UNAVAILABLE"
    workspace_detail = "Controlled Workspace Apply is bound to a validated application service." if workspace_apply_port is not None else "Controlled Workspace Apply requires the production SQLite-backed composition."
    raw_eda_evidence = getattr(request.app.state, "eda_validation_evidence", None)
    eda_evidence = raw_eda_evidence if isinstance(raw_eda_evidence, EDAValidationEvidence) and raw_eda_evidence.is_kicad10 and raw_eda_evidence.drc_available and raw_eda_evidence.erc_available else None
    eda_status = CapabilityStatus.AVAILABLE if eda_evidence is not None else CapabilityStatus.UNAVAILABLE
    eda_reason = "REAL_KICAD_VALIDATION_EVIDENCE" if eda_evidence is not None else "KICAD_RUNTIME_UNAVAILABLE"
    eda_detail = "Real KiCad 10 validation evidence is bound to this capability." if eda_evidence is not None else "No verified KiCad 10 DRC/ERC runtime is available; structured EDA changes are not executable."

    return (
        _status("PROJECT_CATALOG", project_status, project_reason, project_detail),
        _status("PROJECT_CONTEXT", project_status, project_reason, project_detail),
        _status(
            "CHAT_ANALYSIS",
            analysis_status,
            analysis_reason,
            analysis_detail,
            provider_configured=(
                str(getattr(provider_projection, "credential_status", None))
                if provider_projection is not None
                else None
            ),
            runtime_status="available" if chat_runtime is not None else "unavailable",
            model_status=(
                "available"
                if engineering_analysis_completion == "available"
                else "unavailable"
            ),
        ),
        _status(
            "KNOWLEDGE_ANALYSIS",
            CapabilityStatus.UNVERIFIED if knowledge_search_available else CapabilityStatus.UNAVAILABLE,
            "NOT_GOLDEN_VERIFIED" if knowledge_search_available else "KNOWLEDGE_SEARCH_UNAVAILABLE",
            "Project-bound retrieval is configured but has no real Golden Evidence yet."
            if knowledge_search_available
            else "A project-bound Knowledge Search Port is required.",
        ),
        _status("WORKSPACE_CHANGESET", workspace_status, workspace_reason, workspace_detail),
        _status("WORKSPACE_PREVIEW", workspace_status, workspace_reason, workspace_detail),
        _status("WORKSPACE_APPLY", workspace_status, workspace_reason, workspace_detail),
        _status("WORKSPACE_RECOVERY", workspace_status, workspace_reason, workspace_detail),
        _status("EDA_CHANGESET", CapabilityStatus.DEGRADED if workspace_apply_port is not None else CapabilityStatus.UNAVAILABLE, "STRUCTURED_ONLY" if workspace_apply_port is not None else "EDA_CHANGESET_UNAVAILABLE", "Structured EDA ChangeSets are descriptive until a verified KiCad runtime is available."),
        _status("EDA_DRC", eda_status, eda_reason, eda_detail),
        _status("EDA_ERC", eda_status, eda_reason, eda_detail),
        _status("EDA_APPLY", eda_status if eda_evidence is not None else CapabilityStatus.UNAVAILABLE, eda_reason, eda_detail),
        _status(
            "KNOWLEDGE_EMBEDDING",
            CapabilityStatus.AVAILABLE
            if embedding_provider == "available"
            else CapabilityStatus.DEGRADED
            if embedding_provider == "degraded"
            else CapabilityStatus.UNAVAILABLE,
            "CONFIGURED"
            if embedding_provider == "available"
            else str(embedding_reason_code),
            "The configured embedding provider passed service, model, and vector probes."
            if embedding_provider == "available"
            else (
                "Ollama is configured but its local runtime is unavailable."
                if embedding_provider_configured == "ollama"
                and embedding_runtime_status == "unavailable"
                else "The configured embedding model is unavailable."
                if embedding_provider_configured == "ollama"
                and embedding_model_status == "unavailable"
                else "Knowledge embedding is unavailable until the configured provider and model pass probe."
            ),
            provider_configured=(
                str(embedding_provider_configured)
                if embedding_provider_configured is not None
                else None
            ),
            runtime_status=(
                str(embedding_runtime_status)
                if embedding_runtime_status is not None
                else None
            ),
            model_status=(
                str(embedding_model_status)
                if embedding_model_status is not None
                else None
            ),
        ),
        _status(
            "PROVIDER_CONFIGURATION",
            CapabilityStatus.AVAILABLE if provider_configuration == "available" else CapabilityStatus.UNAVAILABLE,
            provider_configuration_reason,
            "DeepSeek settings and the fixed production endpoint are available."
            if provider_configuration == "available"
            else "DeepSeek provider configuration is not complete.",
        ),
        _status(
            "CHAT_MODEL",
            CapabilityStatus.AVAILABLE if chat_model == "available" else CapabilityStatus.UNAVAILABLE,
            "CONFIGURED" if chat_model == "available" else provider_failure_code,
            "The final-answer chat provider passed the current configuration connection test."
            if chat_model == "available"
            else "The final-answer chat provider is not available under the current configuration.",
        ),
        _status(
            "ENGINEERING_ANALYSIS_COMPLETION",
            CapabilityStatus.AVAILABLE if engineering_analysis_completion == "available" else CapabilityStatus.UNAVAILABLE,
            "CONFIGURED" if engineering_analysis_completion == "available" else "PROVIDER_CONNECTION_UNVERIFIED",
            "Greenfield analysis is composed; external transmission still requires an independent per-request approval."
            if engineering_analysis_completion == "available"
            else "Greenfield analysis cannot complete until the provider configuration and connection gate are valid.",
        ),
        _status(
            "FIRMWARE_ANALYSIS",
            CapabilityStatus.UNVERIFIED
            if getattr(request.app.state, "copilot_firmware_analysis_port", None) is not None
            else CapabilityStatus.UNAVAILABLE,
            "NOT_GOLDEN_VERIFIED"
            if getattr(request.app.state, "copilot_firmware_analysis_port", None) is not None
            else "FIRMWARE_ANALYSIS_UNAVAILABLE",
            "Parser-backed source analysis is configured but has no real Golden Evidence yet."
            if getattr(request.app.state, "copilot_firmware_analysis_port", None) is not None
            else "A project-bound Firmware analysis Port is required.",
        ),
        _status("DEBUG_INTELLIGENCE", CapabilityStatus.UNVERIFIED if domain_ports["DEBUG_INTELLIGENCE"] is not None else CapabilityStatus.UNAVAILABLE, "NOT_GOLDEN_VERIFIED" if domain_ports["DEBUG_INTELLIGENCE"] is not None else "DEBUG_INTELLIGENCE_UNAVAILABLE", "Read-only Debug Intelligence is configured but Golden verification is pending." if domain_ports["DEBUG_INTELLIGENCE"] is not None else "Debug Intelligence provider is unavailable."),
        _status("HARDWARE_INTELLIGENCE", CapabilityStatus.UNVERIFIED if domain_ports["HARDWARE_INTELLIGENCE"] is not None else CapabilityStatus.UNAVAILABLE, "NOT_GOLDEN_VERIFIED" if domain_ports["HARDWARE_INTELLIGENCE"] is not None else "HARDWARE_INTELLIGENCE_UNAVAILABLE", "Hardware Intelligence is read-only and Golden verification is pending." if domain_ports["HARDWARE_INTELLIGENCE"] is not None else "Hardware Intelligence provider is unavailable."),
        _status("PCB_INTELLIGENCE", CapabilityStatus.UNVERIFIED if domain_ports["PCB_INTELLIGENCE"] is not None else CapabilityStatus.UNAVAILABLE, "NOT_GOLDEN_VERIFIED" if domain_ports["PCB_INTELLIGENCE"] is not None else "PCB_INTELLIGENCE_UNAVAILABLE", "PCB/KiCad Intelligence is read-only and Golden verification is pending." if domain_ports["PCB_INTELLIGENCE"] is not None else "PCB Intelligence provider is unavailable."),
        _status("REQUIREMENT_INTELLIGENCE", CapabilityStatus.UNVERIFIED if domain_ports["REQUIREMENT_INTELLIGENCE"] is not None else CapabilityStatus.UNAVAILABLE, "NOT_GOLDEN_VERIFIED" if domain_ports["REQUIREMENT_INTELLIGENCE"] is not None else "REQUIREMENT_INTELLIGENCE_UNAVAILABLE", "Requirement analysis is deterministic and Golden verification is pending." if domain_ports["REQUIREMENT_INTELLIGENCE"] is not None else "Requirement Intelligence provider is unavailable."),
        _status("ARCHITECTURE_INTELLIGENCE", CapabilityStatus.UNVERIFIED if domain_ports["ARCHITECTURE_INTELLIGENCE"] is not None else CapabilityStatus.UNAVAILABLE, "NOT_GOLDEN_VERIFIED" if domain_ports["ARCHITECTURE_INTELLIGENCE"] is not None else "ARCHITECTURE_INTELLIGENCE_UNAVAILABLE", "Architecture analysis is evidence-bound and Golden verification is pending." if domain_ports["ARCHITECTURE_INTELLIGENCE"] is not None else "Architecture Intelligence provider is unavailable."),
        _status("MULTIMODAL_INTELLIGENCE", CapabilityStatus.UNVERIFIED if domain_ports["MULTIMODAL_INTELLIGENCE"] is not None else CapabilityStatus.UNAVAILABLE, "NOT_GOLDEN_VERIFIED" if domain_ports["MULTIMODAL_INTELLIGENCE"] is not None else "VISION_PROVIDER_UNAVAILABLE", "Vision observation is configured but not automatically verified." if domain_ports["MULTIMODAL_INTELLIGENCE"] is not None else "Vision Provider is not configured."),
        _status(
            "MEMORY_READ",
            CapabilityStatus.UNVERIFIED if v63_ready else CapabilityStatus.UNAVAILABLE,
            "NOT_GOLDEN_VERIFIED" if v63_ready else "APPROVED_STORE_NOT_BOUND",
            "Only approved, project-bound memory may be projected into Desktop context."
            if v63_ready else "The v6.3 SQLite store is unavailable; no in-memory fallback is allowed.",
        ),
        _status(
            "ENGINEERING_MEMORY",
            CapabilityStatus.UNVERIFIED if v63_ready else CapabilityStatus.UNAVAILABLE,
            "NOT_GOLDEN_VERIFIED" if v63_ready else "V63_STORE_UNAVAILABLE",
            "Approved Engineering Memory is a read-only context source; Current Evidence remains required."
            if v63_ready else "The v6.3 SQLite store is unavailable.",
        ),
        _status(
            "KNOWLEDGE_GOVERNANCE",
            CapabilityStatus.UNVERIFIED if v63_ready else CapabilityStatus.UNAVAILABLE,
            "NOT_GOLDEN_VERIFIED" if v63_ready else "V63_STORE_UNAVAILABLE",
            "Governed Knowledge is projected read-only with tenant and project binding."
            if v63_ready else "The v6.3 SQLite store is unavailable.",
        ),
        _status(
            "KNOWLEDGE_EVOLUTION",
            CapabilityStatus.UNVERIFIED if v63_ready else CapabilityStatus.UNAVAILABLE,
            "SINK_UNAVAILABLE" if v63_ready else "V63_STORE_UNAVAILABLE",
            "Proposal, Review and Audit are available; physical Apply remains UNAVAILABLE/UNVERIFIED."
            if v63_ready else "The v6.3 SQLite store is unavailable.",
        ),
        _status("ENGINEERING_VALIDATION", CapabilityStatus.AVAILABLE if v64_ready else CapabilityStatus.UNAVAILABLE, "CONFIGURED" if v64_ready else "ENGINEERING_VALIDATION_UNAVAILABLE", "Evidence-bound deterministic validation is available." if v64_ready else "Production Validation composition is unavailable."),
        _status("ENGINEERING_FINDINGS", CapabilityStatus.AVAILABLE if v64_ready else CapabilityStatus.UNAVAILABLE, "CONFIGURED" if v64_ready else "ENGINEERING_FINDINGS_UNAVAILABLE", "Findings remain candidate engineering observations." if v64_ready else "Production Finding composition is unavailable."),
        _status("REPAIR_PROPOSAL", CapabilityStatus.AVAILABLE if v64_ready else CapabilityStatus.UNAVAILABLE, "CONFIGURED" if v64_ready else "REPAIR_PROPOSAL_UNAVAILABLE", "Repair proposals are reviewable descriptions and cannot be applied." if v64_ready else "Production Proposal composition is unavailable."),
        _status("OPTIMIZATION_PROPOSAL", CapabilityStatus.AVAILABLE if v64_ready else CapabilityStatus.UNAVAILABLE, "CONFIGURED" if v64_ready else "OPTIMIZATION_PROPOSAL_UNAVAILABLE", "Optimization proposals require evidence and review; execution is disabled." if v64_ready else "Production Proposal composition is unavailable."),
        _status("PROPOSAL_REVIEW", CapabilityStatus.AVAILABLE if v64_ready else CapabilityStatus.UNAVAILABLE, "CONFIGURED" if v64_ready else "PROPOSAL_REVIEW_UNAVAILABLE", "Proposal approval is a terminal governance state, not execution permission." if v64_ready else "Production Proposal Review composition is unavailable."),
        _status("EXECUTION_WORKER", worker_capability, worker_reason, worker_detail),
        _status("ARTIFACT_STORE", CapabilityStatus.UNVERIFIED if execution_port is not None else CapabilityStatus.UNAVAILABLE, "GOLDEN_PENDING" if execution_port is not None else "ARTIFACT_STORE_UNAVAILABLE", "Immutable ArtifactIdentity persistence is composed; external artifact Golden evidence is pending." if execution_port is not None else "Artifact store is unavailable."),
        _status("TARGET_IDENTITY", CapabilityStatus.UNVERIFIED if execution_runtime_ready else CapabilityStatus.UNAVAILABLE, "EXPLICIT_BINDING_REQUIRED" if execution_runtime_ready else "TARGET_REGISTRY_UNAVAILABLE", "Discovery does not grant trust; explicit project target binding is required." if execution_runtime_ready else "Target registry is not active."),
        _status("CONTROLLED_BUILD", CapabilityStatus.UNVERIFIED if execution_runtime_ready else CapabilityStatus.UNAVAILABLE, "TOOLCHAIN_GOLDEN_PENDING" if execution_runtime_ready else "WORKER_OR_TOOLCHAIN_UNAVAILABLE", "Typed Build Operation is available for review; real toolchain evidence is pending." if execution_runtime_ready else "Controlled Build is unavailable until the Worker and trusted toolchain are active."),
        _status("CONTROLLED_FLASH", CapabilityStatus.UNVERIFIED if execution_runtime_ready else CapabilityStatus.UNAVAILABLE, "HARDWARE_GOLDEN_PENDING" if execution_runtime_ready else "WORKER_OR_HARDWARE_UNAVAILABLE", "Flash remains artifact-, target- and approval-bound; real hardware evidence is pending." if execution_runtime_ready else "Controlled Flash is unavailable until the Worker and target are active."),
        _status("UART_OBSERVATION", CapabilityStatus.UNVERIFIED if execution_runtime_ready else CapabilityStatus.UNAVAILABLE, "UART_GOLDEN_PENDING" if execution_runtime_ready else "WORKER_OR_UART_UNAVAILABLE", "UART is bounded read-only observation; real device evidence is pending." if execution_runtime_ready else "UART Observation is unavailable until the Worker and UART adapter are active."),
        _status("HIL_EXECUTION", CapabilityStatus.UNVERIFIED if execution_runtime_ready else CapabilityStatus.UNAVAILABLE, "HIL_GOLDEN_PENDING" if execution_runtime_ready else "WORKER_OR_HIL_UNAVAILABLE", "HIL is declarative and approval-bound; real scenario evidence is pending." if execution_runtime_ready else "HIL is unavailable until the Worker and approved adapters are active."),
        _status("ESP32-S3_GOLDEN", CapabilityStatus.UNVERIFIED, "REAL_HARDWARE_EVIDENCE_REQUIRED", "Synthetic Worker tests cannot establish ESP32-S3 hardware Golden evidence."),
        _status("STM32F446RE_GOLDEN", CapabilityStatus.UNVERIFIED, "REAL_HARDWARE_EVIDENCE_REQUIRED", "Synthetic Worker tests cannot establish STM32F446RE hardware Golden evidence."),
        _status("WORKSPACE_WRITE", CapabilityStatus.UNAVAILABLE, "READ_ONLY_DESKTOP_BOUNDARY", "Desktop cannot modify Workspace or engineering files."),
        _status("BUILD", CapabilityStatus.UNAVAILABLE, "READ_ONLY_DESKTOP_BOUNDARY", "Build is not enabled in the current Desktop composition."),
        _status("FLASH", CapabilityStatus.UNAVAILABLE, "READ_ONLY_DESKTOP_BOUNDARY", "Flash is not enabled in the current Desktop composition."),
        _status("HIL", CapabilityStatus.UNAVAILABLE, "READ_ONLY_DESKTOP_BOUNDARY", "HIL and device control are not enabled in the current Desktop composition."),
        _status("EDA_APPLY", CapabilityStatus.UNAVAILABLE, "READ_ONLY_DESKTOP_BOUNDARY", "EDA changes cannot be applied from Desktop."),
        _status("REPAIR", CapabilityStatus.UNAVAILABLE, "READ_ONLY_DESKTOP_BOUNDARY", "Repair proposals cannot modify Workspace or devices."),
        _status("DEPLOY", CapabilityStatus.UNAVAILABLE, "READ_ONLY_DESKTOP_BOUNDARY", "Deploy is not enabled in the current Desktop composition."),
    )


__all__ = ("router",)
