from pathlib import Path

import pytest

from embedded_copilot.engineering_context.adapters.project_engineering import ProjectEngineeringEvidenceAdapter
from embedded_copilot.engineering_context.models import ContextSourceType, EngineeringContextRequest
from embedded_copilot.engineering_intelligence.correlation import EngineeringCorrelationService


_PROJECT_ROOT = Path(r"C:\acceptance_projects\v6.2-engineering-golden")


pytestmark = pytest.mark.skipif(
    not _PROJECT_ROOT.is_dir(),
    reason="v6.2 Synthetic Acceptance Project is an explicit external Golden input",
)


def _evidence():
    adapter = ProjectEngineeringEvidenceAdapter(lambda _project_id: _PROJECT_ROOT)
    request = EngineeringContextRequest(
        project_id="v62-golden",
        tenant_id="tenant-golden",
        task="debug HardFault I2C timeout",
        constraints=(),
    )
    return adapter.list_evidence(request)


def test_external_project_projects_real_domain_evidence() -> None:
    values = _evidence()
    by_type = {source_type: tuple(item for item in values if item.source_type is source_type) for source_type in ContextSourceType}

    assert any(item.fact_value == "0x68" for item in by_type[ContextSourceType.HARDWARE])
    assert any(item.fact_value == "0x69" for item in by_type[ContextSourceType.FIRMWARE_SOURCE])
    assert any(item.fact_value == "I2C_SCL -> PB8" for item in by_type[ContextSourceType.PCB])
    assert any(item.fact_value == "NON_COMPLIANCE_CANDIDATE" for item in by_type[ContextSourceType.REQUIREMENT])
    assert {item.fact_value for item in by_type[ContextSourceType.ARCHITECTURE]} >= {"SensorDriver", "HAL_I2C", "I2C1"}
    conflict = next(item for item in by_type[ContextSourceType.HARDWARE] if item.fact_key == "address_conflict")
    assert {"firmware-sensor-address", "hardware-default_address"}.issubset(conflict.provenance)


def test_external_project_closes_hardfault_mapping_and_cross_evidence() -> None:
    values = _evidence()
    firmware = tuple(item for item in values if item.source_type is ContextSourceType.FIRMWARE_SOURCE)
    debug = tuple(item for item in values if item.source_type is ContextSourceType.DEBUG)

    mapping = next(item for item in debug if item.fact_key == "source_mapping")
    assert mapping.fact_value == "Core/Src/sensor.c:L3"
    assert mapping.verification_status.value == "VERIFIED"

    findings = EngineeringCorrelationService().correlate(firmware=firmware, debug=debug)
    assert len(findings) == 1
    assert findings[0].finding_type == "ROOT_CAUSE_CANDIDATE"
    assert findings[0].confidence < 1.0
    assert "runtime call trace" in findings[0].missing_evidence
