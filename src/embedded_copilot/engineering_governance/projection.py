from __future__ import annotations

import copy
import hashlib
import re

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)

from .exceptions import GovernanceRejected
from .models import CandidateSourceType, KnowledgeCandidate

_OBSERVED_PATTERN = re.compile(
    r"^observed\s+(component|interface):\s*(.+)$", re.IGNORECASE
)


def _source_type(value: ContextSourceType) -> CandidateSourceType:
    mapping = {
        ContextSourceType.ENGINEERING_MEMORY: CandidateSourceType.MEMORY,
        ContextSourceType.KNOWLEDGE_GRAPH: CandidateSourceType.GRAPH,
        ContextSourceType.KNOWLEDGE_GATEWAY: CandidateSourceType.RETRIEVAL,
        ContextSourceType.KNOWLEDGE_EVOLUTION: CandidateSourceType.RETRIEVAL,
        ContextSourceType.DATASHEET_METADATA: CandidateSourceType.RETRIEVAL,
        ContextSourceType.MULTIMODAL_OBSERVATION: CandidateSourceType.MULTIMODAL,
    }
    try:
        return mapping[value]
    except KeyError as error:
        raise GovernanceRejected() from error


def _claim(evidence: EngineeringContextEvidence) -> str:
    match = _OBSERVED_PATTERN.fullmatch(evidence.summary)
    if match is None:
        return evidence.summary
    category, value = match.groups()
    suffix = (
        "component observed"
        if category.casefold() == "component"
        else "interface detected"
    )
    return f"{value.strip()} {suffix}"


def _candidate_id(snapshot: EngineeringContextSnapshot, kind: str, item_id: str) -> str:
    material = f"{snapshot.project_id}:{snapshot.context_id}:{kind}:{item_id}".encode()
    return "candidate-" + hashlib.sha256(material).hexdigest()[:48]


def _confidence(
    evidence: EngineeringContextEvidence, snapshot: EngineeringContextSnapshot
) -> float:
    return snapshot.confidence if evidence.confidence is None else evidence.confidence


class KnowledgeCandidateProjectionService:
    """Project only explicit immutable Context evidence into candidates."""

    def project(
        self, snapshot: EngineeringContextSnapshot
    ) -> tuple[KnowledgeCandidate, ...]:
        try:
            checked = EngineeringContextSnapshot.model_validate(
                copy.deepcopy(snapshot.model_dump(mode="python"))
            )
        except Exception as error:
            raise GovernanceRejected() from error

        groups = (
            checked.memory_evidence,
            checked.graph_evidence,
            checked.knowledge_evidence,
            checked.multimodal_evidence,
        )
        evidence_by_id: dict[str, EngineeringContextEvidence] = {}
        candidates: list[KnowledgeCandidate] = []
        try:
            for values in groups:
                for evidence in sorted(values, key=lambda item: item.evidence_id):
                    if evidence.evidence_id in evidence_by_id:
                        raise ValueError("duplicate context evidence id")
                    evidence_by_id[evidence.evidence_id] = evidence
                    candidates.append(
                        KnowledgeCandidate.create(
                            candidate_id=_candidate_id(
                                checked, "evidence", evidence.evidence_id
                            ),
                            project_id=checked.project_id,
                            claim=_claim(evidence),
                            source_type=_source_type(evidence.source_type),
                            evidence_refs=(evidence.source_reference,),
                            confidence=_confidence(evidence, checked),
                            status="PENDING",
                        )
                    )

            for conflict in sorted(
                checked.conflicts, key=lambda item: item.conflict_id
            ):
                source_a = evidence_by_id.get(conflict.source_a)
                source_b = evidence_by_id.get(conflict.source_b)
                if source_a is None or source_b is None:
                    raise ValueError("conflict references unknown evidence")
                type_a = _source_type(source_a.source_type)
                type_b = _source_type(source_b.source_type)
                source_type = (
                    type_a if type_a is type_b else CandidateSourceType.MULTIMODAL
                )
                is_interface = any(
                    (item.fact_key or "").casefold().endswith(".interface")
                    for item in (source_a, source_b)
                )
                claim = (
                    "Interface evidence conflict detected"
                    if is_interface
                    else "Engineering evidence conflict detected"
                )
                confidence_values = tuple(
                    item.confidence
                    for item in (source_a, source_b)
                    if item.confidence is not None
                )
                candidates.append(
                    KnowledgeCandidate.create(
                        candidate_id=_candidate_id(
                            checked, "conflict", conflict.conflict_id
                        ),
                        project_id=checked.project_id,
                        claim=claim,
                        source_type=source_type,
                        evidence_refs=(
                            source_a.source_reference,
                            source_b.source_reference,
                        ),
                        confidence=min(confidence_values, default=checked.confidence),
                        status="PENDING",
                    )
                )
        except GovernanceRejected:
            raise
        except Exception as error:
            raise GovernanceRejected() from error
        return tuple(candidates)


__all__ = ("KnowledgeCandidateProjectionService",)
