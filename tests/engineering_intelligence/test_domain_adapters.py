from __future__ import annotations

from embedded_copilot.engineering_context import ContextSourceType, EvidenceTrustLevel
from embedded_copilot.engineering_intelligence.domain_adapters import (
    build_architecture_finding,
    evaluate_sample_rate,
    normalize_domain_evidence,
    project_hardware_metadata,
    project_multimodal_observation,
    project_pcb_connectivity,
)


def _firmware(value: str, evidence_id: str = "firmware-timing"):
    return normalize_domain_evidence(
        source_type=ContextSourceType.FIRMWARE_SOURCE,
        tenant_id="tenant-1",
        project_id="project-1",
        evidence_id=evidence_id,
        subject="SensorTask",
        fact_key="period",
        fact_value=value,
        summary="parser observed firmware timing",
        source_reference="workspace:firmware",
        source_fingerprint="sha256:" + "1" * 64,
        trust_level=EvidenceTrustLevel.VERIFIED_FACT,
    )


def test_requirement_adapter_deterministically_calculates_sample_rate() -> None:
    result = evaluate_sample_rate(requirement_id="req-sample", required_hz=100.0, firmware_evidence=(_firmware("20ms"),))
    assert result.status == "NON_COMPLIANCE_CANDIDATE"
    assert result.measured_value == 50.0
    assert result.evidence_ids == ("firmware-timing",)


def test_requirement_without_timing_evidence_is_insufficient() -> None:
    result = evaluate_sample_rate(requirement_id="req-sample", required_hz=100.0, firmware_evidence=())
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.measured_value is None


def test_architecture_adapter_uses_only_structured_evidence() -> None:
    value = normalize_domain_evidence(
        source_type=ContextSourceType.ARCHITECTURE,
        tenant_id="tenant-1",
        project_id="project-1",
        evidence_id="arch-1",
        subject="SensorTask",
        fact_key="depends_on",
        fact_value="SensorDriver",
        summary="documented static dependency",
        source_reference="workspace:architecture",
        source_fingerprint="sha256:" + "2" * 64,
    )
    finding = build_architecture_finding(evidence=(value,))
    assert finding.components == ("SensorTask",)
    assert finding.dependencies == ("SensorDriver",)


def test_hardware_and_pcb_projections_require_structured_records() -> None:
    hardware = project_hardware_metadata(
        tenant_id="tenant-1", project_id="project-1",
        records=({"evidence_id": "hw-1", "subject": "sensor", "fact_key": "voltage", "fact_value": "3.3V", "summary": "structured rail"},),
        source_reference="workspace:hardware", source_fingerprint="sha256:" + "3" * 64,
    )
    pcb = project_pcb_connectivity(
        tenant_id="tenant-1", project_id="project-1",
        connections=({"evidence_id": "pcb-1", "component": "U3", "net": "I2C_SCL", "pin": "PB8"},),
        source_reference="workspace:pcb", source_fingerprint="sha256:" + "4" * 64,
    )
    assert hardware.evidence[0].source_type is ContextSourceType.HARDWARE
    assert pcb.evidence[0].fact_value == "I2C_SCL -> PB8"


def test_multimodal_projection_cannot_be_verified_fact() -> None:
    projection = project_multimodal_observation(
        tenant_id="tenant-1", project_id="project-1", observation_id="vision-1",
        subject="waveform", summary="visible ACK candidate", source_reference="artifact:waveform-1",
        source_fingerprint="sha256:" + "5" * 64,
    )
    assert projection.evidence[0].evidence_status.value == "OBSERVED"
    assert projection.evidence[0].trust_level is EvidenceTrustLevel.OBSERVATION
