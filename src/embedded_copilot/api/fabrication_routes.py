from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceTargetResolver,
)
from embedded_copilot.manufacturing import (
    ManufacturingEntryGateRejected,
    FabricationBaseline,
    FabricationReadinessService,
    FabricationRecord,
    FabricationStore,
    ManufacturingManifest,
    ManufacturingPipeline,
)
from embedded_copilot.project_catalog import ProjectCatalogError

from .fabrication_models import FabricationPackageRequest, FabricationReadinessRequest

router = APIRouter(prefix="/api/v1/copilot/fabrication", tags=["fabrication-readiness"])
_IDENTITY_DEPENDENCY = Depends(desktop_identity)


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


def _project_root(request: Request, identity: TrustedDesktopIdentity, project_id: str) -> Path | JSONResponse:
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    if catalog is None:
        return _error(request, 503, "FABRICATION_PROJECT_UNAVAILABLE")
    try:
        target = ProjectCatalogWorkspaceTargetResolver(catalog).resolve(
            tenant_id=identity.tenant_id,
            project_id=project_id,
            target_id="project-root",
        )
        return target.root
    except (AttributeError, OSError, PermissionError, ProjectCatalogError, RuntimeError, TypeError, ValueError):
        return _error(request, 404, "FABRICATION_PROJECT_NOT_FOUND")


def _path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve(strict=False)
    if not candidate.is_relative_to(root) or candidate.is_symlink():
        raise ValueError("fabrication source path is invalid")
    return candidate


def _service(request: Request) -> FabricationReadinessService | None:
    value = getattr(request.app.state, "fabrication_readiness_service", None)
    return value if isinstance(value, FabricationReadinessService) else None


def _store(request: Request) -> FabricationStore | None:
    value = getattr(request.app.state, "fabrication_store", None)
    return value if isinstance(value, FabricationStore) else None


def _pipeline(request: Request) -> ManufacturingPipeline | None:
    value = getattr(request.app.state, "fabrication_pipeline", None)
    return value if isinstance(value, ManufacturingPipeline) else None


@router.post("/readiness", response_model=FabricationRecord)
def evaluate_fabrication_readiness(
    body: FabricationReadinessRequest,
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
) -> FabricationRecord | JSONResponse:
    service = _service(request)
    root = _project_root(request, identity, body.project_id)
    if service is None:
        return _error(request, 503, "FABRICATION_READINESS_UNAVAILABLE")
    if isinstance(root, JSONResponse):
        return root
    try:
        return service.evaluate(
            source=body.source,
            pcb_path=_path(root, body.pcb_relative_path),
            bom_path=_path(root, body.bom_relative_path),
            schematic_path=_path(root, body.schematic_relative_path),
            elf_path=_path(root, body.elf_relative_path),
            requirement_context=body.requirement_context,
            requirement_specification=body.requirement_specification,
            engineering_decision=body.engineering_decision,
            design_truth=body.design_truth,
            pcb_artifact=body.pcb_artifact,
            bom_artifact=body.bom_artifact,
            existing_evidence=body.existing_evidence,
            explicit_mode=body.explicit_mode,
            allow_pcb_only_without_assembly=body.allow_pcb_only_without_assembly,
            mode_approval_ref=body.mode_approval_ref,
            mode_approved_by=body.mode_approved_by,
            mode_evidence_refs=body.mode_evidence_refs,
            specification=body.specification,
            parameter_decisions=body.parameter_decisions,
            upstream_checks=body.upstream_checks,
            upstream_gates=body.upstream_gates,
            persist=True,
        )
    except (OSError, TypeError, ValueError):
        return _error(request, 422, "FABRICATION_READINESS_REJECTED")


@router.post("/package", response_model=FabricationRecord, status_code=201)
def generate_manufacturing_package(
    body: FabricationPackageRequest,
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
) -> FabricationRecord | JSONResponse:
    pipeline = _pipeline(request)
    root = _project_root(request, identity, body.project_id)
    if pipeline is None:
        return _error(request, 503, "FABRICATION_PACKAGE_UNAVAILABLE")
    if isinstance(root, JSONResponse):
        return root
    try:
        result = pipeline.run(
            source=body.source,
            specification=body.specification,
            parameter_decisions=body.parameter_decisions,
            requirement_specification=body.requirement_specification,
            engineering_decision=body.engineering_decision,
            design_truth=body.design_truth,
            pcb_artifact=body.pcb_artifact,
            bom_artifact=body.bom_artifact,
            existing_evidence=body.existing_evidence,
            project_root=root,
            pcb_path=_path(root, body.pcb_relative_path),
            bom_path=_path(root, body.bom_relative_path),
            schematic_path=_path(root, body.schematic_relative_path),
            elf_path=_path(root, body.elf_relative_path),
            tenant_id=identity.tenant_id,
            requester_id=identity.requester,
            target_id=body.target_id,
            target_fingerprint=body.target_fingerprint,
            workspace_snapshot_id=body.workspace_snapshot_id,
            workspace_fingerprint=body.workspace_fingerprint,
            proposal_id=body.proposal_id,
            proposal_fingerprint=body.proposal_fingerprint,
            conversation_id=body.conversation_id,
            output_root_relative=body.output_root_relative,
            upstream_checks=body.upstream_checks,
            upstream_gates=body.upstream_gates,
        )
        return result.record
    except ManufacturingEntryGateRejected:
        return _error(request, 409, "G8_ENTRY_GATE_REJECTED")
    except (OSError, RuntimeError, TypeError, ValueError):
        return _error(request, 422, "FABRICATION_PACKAGE_REJECTED")


@router.get("/{result_id}", response_model=FabricationRecord)
def get_fabrication_readiness(
    result_id: str,
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
) -> FabricationRecord | JSONResponse:
    root = _project_root(request, identity, project_id)
    if isinstance(root, JSONResponse):
        return root
    del root
    store = _store(request)
    if store is None:
        return _error(request, 503, "FABRICATION_READINESS_UNAVAILABLE")
    try:
        record = store.get(project_id=project_id, result_id=result_id)
    except (OSError, TypeError, ValueError):
        return _error(request, 422, "FABRICATION_RECORD_REJECTED")
    return record if record is not None else _error(request, 404, "FABRICATION_RECORD_NOT_FOUND")


@router.get("/{result_id}/baseline", response_model=FabricationBaseline)
def get_fabrication_baseline(
    result_id: str,
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
):
    record = get_fabrication_readiness(result_id, project_id, request, identity)
    if isinstance(record, JSONResponse):
        return record
    return record.baseline if record.baseline is not None else _error(request, 404, "FABRICATION_BASELINE_NOT_FOUND")


@router.get("/{result_id}/manifest", response_model=ManufacturingManifest)
def get_fabrication_manifest(
    result_id: str,
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
):
    record = get_fabrication_readiness(result_id, project_id, request, identity)
    if isinstance(record, JSONResponse):
        return record
    return record.manifest if record.manifest is not None else _error(request, 404, "FABRICATION_MANIFEST_NOT_FOUND")


__all__ = ("router",)
