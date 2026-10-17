from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
)


class EvidenceGateRejected(ValueError):
    """Evidence failed the application trust boundary."""


@dataclass(frozen=True, slots=True)
class EvidenceGateResult:
    evidence: tuple[EngineeringContextEvidence, ...]
    evidence_count: int
    fingerprint: str


class EvidenceGate:
    """Validate and normalize internal evidence before ModelGateway use."""

    def validate(self, evidence: tuple[EngineeringContextEvidence, ...], *, tenant_id: str, project_id: str) -> EvidenceGateResult:
        if not isinstance(evidence, tuple):
            raise EvidenceGateRejected("evidence collection must be a tuple")
        checked: list[EngineeringContextEvidence] = []
        seen: set[str] = set()
        for value in evidence:
            try:
                if type(value) is not EngineeringContextEvidence:
                    raise TypeError("evidence must be a typed projection")
                item = EngineeringContextEvidence.model_validate(copy.deepcopy(value.model_dump(mode="python")))
            except Exception as error:
                raise EvidenceGateRejected("evidence validation failed") from error
            if item.tenant_id != tenant_id or item.project_id != project_id:
                raise EvidenceGateRejected("evidence identity mismatch")
            if item.evidence_id in seen:
                raise EvidenceGateRejected("duplicate evidence id")
            if not item.source_reference or not item.source_fingerprint or not item.provenance:
                raise EvidenceGateRejected("evidence provenance is incomplete")
            seen.add(item.evidence_id)
            checked.append(item)
        ordered = tuple(sorted(checked, key=lambda item: item.evidence_id))
        material = "|".join(item.fingerprint for item in ordered)
        return EvidenceGateResult(
            evidence=ordered,
            evidence_count=len(ordered),
            fingerprint="sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest(),
        )

    def require(self, evidence: tuple[EngineeringContextEvidence, ...], *, tenant_id: str, project_id: str) -> EvidenceGateResult:
        result = self.validate(evidence, tenant_id=tenant_id, project_id=project_id)
        current_evidence_count = sum(
            item.source_type not in {ContextSourceType.KNOWLEDGE_EVOLUTION}
            and not (
                item.source_type is ContextSourceType.ENGINEERING_MEMORY
                and bool(item.provenance)
            )
            for item in result.evidence
        )
        if current_evidence_count == 0:
            raise EvidenceGateRejected("insufficient evidence")
        return result


__all__ = ("EvidenceGate", "EvidenceGateRejected", "EvidenceGateResult")
