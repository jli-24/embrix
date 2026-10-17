from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
)
from embedded_copilot.engineering_intelligence.correlation import EngineeringCorrelationService


def _evidence(evidence_id: str, source_type: ContextSourceType, summary: str) -> EngineeringContextEvidence:
    return EngineeringContextEvidence.create(
        evidence_id=evidence_id,
        tenant_id="tenant-1",
        project_id="project-1",
        source_type=source_type,
        subject=evidence_id,
        summary=summary,
        fact_key="observation",
        fact_value=summary,
        source_reference=f"artifact:{evidence_id}",
        source_fingerprint="sha256:" + evidence_id[-1] * 64,
        confidence=1.0,
        verification_status=ContextVerificationStatus.VERIFIED,
    )


def test_correlation_returns_candidate_with_all_evidence_ids() -> None:
    result = EngineeringCorrelationService().correlate(
        firmware=(_evidence("firmware-1", ContextSourceType.FIRMWARE_SOURCE, "sensor_read calls HAL_I2C_Master_Transmit"),),
        debug=(_evidence("debug-1", ContextSourceType.DEBUG, "debug artifact reports HAL_TIMEOUT"),),
    )
    assert len(result) == 1
    assert result[0].finding_type == "ROOT_CAUSE_CANDIDATE"
    assert result[0].evidence_ids == ("firmware-1", "debug-1")
    assert result[0].missing_evidence


def test_correlation_rejects_cross_project_inputs() -> None:
    debug = _evidence("debug-1", ContextSourceType.DEBUG, "HAL_TIMEOUT").model_copy(update={"project_id": "project-2"})
    with pytest.raises(ValueError, match="project"):
        EngineeringCorrelationService().correlate(
            firmware=(_evidence("firmware-1", ContextSourceType.FIRMWARE_SOURCE, "sensor_read calls HAL_I2C_Master_Transmit"),),
            debug=(debug,),
        )
