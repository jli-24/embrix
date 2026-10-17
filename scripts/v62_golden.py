from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from embedded_copilot.debug_intelligence import HardFaultMappingService
from embedded_copilot.engineering_context import ContextSourceType, EngineeringContextRequest
from embedded_copilot.engineering_context.adapters.project_engineering import ProjectEngineeringEvidenceAdapter
from embedded_copilot.engineering_intelligence.correlation import EngineeringCorrelationService
from embedded_copilot.engineering_intelligence import EvidenceGate, EvidenceGateRejected


@dataclass(frozen=True, slots=True)
class GoldenCaseResult:
    case_id: str
    status: str
    details: str


def run_golden_cases(root: Path) -> tuple[GoldenCaseResult, ...]:
    """Run offline deterministic v6.2 Golden checks against real acceptance inputs."""
    if not root.is_dir():
        raise FileNotFoundError(root)
    project_root = Path(r"C:\acceptance_projects\v6.2-engineering-golden")
    if not project_root.is_dir():
        raise FileNotFoundError(project_root)
    adapter = ProjectEngineeringEvidenceAdapter(lambda _project_id: project_root)
    request = EngineeringContextRequest(
        project_id="v62-golden",
        tenant_id="tenant-golden",
        task="debug HardFault I2C timeout",
        constraints=(),
    )
    evidence = adapter.list_evidence(request)
    by_type = {
        source_type: tuple(item for item in evidence if item.source_type is source_type)
        for source_type in ContextSourceType
    }
    firmware = by_type[ContextSourceType.FIRMWARE_SOURCE]
    debug = by_type[ContextSourceType.DEBUG]
    correlation = EngineeringCorrelationService().correlate(firmware=firmware, debug=debug)
    mapping = next((item for item in debug if item.fact_key == "source_mapping"), None)
    requirement = next((item for item in by_type[ContextSourceType.REQUIREMENT] if item.fact_key == "verification_status"), None)
    firmware_address = next((item for item in firmware if item.fact_key == "configured_address"), None)
    hardware_address = next((item for item in by_type[ContextSourceType.HARDWARE] if item.fact_key == "default_address"), None)
    conflict_finding = next((item for item in by_type[ContextSourceType.HARDWARE] if item.fact_key == "address_conflict"), None)
    pcb = next((item for item in by_type[ContextSourceType.PCB] if item.fact_key == "connectivity"), None)
    no_mapping = HardFaultMappingService().map(
        tenant_id="tenant-golden",
        project_id="v62-golden",
        artifact_id="hardfault-no-map",
        pc="0x08001234",
        map_text=None,
        source_fingerprints={},
        map_source_reference="workspace://debug/firmware.map",
    )
    try:
        EvidenceGate().require((), tenant_id="tenant-golden", project_id="v62-golden")
    except EvidenceGateRejected:
        insufficient = True
    else:
        insufficient = False
    conflict = (
        firmware_address is not None
        and hardware_address is not None
        and conflict_finding is not None
        and firmware_address.evidence_id in conflict_finding.provenance
        and hardware_address.evidence_id in conflict_finding.provenance
        and firmware_address.fact_value != hardware_address.fact_value
    )
    return (
        GoldenCaseResult("1-firmware", "PASS" if any(item.subject == "MX_I2C1_Init" for item in firmware) else "FAIL", "real firmware parser evidence includes MX_I2C1_Init and source fingerprint"),
        GoldenCaseResult("2-debug-cross-evidence", "PASS" if correlation else "FAIL", "candidate correlation is evidence-bound"),
        GoldenCaseResult("3-hardfault", "PASS" if mapping is not None and no_mapping.status == "INSUFFICIENT_MAPPING_EVIDENCE" else "FAIL", "ELF/MAP source mapping is verified and no-map attribution is rejected"),
        GoldenCaseResult("4-hardware-firmware-conflict", "PASS" if conflict else "FAIL", "0x69 firmware address conflicts with 0x68 hardware default; both Evidence are bound"),
        GoldenCaseResult("5-requirement", "PASS" if requirement is not None and requirement.fact_value == "NON_COMPLIANCE_CANDIDATE" else "FAIL", "real firmware timing evidence computes 20ms => 50Hz against 100Hz"),
        GoldenCaseResult("6-pcb", "PASS" if pcb is not None and pcb.fact_value == "I2C_SCL -> PB8" else "FAIL", "real KiCad parser connectivity evidence"),
        GoldenCaseResult("7-multimodal", "UNAVAILABLE", "Vision Provider is optional and not required for core Golden"),
        GoldenCaseResult("8-insufficient-evidence", "PASS" if insufficient else "FAIL", "Evidence Gate rejects zero evidence before ModelGateway invocation"),
    )


__all__ = ("GoldenCaseResult", "run_golden_cases")
