from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import (
    ContextConflict,
    ContextSourceReference,
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)


def _evidence() -> EngineeringContextEvidence:
    return EngineeringContextEvidence.create(
        evidence_id="memory-1",
        project_id="project-1",
        source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="GPIO12",
        summary="GPIO12 is reserved",
        fact_key="gpio12.function",
        fact_value="reserved",
        source_reference="memory:1",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=0.9,
        verification_status="APPROVED",
    )


def test_context_contracts_are_frozen_strict_normalized_and_provenance_bound() -> None:
    evidence = _evidence()
    assert evidence.summary == "GPIO12 is reserved"
    assert EngineeringContextRequest(project_id="project-1", task="cafe\u0301").task == "café"
    with pytest.raises((TypeError, ValidationError)):
        evidence.summary = "changed"
    with pytest.raises(ValidationError):
        EngineeringContextEvidence.model_validate(
            {**evidence.model_dump(), "extra": "x"}
        )
    with pytest.raises(ValidationError):
        EngineeringContextEvidence.model_validate(
            {**evidence.model_dump(), "summary": "tampered"}
        )


def test_nested_values_are_deep_copied_and_collections_are_tuple_only() -> None:
    evidence = _evidence()
    raw = evidence.model_dump(mode="python")
    checked = EngineeringContextEvidence.model_validate(copy.deepcopy(raw))
    assert checked == evidence
    with pytest.raises((TypeError, ValidationError)):
        EngineeringContextRequest.model_validate(
            {"project_id": "project-1", "task": "task", "constraints": ["x"]}
        )


def test_snapshot_and_conflict_fingerprints_are_stable_and_bound() -> None:
    source = ContextSourceReference.create(
        source_type=ContextSourceType.ENGINEERING_MEMORY,
        source_id="memory-1",
        source_reference="memory:1",
        source_fingerprint=_evidence().fingerprint,
        verification_status="APPROVED",
        confidence=0.9,
    )
    conflict = ContextConflict.create(
        project_id="project-1",
        source_a="memory-1",
        source_b="graph-1",
        reason="explicit conflicting fact values",
    )
    values = {
        "project_id": "project-1",
        "task_description": "GPIO12",
        "memory_evidence": (_evidence(),),
        "graph_evidence": (),
        "knowledge_evidence": (),
        "multimodal_evidence": (),
        "conflicts": (conflict,),
        "confidence": 0.9,
        "provenance": (source,),
        "verification_status": "REQUIRES_REVIEW",
    }
    snapshot = EngineeringContextSnapshot.create(**values)
    assert snapshot.fingerprint.startswith("sha256:")
    assert snapshot.context_id.startswith("context-")
    with pytest.raises(ValidationError):
        EngineeringContextSnapshot.model_validate(
            {**snapshot.model_dump(), "context_id": "context-2"}
        )
