# ruff: noqa: B008, BLE001
"""Production-session and Catalog Owner bound consumption consent routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.api.project_catalog_models import HardwareSelectionChoiceV2Input
from embedded_copilot.model_gateway.invocation.consumption_contracts import (
    ConsumptionCandidateSelectorV1,
    ConsumptionPreviewV1,
)
from embedded_copilot.model_gateway.invocation.consumption_store import (
    ConsumptionRejected,
)

router = APIRouter(prefix="/api/v1/copilot/projects", tags=["model-consumption"])


class ConsumptionPreviewView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    status: Literal["READY", "BLOCKED"]
    preview: ConsumptionPreviewV1 | None
    blockers: tuple[str, ...]
    dispatch_ready: Literal[False] = False


class ConsumptionGrantView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    grant_id: str
    preview_id: str
    preview_fingerprint: str
    scope_fingerprint: str
    status: Literal["ACTIVE", "REVOKED", "EXPIRED", "STALE"]
    revision: int = Field(strict=True, ge=1, le=2)
    not_after: str
    counter_status: str
    generation_status: str
    total_external_request_count: int | None = Field(strict=True, ge=0, le=2)
    dispatch_ready: Literal[False] = False


class ConsumptionPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal[1] = 1
    selection_schema_version: Literal[1, 2, 3] = 1
    operation_id: Literal["HARDWARE_SELECTION_PROPOSAL"] = "HARDWARE_SELECTION_PROPOSAL"
    request_id: str
    plan_id: str
    phase_id: str
    plan_task_id: str
    expected_head_revision_id: str | None = None
    expected_head_selection_id: str | None = None
    expected_head_selection_fingerprint: str | None = None
    selection_preview_fingerprint: str | None = None
    parent_choices: list[HardwareSelectionChoiceV2Input] = Field(
        default_factory=list, max_length=64
    )

    @field_validator("schema_version", "selection_schema_version", mode="before")
    @classmethod
    def _exact_version(cls, value: object) -> int:
        if type(value) is not int:
            raise ValueError("exact integer version required")
        return value

    def selector(self) -> ConsumptionCandidateSelectorV1:
        data = self.model_dump(mode="python")
        data["parent_choices"] = tuple(
            (item.slot_id, item.source_id, item.source_fingerprint)
            for item in self.parent_choices
        )
        return ConsumptionCandidateSelectorV1.model_validate(data)

    @model_validator(mode="after")
    def _checked(self):
        self.selector()
        return self


class ConsumptionConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    preview_id: str = Field(min_length=1, max_length=256)
    preview_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    consent: Literal[True]

    @field_validator("consent", mode="before")
    @classmethod
    def _explicit_consent(cls, value: object) -> bool:
        if type(value) is not bool or value is not True:
            raise ValueError("explicit consent required")
        return value


class ConsumptionRevokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    expected_revision: int = Field(strict=True, ge=1, le=2)


def _error(request: Request, code: str, status: int = 503) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": code, "trace_id": request.state.trace_id}
    )


def _dependencies(
    request: Request,
    identity: TrustedDesktopIdentity,
    project_id: str,
    conversation_id: str,
):
    if identity.authentication != "LOCAL_BOOTSTRAP_SESSION":
        raise ConsumptionRejected("PRODUCTION_SESSION_REQUIRED")
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    service = getattr(request.app.state, "model_consumption_service", None)
    if catalog is None or service is None:
        raise ConsumptionRejected("MODEL_CONSUMPTION_UNAVAILABLE")
    catalog.get_project_for_requester(
        project_id, tenant_id=identity.tenant_id, requester=identity.requester
    )
    catalog.conversation(
        project_id,
        conversation_id,
        tenant_id=identity.tenant_id,
        requester=identity.requester,
    )
    owner = {
        "tenant_id": identity.tenant_id,
        "principal_id": identity.requester,
        "project_id": project_id,
        "conversation_id": conversation_id,
    }

    def prepare(selector: ConsumptionCandidateSelectorV1):
        from embedded_copilot.api.project_catalog_routes import (
            HardwareModelCandidateProposalV2Input,
            HardwareModelCandidateProposalV3Input,
            ModelCandidateProposalInput,
            _prepare_model_candidate,
        )

        data = {
            name: getattr(selector, name)
            for name in ("request_id", "plan_id", "phase_id", "plan_task_id")
        }
        kind = ModelCandidateProposalInput
        if selector.selection_schema_version in {2, 3}:
            data.update(
                schema_version=selector.selection_schema_version,
                expected_head_revision_id=selector.expected_head_revision_id,
                expected_head_selection_id=selector.expected_head_selection_id,
                expected_head_selection_fingerprint=selector.expected_head_selection_fingerprint,
                preview_fingerprint=selector.selection_preview_fingerprint,
            )
            kind = HardwareModelCandidateProposalV2Input
        if selector.selection_schema_version == 3:
            data["parent_choices"] = [
                {"slot_id": a, "source_id": b, "source_fingerprint": c}
                for a, b, c in selector.parent_choices
            ]
            kind = HardwareModelCandidateProposalV3Input
        payload = kind.model_validate(data)
        invocation_request, authority, *_ = _prepare_model_candidate(
            project_id,
            conversation_id,
            payload,
            request,
            identity,
            catalog,
            "HARDWARE_SELECTION_PROPOSAL",
        )
        invocation = request.app.state.model_invocation_service
        return invocation.prepare_consumption_context(
            invocation_request, authority_source=authority
        )

    return service, owner, prepare


def _failure(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, ConsumptionRejected):
        code = error.code
        status = (
            401
            if code == "PRODUCTION_SESSION_REQUIRED"
            else 404
            if code
            in {
                "GRANT_NOT_FOUND",
                "PREVIEW_NOT_FOUND",
                "CONSUMPTION_OWNER_MISMATCH",
                "CONSUMPTION_NOT_FOUND",
            }
            else 410
            if code in {"PREVIEW_EXPIRED", "GRANT_EXPIRED"}
            else 409
            if code
            in {
                "PREVIEW_STALE",
                "GRANT_SCOPE_CONFLICT",
                "GRANT_REVISION_CONFLICT",
                "GRANT_SCOPE_STALE",
                "GRANT_REVOKED",
            }
            else 503
        )
        return _error(request, code, status)
    from embedded_copilot.api.project_catalog_routes import _map

    return _map(request, error)


@router.post(
    "/{project_id}/conversations/{conversation_id}/model-consumption/preview",
    response_model=ConsumptionPreviewView,
)
def preview_consumption(
    project_id: str,
    conversation_id: str,
    payload: ConsumptionPreviewInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    try:
        service, owner, prepare = _dependencies(
            request, identity, project_id, conversation_id
        )
        return service.preview(payload.selector(), owner=owner, prepare=prepare)
    except Exception as error:
        # Incomplete local preparation produces a safe non-confirmable projection.
        from embedded_copilot.model_gateway.exceptions import ModelGatewayError

        if isinstance(error, ModelGatewayError):
            return {
                "schema_version": 1,
                "status": "BLOCKED",
                "preview": None,
                "blockers": ("MODEL_PREPARATION_UNAVAILABLE",),
                "dispatch_ready": False,
            }
        return _failure(request, error)


@router.post(
    "/{project_id}/conversations/{conversation_id}/model-consumption/grants",
    response_model=ConsumptionGrantView,
)
def confirm_consumption(
    project_id: str,
    conversation_id: str,
    payload: ConsumptionConfirmInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    try:
        service, owner, prepare = _dependencies(
            request, identity, project_id, conversation_id
        )
        return service.confirm(
            preview_id=payload.preview_id,
            preview_fingerprint=payload.preview_fingerprint,
            owner=owner,
            prepare=prepare,
        )
    except Exception as error:
        return _failure(request, error)


@router.get(
    "/{project_id}/conversations/{conversation_id}/model-consumption/grants/{grant_id}",
    response_model=ConsumptionGrantView,
)
def get_consumption(
    project_id: str,
    conversation_id: str,
    grant_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    try:
        service, owner, prepare = _dependencies(
            request, identity, project_id, conversation_id
        )
        return service.get(grant_id, owner=owner, prepare=prepare)
    except Exception as error:
        return _failure(request, error)


@router.post(
    "/{project_id}/conversations/{conversation_id}/model-consumption/grants/{grant_id}/revoke",
    response_model=ConsumptionGrantView,
)
def revoke_consumption(
    project_id: str,
    conversation_id: str,
    grant_id: str,
    payload: ConsumptionRevokeInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    try:
        service, owner, _ = _dependencies(
            request, identity, project_id, conversation_id
        )
        return service.revoke(
            grant_id, expected_revision=payload.expected_revision, owner=owner
        )
    except Exception as error:
        return _failure(request, error)
