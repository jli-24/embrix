from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)


@dataclass(frozen=True, slots=True)
class RequirementFinding:
    status: str
    requirement_id: str
    measured_value: float | None
    unit: str | None
    evidence_ids: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchitectureFinding:
    components: tuple[str, ...]
    dependencies: tuple[str, ...]
    interfaces: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DomainEvidenceProjection:
    source_type: ContextSourceType
    evidence: tuple[EngineeringContextEvidence, ...]
    limitations: tuple[str, ...] = ()


def normalize_domain_evidence(
    *,
    source_type: ContextSourceType,
    tenant_id: str,
    project_id: str,
    evidence_id: str,
    subject: str,
    fact_key: str,
    fact_value: str,
    summary: str,
    source_reference: str,
    source_fingerprint: str,
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.OBSERVATION,
    verification_status: ContextVerificationStatus = ContextVerificationStatus.VERIFIED,
    evidence_status: EvidenceVerificationStatus = EvidenceVerificationStatus.OBSERVED,
    provenance: tuple[str, ...] = (),
) -> EngineeringContextEvidence:
    if not provenance:
        provenance = (evidence_id,)
    return EngineeringContextEvidence.create(
        evidence_id=evidence_id,
        tenant_id=tenant_id,
        project_id=project_id,
        source_type=source_type,
        subject=subject,
        fact_key=fact_key,
        fact_value=fact_value,
        summary=summary,
        source_reference=source_reference,
        source_fingerprint=source_fingerprint,
        trust_level=trust_level,
        verification_status=verification_status,
        evidence_status=evidence_status,
        provenance=provenance,
        confidence=1.0 if trust_level is EvidenceTrustLevel.VERIFIED_FACT else 0.7,
    )


def evaluate_sample_rate(
    *,
    requirement_id: str,
    required_hz: float,
    firmware_evidence: tuple[EngineeringContextEvidence, ...],
) -> RequirementFinding:
    for item in firmware_evidence:
        match = re.search(r"(?:period|interval)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*ms", item.fact_value or "", re.IGNORECASE)
        if match:
            period_ms = float(match.group(1))
            rate = 1000.0 / period_ms
            return RequirementFinding(
                status="COMPLIANT" if rate >= required_hz else "NON_COMPLIANCE_CANDIDATE",
                requirement_id=requirement_id,
                measured_value=rate,
                unit="Hz",
                evidence_ids=(item.evidence_id,),
                limitations=("static firmware timing evidence is not runtime measurement",),
            )
    return RequirementFinding(
        status="INSUFFICIENT_EVIDENCE",
        requirement_id=requirement_id,
        measured_value=None,
        unit=None,
        evidence_ids=(),
        limitations=("firmware timing evidence is missing",),
    )


def build_architecture_finding(
    *,
    evidence: tuple[EngineeringContextEvidence, ...],
) -> ArchitectureFinding:
    components = tuple(sorted({item.subject for item in evidence if item.subject}))
    dependencies = tuple(sorted({item.fact_value for item in evidence if item.fact_key in {"calls", "depends_on"} and item.fact_value}))
    interfaces = tuple(sorted({item.fact_value for item in evidence if item.fact_key in {"interface", "peripheral"} and item.fact_value}))
    return ArchitectureFinding(
        components=components,
        dependencies=dependencies,
        interfaces=interfaces,
        risks=("graph is static evidence and does not prove runtime scheduling",) if evidence else ("architecture evidence is unavailable",),
        evidence_ids=tuple(item.evidence_id for item in evidence),
    )


def project_hardware_metadata(
    *,
    tenant_id: str,
    project_id: str,
    records: tuple[dict[str, Any], ...],
    source_reference: str,
    source_fingerprint: str,
) -> DomainEvidenceProjection:
    if not isinstance(records, tuple):
        raise TypeError("hardware records must be a tuple")
    evidence = tuple(
        normalize_domain_evidence(
            source_type=ContextSourceType.HARDWARE,
            tenant_id=tenant_id,
            project_id=project_id,
            evidence_id=str(record["evidence_id"]),
            subject=str(record["subject"]),
            fact_key=str(record["fact_key"]),
            fact_value=str(record["fact_value"]),
            summary=str(record["summary"]),
            source_reference=source_reference,
            source_fingerprint=source_fingerprint,
            provenance=(str(record["evidence_id"]),),
        )
        for record in records
        if isinstance(record, dict)
        and {"evidence_id", "subject", "fact_key", "fact_value", "summary"}.issubset(record)
    )
    if len(evidence) != len(records):
        raise ValueError("hardware records are malformed")
    return DomainEvidenceProjection(ContextSourceType.HARDWARE, evidence)


def project_pcb_connectivity(
    *,
    tenant_id: str,
    project_id: str,
    connections: tuple[dict[str, Any], ...],
    source_reference: str,
    source_fingerprint: str,
) -> DomainEvidenceProjection:
    if not isinstance(connections, tuple):
        raise TypeError("PCB connections must be a tuple")
    evidence = tuple(
        normalize_domain_evidence(
            source_type=ContextSourceType.PCB,
            tenant_id=tenant_id,
            project_id=project_id,
            evidence_id=str(record["evidence_id"]),
            subject=str(record["component"]),
            fact_key="connectivity",
            fact_value=f"{record['net']} -> {record['pin']}",
            summary=f"{record['component']} pin {record['pin']} is connected to {record['net']}",
            source_reference=source_reference,
            source_fingerprint=source_fingerprint,
            provenance=(str(record["evidence_id"]),),
        )
        for record in connections
        if isinstance(record, dict) and {"evidence_id", "component", "net", "pin"}.issubset(record)
    )
    if len(evidence) != len(connections):
        raise ValueError("PCB connectivity is malformed")
    return DomainEvidenceProjection(ContextSourceType.PCB, evidence)


def project_multimodal_observation(
    *,
    tenant_id: str,
    project_id: str,
    observation_id: str,
    subject: str,
    summary: str,
    source_reference: str,
    source_fingerprint: str,
) -> DomainEvidenceProjection:
    return DomainEvidenceProjection(
        ContextSourceType.VISION_OBSERVATION,
        (normalize_domain_evidence(
            source_type=ContextSourceType.VISION_OBSERVATION,
            tenant_id=tenant_id,
            project_id=project_id,
            evidence_id=observation_id,
            subject=subject,
            fact_key="observation",
            fact_value=summary,
            summary=summary,
            source_reference=source_reference,
            source_fingerprint=source_fingerprint,
            trust_level=EvidenceTrustLevel.OBSERVATION,
            verification_status=ContextVerificationStatus.UNVERIFIED,
            evidence_status=EvidenceVerificationStatus.OBSERVED,
            provenance=(observation_id,),
        ),),
        limitations=("vision observation is not a verified engineering fact",),
    )


__all__ = (
    "ArchitectureFinding",
    "DomainEvidenceProjection",
    "RequirementFinding",
    "build_architecture_finding",
    "evaluate_sample_rate",
    "normalize_domain_evidence",
    "project_hardware_metadata",
    "project_multimodal_observation",
    "project_pcb_connectivity",
)
