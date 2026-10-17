from __future__ import annotations

import copy

from embedded_copilot.engineering_context import (
    EngineeringRuntimeContextOverlay,
    RuntimeContextEvidence,
    RuntimeEvidenceType,
)

from .contracts import deterministic_id
from .models import EngineeringOptimizationOverlay


class OptimizationRuntimeOverlayProjector:
    """Pure projection; it never writes an EngineeringContextSnapshot."""

    def project(
        self, overlay: EngineeringOptimizationOverlay
    ) -> EngineeringRuntimeContextOverlay:
        checked = EngineeringOptimizationOverlay.model_validate(
            copy.deepcopy(overlay.model_dump(mode="python"))
        )
        evidence = RuntimeContextEvidence(
            evidence_id=deterministic_id(
                "optimization-runtime-evidence", checked.fingerprint
            ),
            project_id=checked.project_id,
            evidence_type=RuntimeEvidenceType.OPTIMIZATION_DECISION,
            source_id=checked.decision.decision_id,
            source_fingerprint=checked.decision.fingerprint,
            summary="Human-approved optimization decision is projected for review.",
            observed_at=checked.created_at,
        )
        return EngineeringRuntimeContextOverlay(
            overlay_id=deterministic_id(
                "optimization-runtime-overlay", checked.fingerprint
            ),
            project_id=checked.project_id,
            base_context_fingerprint=checked.base_context_fingerprint,
            evidence=(evidence,),
            created_at=checked.created_at,
        )
