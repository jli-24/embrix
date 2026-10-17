from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import ContextSourceType, ContextVerificationStatus, EngineeringContextEvidence
from embedded_copilot.engineering_intelligence import EvidenceGate, EvidenceGateRejected


def _evidence(project_id: str = "project-1") -> EngineeringContextEvidence:
    return EngineeringContextEvidence.create(
        evidence_id="evidence-1",
        tenant_id="tenant-1",
        project_id=project_id,
        source_type=ContextSourceType.DEBUG,
        subject="artifact",
        summary="observed HAL_TIMEOUT",
        fact_key="error",
        fact_value="HAL_TIMEOUT",
        source_reference="artifact:debug-1",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=1.0,
        verification_status=ContextVerificationStatus.VERIFIED,
        provenance=("debug-1",),
    )


def test_gate_requires_evidence_and_preserves_identity() -> None:
    result = EvidenceGate().require((_evidence(),), tenant_id="tenant-1", project_id="project-1")
    assert result.evidence_count == 1
    assert result.evidence[0].evidence_id == "evidence-1"


def test_gate_rejects_empty_and_cross_project_evidence() -> None:
    with pytest.raises(EvidenceGateRejected):
        EvidenceGate().require((), tenant_id="tenant-1", project_id="project-1")
    with pytest.raises(EvidenceGateRejected, match="identity"):
        EvidenceGate().require((_evidence("project-2"),), tenant_id="tenant-1", project_id="project-1")
