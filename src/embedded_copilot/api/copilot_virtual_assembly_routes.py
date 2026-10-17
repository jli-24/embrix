from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCatalogRejected,
    ProjectCatalogUnavailable,
)
from embedded_copilot.virtual_assembly import (
    CanonicalVirtualAssemblyAdapter,
    SQLiteVirtualAssemblyStore,
    VirtualAssemblyService,
    VirtualBoardAssembly,
)

router = APIRouter(
    prefix="/api/v1/copilot", tags=["copilot-virtual-assembly"]
)


class VirtualAssemblyNaturalLanguageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    request: str

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError("virtual assembly request is invalid")
        return value.strip()


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


def _recognized(value: str) -> bool:
    normalized = value.casefold()
    return any(
        marker in normalized
        for marker in (
            "虚拟装配",
            "虚拟板",
            "装配",
            "virtual assembly",
            "virtual board",
            "populated board",
        )
    )


def _assemble(
    request: Request,
    project_id: str,
    identity: TrustedDesktopIdentity,
) -> VirtualBoardAssembly | JSONResponse:
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    settings = getattr(request.app.state, "settings", None)
    toolchain_bin = getattr(settings, "consistency_toolchain_bin", None)
    if catalog is None or not callable(
        getattr(catalog, "knowledge_snapshot_root", None)
    ):
        return _error(request, 503, "VIRTUAL_ASSEMBLY_PROJECT_CATALOG_UNAVAILABLE")
    if toolchain_bin is None:
        return _error(request, 503, "VIRTUAL_ASSEMBLY_TOOLCHAIN_UNAVAILABLE")
    try:
        root = catalog.knowledge_snapshot_root(project_id, tenant_id=identity.tenant_id)
        source = CanonicalVirtualAssemblyAdapter(
            Path(root),
            project_id=project_id,
            toolchain_bin=Path(toolchain_bin),
        ).collect()
        assembly = VirtualAssemblyService().assemble(source)
        store = getattr(request.app.state, "virtual_assembly_store", None)
        if store is None:
            database = Path(settings.desktop_state_database).with_name(
                f"{Path(settings.desktop_state_database).stem}-virtual-assembly.sqlite3"
            )
            store = SQLiteVirtualAssemblyStore(database)
        store.save(assembly)
        return assembly
    except (ProjectCatalogNotFound, FileNotFoundError):
        return _error(request, 404, "VIRTUAL_ASSEMBLY_PROJECT_NOT_FOUND")
    except ProjectCatalogForbidden:
        return _error(request, 403, "VIRTUAL_ASSEMBLY_PROJECT_FORBIDDEN")
    except (ProjectCatalogRejected, ProjectCatalogUnavailable, ValueError):
        return _error(request, 422, "VIRTUAL_ASSEMBLY_INPUT_REJECTED")
    except (OSError, RuntimeError):
        return _error(request, 503, "VIRTUAL_ASSEMBLY_UNAVAILABLE")


@router.post(
    "/projects/{project_id}/virtual-assembly",
    response_model=VirtualBoardAssembly,
)
def assemble_from_natural_language(
    project_id: str,
    payload: VirtualAssemblyNaturalLanguageRequest,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),  # noqa: B008
) -> VirtualBoardAssembly | JSONResponse:
    if not _recognized(payload.request):
        return _error(request, 422, "VIRTUAL_ASSEMBLY_REQUEST_NOT_RECOGNIZED")
    return _assemble(request, project_id, identity)


__all__ = ("router",)
