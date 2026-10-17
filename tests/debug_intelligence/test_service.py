from __future__ import annotations

import pytest

from embedded_copilot.debug_intelligence import DebugIntelligenceService
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EvidenceTrustLevel,
)


def test_debug_service_bounds_and_redacts_serial_input() -> None:
    result = DebugIntelligenceService(max_bytes=1024, max_lines=10).analyze(
        tenant_id="tenant-1",
        project_id="project-1",
        artifact_id="artifact-1",
        source="HAL_TIMEOUT token=private\nI2C transaction failed",
        source_reference="artifact:artifact-1",
    )
    assert result.evidence
    assert all(item.source_type is ContextSourceType.DEBUG for item in result.evidence)
    assert all(item.trust_level is EvidenceTrustLevel.OBSERVATION for item in result.evidence)
    assert "private" not in " ".join(item.summary for item in result.evidence)
    assert result.root_cause_candidates


def test_debug_service_requires_mapping_for_hardfault_source_attribution() -> None:
    result = DebugIntelligenceService().analyze(
        tenant_id="tenant-1",
        project_id="project-1",
        artifact_id="hardfault-1",
        source="HardFault PC=0x08001234 CFSR=0x82",
        source_reference="artifact:hardfault-1",
    )
    assert "ELF/MAP symbol mapping" in result.missing_evidence
    assert not any("source line" in item.casefold() for item in result.root_cause_candidates)


@pytest.mark.parametrize("source", ["", "x" * 200])
def test_debug_service_rejects_empty_or_oversized_input(source: str) -> None:
    with pytest.raises(ValueError):
        DebugIntelligenceService(max_bytes=100).analyze(
            tenant_id="tenant-1",
            project_id="project-1",
            artifact_id="artifact-1",
            source=source,
            source_reference="artifact:artifact-1",
        )
