from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.engineering_consistency import (
    CanonicalProjectConsistencyAdapter,
    HardwareFirmwareConsistencyResult,
    HardwareFirmwareConsistencyService,
    SQLiteConsistencyStore,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCatalogRejected,
    ProjectCatalogUnavailable,
)

router = APIRouter(
    prefix="/api/v1/copilot", tags=["copilot-hardware-firmware-consistency"]
)


class ConsistencyNaturalLanguageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    request: str

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise ValueError("consistency request is invalid")
        return value.strip()


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


def _evaluate(
    request: Request,
    project_id: str,
    identity: TrustedDesktopIdentity,
) -> HardwareFirmwareConsistencyResult | JSONResponse:
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    settings = getattr(request.app.state, "settings", None)
    toolchain_bin = getattr(settings, "consistency_toolchain_bin", None)
    if catalog is None or not callable(
        getattr(catalog, "knowledge_snapshot_root", None)
    ):
        return _error(request, 503, "CONSISTENCY_PROJECT_CATALOG_UNAVAILABLE")
    if toolchain_bin is None:
        return _error(request, 503, "CONSISTENCY_TOOLCHAIN_UNAVAILABLE")
    try:
        root = catalog.knowledge_snapshot_root(project_id, tenant_id=identity.tenant_id)
        adapter = CanonicalProjectConsistencyAdapter(
            Path(root),
            project_id=project_id,
            toolchain_bin=Path(toolchain_bin),
        )
        input_value = adapter.collect()
        result = HardwareFirmwareConsistencyService().evaluate(input_value)
        store = getattr(request.app.state, "consistency_store", None)
        if store is None:
            database = Path(settings.desktop_state_database).with_name(
                f"{Path(settings.desktop_state_database).stem}-consistency.sqlite3"
            )
            store = SQLiteConsistencyStore(database)
        store.save(result)
        return result
    except (ProjectCatalogNotFound, FileNotFoundError):
        return _error(request, 404, "CONSISTENCY_PROJECT_NOT_FOUND")
    except ProjectCatalogForbidden:
        return _error(request, 403, "CONSISTENCY_PROJECT_FORBIDDEN")
    except (ProjectCatalogRejected, ProjectCatalogUnavailable, ValueError):
        return _error(request, 422, "CONSISTENCY_INPUT_REJECTED")
    except (OSError, RuntimeError):
        return _error(request, 503, "CONSISTENCY_UNAVAILABLE")


@router.get(
    "/projects/{project_id}/hardware-firmware-consistency",
    response_model=HardwareFirmwareConsistencyResult,
)
def evaluate_hardware_firmware_consistency(
    request: Request,
    project_id: str,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),  # noqa: B008
) -> HardwareFirmwareConsistencyResult | JSONResponse:
    return _evaluate(request, project_id, identity)


@router.post(
    "/projects/{project_id}/hardware-firmware-consistency",
    response_model=HardwareFirmwareConsistencyResult,
)
def evaluate_from_natural_language(
    project_id: str,
    payload: ConsistencyNaturalLanguageRequest,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),  # noqa: B008
) -> HardwareFirmwareConsistencyResult | JSONResponse:
    normalized = payload.request.casefold()
    markers = (
        "硬件",
        "固件",
        "一致",
        "引脚",
        "pcb",
        "firmware",
        "consistency",
        "pin",
    )
    if not any(marker in normalized for marker in markers):
        return _error(request, 422, "CONSISTENCY_REQUEST_NOT_RECOGNIZED")
    return _evaluate(request, project_id, identity)


__all__ = ("router",)
