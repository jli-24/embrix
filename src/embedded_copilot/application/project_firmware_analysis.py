from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from embedded_copilot.firmware_intelligence import (
    FirmwareIntelligenceResult,
    FirmwareIntelligenceService,
    FirmwareSourceProvider,
)


class FirmwareProjectRootPort(Protocol):
    def knowledge_snapshot_root(self, project_id: str, *, tenant_id: str) -> object: ...


class ProjectFirmwareAnalysisPort:
    """Tenant/project-bound, parser-only Firmware analysis Port."""

    def __init__(self, *, project_catalog: FirmwareProjectRootPort) -> None:
        self._catalog = project_catalog
        self._services: dict[tuple[str, str], FirmwareIntelligenceService] = {}

    def _service(self, *, tenant_id: str, project_id: str) -> FirmwareIntelligenceService:
        key = (tenant_id, project_id)
        service = self._services.get(key)
        if service is None:
            service = FirmwareIntelligenceService(
                source_provider=FirmwareSourceProvider(
                    lambda bound_project_id: self._catalog.knowledge_snapshot_root(
                        bound_project_id, tenant_id=tenant_id
                    )
                )
            )
            self._services[key] = service
        return service

    @staticmethod
    def _evidence(result: FirmwareIntelligenceResult) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "evidence_id": item.evidence_id,
                "level": "OBSERVATION",
                "summary": item.summary,
                "source_reference": item.source_reference,
                "source_fingerprint": item.source_fingerprint,
                "confidence": item.confidence or 0.0,
            }
            for item in result.evidence
        )

    def analyze(self, *, tenant_id: str, request: object) -> Mapping[str, object]:
        project_id = getattr(request, "project_id", None)
        question = getattr(request, "question", None)
        if not isinstance(project_id, str) or not isinstance(question, str):
            raise ValueError("firmware analysis request is invalid")
        result = self._service(tenant_id=tenant_id, project_id=project_id).analyze(
            project_id, question
        )
        return {
            "project_id": project_id,
            "facts": result.facts,
            "evidence": self._evidence(result),
            "recommendations": (
                "Treat parser observations as read-only evidence; review hardware assumptions before changes.",
            ),
            "confidence": result.confidence,
            "limitations": result.limitations,
            "source_fingerprint": result.source_fingerprint,
        }


__all__ = ("ProjectFirmwareAnalysisPort",)
