from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.desktop_workspace_projection import (
    DesktopWorkspaceProjection,
    DesktopWorkspaceProjectionService,
    ProjectionUnavailable,
    WorkbenchProjection,
)
from embedded_copilot.engineering_validation_proposal import ProposalRejected
from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCatalogUnavailable,
)
from embedded_copilot.v63_memory_governance import V63Rejected

router = APIRouter(prefix="/api/v1/copilot/desktop", tags=["desktop-workspace-projection"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


@router.get(
    "/projects/{project_id}/workspace",
    response_model=DesktopWorkspaceProjection,
    summary="Read-only bounded Desktop workspace projection",
)
def workspace_projection(
    project_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=256),
    conversation_id: str | None = Query(default=None, max_length=160),
    task_id: str | None = Query(default=None, max_length=160),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> DesktopWorkspaceProjection | JSONResponse:
    service = getattr(request.app.state, "desktop_workspace_projection", None)
    if not isinstance(service, DesktopWorkspaceProjectionService):
        return _error(request, 503, "DESKTOP_WORKSPACE_PROJECTION_UNAVAILABLE")
    try:
        return service.project(
            tenant_id=identity.tenant_id,
            requester_id=identity.requester,
            project_id=project_id,
            limit=limit,
            cursor=cursor,
            conversation_id=conversation_id,
            task_id=task_id,
        )
    except (ProjectionUnavailable, ProjectCatalogUnavailable):
        return _error(request, 503, "DESKTOP_WORKSPACE_PROJECTION_UNAVAILABLE")
    except (ProjectCatalogNotFound, LookupError):
        return _error(request, 404, "PROJECT_NOT_FOUND")
    except (ProjectCatalogForbidden, PermissionError):
        return _error(request, 403, "PROJECT_ACCESS_DENIED")
    except (ProposalRejected, V63Rejected):
        return _error(request, 410, "PROJECT_DELETED")
    except ValueError:
        return _error(request, 422, "DESKTOP_WORKSPACE_PROJECTION_REJECTED")


@router.get(
    "/projects/{project_id}/workbench",
    response_model=WorkbenchProjection,
    summary="Read-only engineering workbench projection",
)
def workbench_projection(
    project_id: str,
    request: Request,
    limit: int = Query(default=500, ge=1, le=2000),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> WorkbenchProjection | JSONResponse:
    service = getattr(request.app.state, "desktop_workspace_projection", None)
    if not isinstance(service, DesktopWorkspaceProjectionService):
        return _error(request, 503, "DESKTOP_WORKBENCH_PROJECTION_UNAVAILABLE")
    try:
        return service.workbench(
            tenant_id=identity.tenant_id,
            requester_id=identity.requester,
            project_id=project_id,
            limit=limit,
        )
    except (ProjectionUnavailable, ProjectCatalogUnavailable):
        return _error(request, 503, "DESKTOP_WORKBENCH_PROJECTION_UNAVAILABLE")
    except (ProjectCatalogNotFound, LookupError):
        return _error(request, 404, "PROJECT_NOT_FOUND")
    except (ProjectCatalogForbidden, PermissionError):
        return _error(request, 403, "PROJECT_ACCESS_DENIED")
    except (ProposalRejected, V63Rejected):
        return _error(request, 410, "PROJECT_DELETED")
    except ValueError:
        return _error(request, 422, "DESKTOP_WORKBENCH_PROJECTION_REJECTED")


__all__ = ("router",)
