from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity
from embedded_copilot.engineering_review import (
    HumanDecision,
    ReviewDecision,
    ReviewRequest,
    ReviewTargetReference,
    ReviewTargetType,
)
from embedded_copilot.v63_memory_governance import (
    V63MemoryGovernanceService,
    V63Error,
)
from embedded_copilot.v63_memory_governance.models import (
    ReviewStatus as V63ReviewStatus,
    ReviewTargetType as V63ReviewTargetType,
)
from embedded_copilot.engineering_validation_proposal import (
    ProposalConflict,
    ProposalRejected,
    ProposalUnavailable,
    ValidationProposalService,
)

from .copilot_review_models import (
    CopilotReviewCreateRequest,
    CopilotReviewDecision,
    CopilotReviewDecisionRequest,
    CopilotReviewTargetType,
    CopilotReviewView,
)


class TenantBoundReviewPort(Protocol):
    def create_review(self, *, tenant_id: str, requester: str, request: ReviewRequest) -> Any: ...
    def get_review(self, *, tenant_id: str, project_id: str, review_id: str) -> Any: ...
    def decide(self, *, tenant_id: str, decision: HumanDecision) -> Any: ...


router = APIRouter(prefix="/api/v1/copilot/reviews", tags=["copilot-reviews"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


def _view(value: object, *, project_id: str | None = None) -> CopilotReviewView:
    request = getattr(value, "review_request", value)
    if project_id is not None and request.project_id != project_id:
        raise ValueError("review project binding mismatch")
    decision = getattr(value, "decision", None)
    return CopilotReviewView(
        review_id=request.review_id,
        project_id=request.project_id,
        target_type=CopilotReviewTargetType(request.target_reference.target_type.value),
        target_id=request.target_reference.target_id,
        target_fingerprint=request.target_reference.target_fingerprint,
        requester=request.requester,
        status=getattr(getattr(value, "status", None), "value", "OPEN"),
        created_at=request.created_at,
        decision=None
        if decision is None
        else CopilotReviewDecision(decision.decision.value),
        reviewer=None if decision is None else decision.reviewer,
        decided_at=None if decision is None else decision.decided_at,
    )


def _v63_view(value: object, *, project_id: str | None = None) -> CopilotReviewView:
    if project_id is not None and value.project_id != project_id:
        raise ValueError("review project binding mismatch")
    return CopilotReviewView(
        review_id=value.review_id,
        project_id=value.project_id,
        target_type=CopilotReviewTargetType(value.target_type.value),
        target_id=value.target_id,
        target_fingerprint=value.target_fingerprint,
        requester=value.requester_id,
        status=value.status.value,
        created_at=value.created_at,
        decision=None if value.decision is None else CopilotReviewDecision(value.decision),
        reviewer=value.reviewer_id,
        decided_at=value.decided_at,
    )


def _v63_service(request: Request) -> V63MemoryGovernanceService | None:
    value = getattr(request.app.state, "v63_memory_governance", None)
    return value if isinstance(value, V63MemoryGovernanceService) else None


def _is_v63_target(target_type: CopilotReviewTargetType) -> bool:
    return target_type in {
        CopilotReviewTargetType.MEMORY_CANDIDATE,
        CopilotReviewTargetType.KNOWLEDGE_CANDIDATE,
        CopilotReviewTargetType.KNOWLEDGE_EVOLUTION_PROPOSAL,
    }


def _is_v64_target(target_type: CopilotReviewTargetType) -> bool:
    return target_type in {
        CopilotReviewTargetType.REPAIR_PROPOSAL,
        CopilotReviewTargetType.OPTIMIZATION_PROPOSAL,
        CopilotReviewTargetType.ENGINEERING_PROPOSAL,
    }


def _v64_service(request: Request) -> ValidationProposalService | None:
    value = getattr(request.app.state, "engineering_validation_proposal_service", None)
    return value if isinstance(value, ValidationProposalService) else None


def _v64_view(value: object, *, target_type: CopilotReviewTargetType | None = None, project_id: str | None = None) -> CopilotReviewView:
    if project_id is not None and value.project_id != project_id:
        raise ValueError("review project binding mismatch")
    selected_type = target_type or CopilotReviewTargetType.ENGINEERING_PROPOSAL
    return CopilotReviewView(
        review_id=value.review_id,
        project_id=value.project_id,
        target_type=selected_type,
        target_id=value.proposal_id,
        target_fingerprint=value.proposal_fingerprint,
        requester=value.requester_id,
        # The canonical proposal lifecycle calls this state WAIT_REVIEW;
        # the shared review binding stores its pending state as PENDING.
        status="WAIT_REVIEW" if value.status == "PENDING" else value.status,
        created_at=value.created_at,
        decision=None if value.decision is None else CopilotReviewDecision(value.decision),
        reviewer=value.reviewer_id,
        decided_at=value.decided_at,
    )


@router.post("", response_model=CopilotReviewView)
def create_review(
    request: Request,
    body: CopilotReviewCreateRequest,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> CopilotReviewView | JSONResponse:
    v64 = _v64_service(request)
    if _is_v64_target(body.target_type):
        if v64 is None:
            return _error(request, 503, "REVIEW_UNAVAILABLE")
        try:
            proposal = v64.get_proposal(identity.tenant_id, body.project_id, body.target_id)
            if proposal is None or proposal.proposal_fingerprint != body.target_fingerprint:
                return _error(request, 409, "REVIEW_FINGERPRINT_CONFLICT")
            value = v64.submit_review(
                tenant_id=identity.tenant_id,
                project_id=body.project_id,
                proposal_id=body.target_id,
                requester_id=identity.requester,
            )
            return _v64_view(value, target_type=body.target_type, project_id=body.project_id)
        except ProposalConflict:
            return _error(request, 409, "REVIEW_CONFLICT")
        except (ProposalRejected, ProposalUnavailable):
            return _error(request, 422, "REVIEW_REJECTED")
    v63 = _v63_service(request)
    if _is_v63_target(body.target_type):
        if v63 is None:
            return _error(request, 503, "REVIEW_UNAVAILABLE")
        try:
            if body.target_type is CopilotReviewTargetType.MEMORY_CANDIDATE:
                value = v63.request_memory_review(
                    tenant_id=identity.tenant_id,
                    project_id=body.project_id,
                    candidate_id=body.target_id,
                    requester_id=identity.requester,
                )
            elif body.target_type is CopilotReviewTargetType.KNOWLEDGE_CANDIDATE:
                value = v63.request_knowledge_review(
                    tenant_id=identity.tenant_id,
                    project_id=body.project_id,
                    candidate_id=body.target_id,
                    requester_id=identity.requester,
                )
            else:
                value = v63.request_evolution_review(
                    tenant_id=identity.tenant_id,
                    project_id=body.project_id,
                    proposal_id=body.target_id,
                    requester_id=identity.requester,
                )
            if value.target_fingerprint != body.target_fingerprint:
                return _error(request, 409, "REVIEW_FINGERPRINT_CONFLICT")
            return _v63_view(value, project_id=body.project_id)
        except V63Error:
            return _error(request, 422, "REVIEW_REJECTED")
    port = getattr(request.app.state, "copilot_review_port", None)
    method = getattr(port, "create_review", None) if port is not None else None
    if not callable(method):
        return _error(request, 503, "REVIEW_UNAVAILABLE")
    try:
        review = ReviewRequest.create(
            review_id=f"review-{body.target_id}-{uuid4().hex[:16]}",
            project_id=body.project_id,
            target_reference=ReviewTargetReference.create(
                target_type=ReviewTargetType(body.target_type.value),
                target_id=body.target_id,
                target_fingerprint=body.target_fingerprint,
            ),
            requester=identity.requester,
            created_at=datetime.now(UTC),
        )
        stored = method(tenant_id=identity.tenant_id, requester=identity.requester, request=review)
        return _view(stored, project_id=body.project_id)
    except Exception:
        return _error(request, 422, "REVIEW_REJECTED")


@router.get("", response_model=tuple[CopilotReviewView, ...])
def list_reviews(
    request: Request,
    project_id: str,
    status: str | None = None,
    target_type: CopilotReviewTargetType | None = None,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> tuple[CopilotReviewView, ...] | JSONResponse:
    v64 = _v64_service(request)
    if target_type is not None and _is_v64_target(target_type):
        if v64 is None:
            return _error(request, 503, "REVIEW_UNAVAILABLE")
        try:
            values = v64.list_reviews(identity.tenant_id, project_id)
            selected_status = None if status is None else ("WAIT_REVIEW" if status == "PENDING" else status)
            filtered = tuple(value for value in values if (selected_status is None or ("WAIT_REVIEW" if value.status == "PENDING" else value.status) == selected_status))
            return tuple(_v64_view(value, target_type=target_type, project_id=project_id) for value in filtered)
        except ProposalRejected:
            return _error(request, 410, "PROJECT_DELETED")
    v63 = _v63_service(request)
    if v63 is None:
        return _error(request, 503, "REVIEW_UNAVAILABLE")
    try:
        if v63.is_project_inaccessible(identity.tenant_id, project_id):
            return _error(request, 410, "PROJECT_DELETED")
        checked_status = None if status is None else V63ReviewStatus(status)
        checked_target = None if target_type is None else V63ReviewTargetType(target_type.value)
        values = v63.list_reviews(identity.tenant_id, project_id, status=checked_status, target_type=checked_target)
        return tuple(_v63_view(value, project_id=project_id) for value in values)
    except (ValueError, V63Error):
        return _error(request, 422, "REVIEW_REJECTED")


@router.get("/{review_id}", response_model=CopilotReviewView)
def get_review(
    review_id: str,
    project_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> CopilotReviewView | JSONResponse:
    v64 = _v64_service(request)
    if v64 is not None:
        try:
            value = v64.get_review(identity.tenant_id, project_id, review_id)
            if value is not None:
                proposal = v64.get_proposal(identity.tenant_id, project_id, value.proposal_id)
                target_type = None
                if proposal is not None:
                    target_type = {
                        "REPAIR": CopilotReviewTargetType.REPAIR_PROPOSAL,
                        "OPTIMIZATION": CopilotReviewTargetType.OPTIMIZATION_PROPOSAL,
                    }.get(proposal.proposal_type.value, CopilotReviewTargetType.ENGINEERING_PROPOSAL)
                return _v64_view(value, target_type=target_type, project_id=project_id)
        except ProposalRejected:
            return _error(request, 410, "PROJECT_DELETED")
    v63 = _v63_service(request)
    if v63 is not None:
        try:
            if v63.is_project_inaccessible(identity.tenant_id, project_id):
                return _error(request, 410, "PROJECT_DELETED")
            value = v63.store.get_review(identity.tenant_id, project_id, review_id)
            if value is not None:
                return _v63_view(value, project_id=project_id)
        except V63Error:
            return _error(request, 422, "REVIEW_REJECTED")
    port = getattr(request.app.state, "copilot_review_port", None)
    method = getattr(port, "get_review", None) if port is not None else None
    if not callable(method):
        return _error(request, 503, "REVIEW_UNAVAILABLE")
    try:
        value = method(tenant_id=identity.tenant_id, project_id=project_id, review_id=review_id)
        if value is None:
            return _error(request, 404, "REVIEW_NOT_FOUND")
        return _view(value, project_id=project_id)
    except Exception:
        return _error(request, 422, "REVIEW_REJECTED")


@router.post("/{review_id}/decision", response_model=CopilotReviewView)
def decide_review(
    review_id: str,
    body: CopilotReviewDecisionRequest,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> CopilotReviewView | JSONResponse:
    v64 = _v64_service(request)
    if v64 is not None:
        try:
            existing = v64.get_review(identity.tenant_id, body.project_id, review_id)
            if existing is not None:
                value = v64.decide_review(
                    tenant_id=identity.tenant_id,
                    project_id=body.project_id,
                    review_id=review_id,
                    reviewer_id=body.reviewer,
                    decision="APPROVE" if body.decision is CopilotReviewDecision.APPROVE else "REJECT",
                )
                return _v64_view(value, project_id=body.project_id)
        except ProposalConflict:
            return _error(request, 409, "REVIEW_CONFLICT")
        except ProposalRejected:
            return _error(request, 422, "REVIEW_REJECTED")
    v63 = _v63_service(request)
    if v63 is not None:
        try:
            if v63.is_project_inaccessible(identity.tenant_id, body.project_id):
                return _error(request, 410, "PROJECT_DELETED")
            existing = v63.store.get_review(identity.tenant_id, body.project_id, review_id)
            if existing is not None:
                decision = "APPROVE" if body.decision is CopilotReviewDecision.APPROVE else "REJECT"
                value = v63.decide_review(
                    tenant_id=identity.tenant_id,
                    project_id=body.project_id,
                    review_id=review_id,
                    reviewer_id=body.reviewer,
                    decision=decision,
                )
                return _v63_view(value, project_id=body.project_id)
        except V63Error:
            return _error(request, 422, "REVIEW_REJECTED")
    port = getattr(request.app.state, "copilot_review_port", None)
    method = getattr(port, "decide", None) if port is not None else None
    if not callable(method):
        return _error(request, 503, "REVIEW_UNAVAILABLE")
    try:
        value = method(
            tenant_id=identity.tenant_id,
            decision=HumanDecision.create(
                decision_id=f"decision-{review_id}",
                review_id=review_id,
                project_id=body.project_id,
                decision=ReviewDecision(body.decision.value),
                reviewer=body.reviewer,
                comment=body.comment,
                decided_at=body.decided_at,
            ),
        )
        return _view(value, project_id=body.project_id)
    except Exception:
        return _error(request, 422, "REVIEW_REJECTED")


__all__ = ("TenantBoundReviewPort", "router")
