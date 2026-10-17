from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity
from embedded_copilot.engineering_validation_proposal import (
    ProposalConflict,
    ProposalRejected,
    ProposalUnavailable,
    ValidationProposalService,
    ValidationRequest,
)

from .copilot_validation_proposal_models import (
    OperationProposal,
    ProposalCreateBody,
    ValidationCreateBody,
    ValidationResult,
)

router = APIRouter(prefix="/api/v1/copilot", tags=["copilot-validation-proposal"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


def _service(request: Request) -> ValidationProposalService | None:
    value = getattr(request.app.state, "engineering_validation_proposal_service", None)
    return value if isinstance(value, ValidationProposalService) else None


@router.post("/validations", response_model=ValidationResult)
def create_validation(
    body: ValidationCreateBody,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> ValidationResult | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "ENGINEERING_VALIDATION_UNAVAILABLE")
    try:
        return service.validate(ValidationRequest.create(
            validation_id=body.validation_id,
            tenant_id=identity.tenant_id,
            project_id=body.project_id,
            requester_id=identity.requester,
            validation_type=body.validation_type,
            target_identity=body.target_identity,
            target_fingerprint=body.target_fingerprint,
            project_fingerprint=body.project_fingerprint,
            evidence_ids=body.evidence_ids,
            memory_ids=body.memory_ids,
            knowledge_ids=body.knowledge_ids,
            requested_at=body.requested_at,
        ))
    except ProposalRejected:
        return _error(request, 422, "ENGINEERING_VALIDATION_REJECTED")
    except ProposalUnavailable:
        return _error(request, 503, "ENGINEERING_VALIDATION_UNAVAILABLE")
    except ProposalConflict:
        return _error(request, 409, "ENGINEERING_VALIDATION_CONFLICT")


@router.get("/validations", response_model=tuple[ValidationResult, ...])
def list_validations(
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> tuple[ValidationResult, ...] | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "ENGINEERING_VALIDATION_UNAVAILABLE")
    try:
        return service.list_validations(identity.tenant_id, project_id)
    except ProposalRejected:
        return _error(request, 410, "PROJECT_DELETED")


@router.get("/validations/{validation_id}", response_model=ValidationResult)
def get_validation(
    validation_id: str,
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> ValidationResult | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "ENGINEERING_VALIDATION_UNAVAILABLE")
    try:
        value = service.get_validation(identity.tenant_id, project_id, validation_id)
        return value if value is not None else _error(request, 404, "VALIDATION_NOT_FOUND")
    except ProposalRejected:
        return _error(request, 410, "PROJECT_DELETED")


@router.post("/proposals", response_model=OperationProposal)
def create_proposal(
    body: ProposalCreateBody,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> OperationProposal | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "PROPOSAL_UNAVAILABLE")
    try:
        return service.create_proposal(
            tenant_id=identity.tenant_id,
            project_id=body.project_id,
            finding_id=body.finding_id,
            proposed_changes=body.proposed_changes or None,
            rationale=body.rationale,
        )
    except ProposalRejected:
        return _error(request, 422, "PROPOSAL_REJECTED")
    except ProposalUnavailable:
        return _error(request, 503, "PROPOSAL_UNAVAILABLE")
    except ProposalConflict:
        return _error(request, 409, "PROPOSAL_CONFLICT")


@router.get("/proposals", response_model=tuple[OperationProposal, ...])
def list_proposals(
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> tuple[OperationProposal, ...] | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "PROPOSAL_UNAVAILABLE")
    try:
        return service.list_proposals(identity.tenant_id, project_id)
    except ProposalRejected:
        return _error(request, 410, "PROJECT_DELETED")


@router.get("/proposals/{proposal_id}", response_model=OperationProposal)
def get_proposal(
    proposal_id: str,
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> OperationProposal | JSONResponse:
    service = _service(request)
    if service is None:
        return _error(request, 503, "PROPOSAL_UNAVAILABLE")
    try:
        value = service.get_proposal(identity.tenant_id, project_id, proposal_id)
        return value if value is not None else _error(request, 404, "PROPOSAL_NOT_FOUND")
    except ProposalRejected:
        return _error(request, 410, "PROJECT_DELETED")


__all__ = ("router",)
