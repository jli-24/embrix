from __future__ import annotations

from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import (
    Artifact,
    ArtifactEvidence,
    DesignTruth,
    EngineeringEvidence,
    EngineeringEvidenceBundle,
    generate_teaching_context,
)


@pytest.mark.parametrize(
    "value",
    [
        object(),
        Artifact.model_construct(),
        ArtifactEvidence.model_construct(),
        EngineeringEvidence.model_construct(),
        EngineeringEvidenceBundle.model_construct(),
        DesignTruth.model_construct(),
    ],
)
def test_teaching_adapter_rejects_non_knowledge_inputs(value: object) -> None:
    with pytest.raises(TypeError):
        generate_teaching_context(value)  # type: ignore[arg-type]


def test_teaching_adapter_has_no_direct_side_effect_or_agent_dependency() -> None:
    root = Path(__file__).parents[2]
    source = (root / "src" / "embedded_copilot" / "engineering_copilot" / "teaching_service.py").read_text(encoding="utf-8")
    for forbidden in ("Artifact", "DesignTruth", "EvidenceCandidate", "Agent", "ModelGateway", "Executor", "KnowledgeGraph"):
        assert forbidden not in source
