"""Desktop session boundary for explicit local application updates."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.production_release.desktop_update import (
    DesktopUpdateService,
    UpdateRejected,
)

router = APIRouter(prefix="/api/v1/copilot/desktop/update", tags=["desktop-update"])


class InstallUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    build_id: str = Field(min_length=8, max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _service(request: Request) -> DesktopUpdateService:
    service = getattr(request.app.state, "desktop_update_service", None)
    if not isinstance(service, DesktopUpdateService):
        raise HTTPException(status_code=503, detail="DESKTOP_UPDATE_UNAVAILABLE")
    return service


def _rejected(error: UpdateRejected) -> HTTPException:
    if error.code == "UPDATER_START_FAILED":
        return HTTPException(status_code=500, detail=error.code)
    status = 409 if error.code in {"UPDATE_BUSY", "UPDATE_ALREADY_STAGED", "CANDIDATE_CHANGED",
                                    "ACTIVE_ENGINEERING_OPERATION", "UPDATE_NOT_STAGED"} else 400
    return HTTPException(status_code=status, detail=error.code)


@router.get("/status")
def status(request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> dict:  # noqa: B008
    del identity
    try:
        return _service(request).status()
    except UpdateRejected as error:
        raise _rejected(error) from error


@router.post("/check")
def check(request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> dict:  # noqa: B008
    del identity
    try:
        return _service(request).check()
    except UpdateRejected as error:
        raise _rejected(error) from error


@router.post("/install")
def install(payload: InstallUpdateInput, request: Request,
            identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> dict:  # noqa: B008
    del identity
    try:
        return _service(request).install(build_id=payload.build_id, sha256=payload.sha256)
    except UpdateRejected as error:
        raise _rejected(error) from error


@router.post("/restart")
def restart(request: Request, background_tasks: BackgroundTasks,
            identity: TrustedDesktopIdentity = Depends(desktop_identity)) -> dict:  # noqa: B008
    del identity
    try:
        return _service(request).restart(background_tasks.add_task)
    except UpdateRejected as error:
        raise _rejected(error) from error



__all__ = ("router",)
