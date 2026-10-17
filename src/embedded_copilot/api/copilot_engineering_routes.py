from __future__ import annotations

import inspect
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity, desktop_identity

from .copilot_engineering_models import (
    DebugAnalysisRequest,
    DebugAnalysisResponse,
    EngineeringEvidenceLevel,
    EngineeringEvidenceView,
    FirmwareAnalysisRequest,
    FirmwareAnalysisResponse,
)


class FirmwareAnalysisPort(Protocol):
    def analyze(self, *, tenant_id: str, request: FirmwareAnalysisRequest) -> Any: ...


class DebugAnalysisPort(Protocol):
    def analyze(self, *, tenant_id: str, request: DebugAnalysisRequest) -> Any: ...


router = APIRouter(prefix="/api/v1/copilot", tags=["copilot-engineering-analysis"])


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "trace_id": getattr(request.state, "trace_id", "")},
    )


async def _invoke(method: Any, **kwargs: object) -> object:
    value = method(**kwargs)
    return await value if inspect.isawaitable(value) else value


def _evidence(
    *,
    project_id: str,
    evidence_id: str,
    level: EngineeringEvidenceLevel,
    summary: str,
    source_reference: str,
    source_fingerprint: str,
    confidence: float,
    needs_more_evidence: tuple[str, ...] = (),
) -> EngineeringEvidenceView:
    return EngineeringEvidenceView(
        evidence_id=evidence_id,
        project_id=project_id,
        level=(EngineeringEvidenceLevel(level) if isinstance(level, str) else level),
        summary=summary,
        source_reference=source_reference,
        source_fingerprint=source_fingerprint,
        confidence=confidence,
        needs_more_evidence=needs_more_evidence,
    )


@router.post("/firmware/analyze", response_model=FirmwareAnalysisResponse)
async def analyze_firmware(
    request: Request,
    body: FirmwareAnalysisRequest,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> FirmwareAnalysisResponse | JSONResponse:
    port = getattr(request.app.state, "copilot_firmware_analysis_port", None)
    method = getattr(port, "analyze", None) if port is not None else None
    if not callable(method):
        return _error(request, 503, "FIRMWARE_ANALYSIS_UNAVAILABLE")
    try:
        raw = await _invoke(method, tenant_id=identity.tenant_id, request=body)
        if not isinstance(raw, dict):
            raw = raw.model_dump(mode="python")
        if raw.get("project_id") != body.project_id:
            raise ValueError("firmware project binding mismatch")
        evidence = tuple(
            _evidence(project_id=body.project_id, **item)
            for item in raw.get("evidence", ())
        )
        return FirmwareAnalysisResponse(
            project_id=body.project_id,
            facts=tuple(raw.get("facts", ())),
            evidence=evidence,
            recommendations=tuple(raw.get("recommendations", ())),
            confidence=float(raw.get("confidence", 0.0)),
            limitations=tuple(raw.get("limitations", ())),
            source_fingerprint=raw["source_fingerprint"],
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return _error(request, 422, "FIRMWARE_ANALYSIS_REJECTED")
    except Exception:
        return _error(request, 503, "FIRMWARE_ANALYSIS_FAILED")


@router.post("/debug/analyze", response_model=DebugAnalysisResponse)
async def analyze_debug(
    request: Request,
    body: DebugAnalysisRequest,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
) -> DebugAnalysisResponse | JSONResponse:
    port = getattr(request.app.state, "copilot_debug_analysis_port", None)
    method = getattr(port, "analyze", None) if port is not None else None
    if not callable(method):
        return _error(request, 503, "DEBUG_ANALYSIS_UNAVAILABLE")
    try:
        raw = await _invoke(method, tenant_id=identity.tenant_id, request=body)
        if not isinstance(raw, dict):
            raw = raw.model_dump(mode="python")
        if raw.get("project_id") != body.project_id or raw.get("failure_reference") != body.failure_reference:
            raise ValueError("debug project binding mismatch")
        candidates = tuple(
            _evidence(project_id=body.project_id, **item)
            for item in raw.get("root_cause_candidates", ())
        )
        return DebugAnalysisResponse(
            project_id=body.project_id,
            failure_reference=body.failure_reference,
            root_cause_candidates=candidates,
            confidence=float(raw.get("confidence", 0.0)),
            needs_more_evidence=tuple(raw.get("needs_more_evidence", ())),
            recommendations=tuple(raw.get("recommendations", ())),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return _error(request, 422, "DEBUG_ANALYSIS_REJECTED")
    except Exception:
        return _error(request, 503, "DEBUG_ANALYSIS_FAILED")


__all__ = ("router",)
