from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_optimization import (
    OptimizationEvidenceReference,
    OptimizationEvidenceSourceType,
    OptimizationRequest,
    OptimizationScope,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def evidence() -> OptimizationEvidenceReference:
    return OptimizationEvidenceReference(
        evidence_id="evidence-1",
        project_id="project-1",
        source_type=OptimizationEvidenceSourceType.POWER_MEASUREMENT,
        source_reference="measurement-1",
        source_fingerprint="sha256:" + "1" * 64,
        created_at=NOW,
    )


def request() -> OptimizationRequest:
    return OptimizationRequest(
        request_id="request-1",
        project_id="project-1",
        evidence_refs=(evidence(),),
        optimization_scope=(OptimizationScope.POWER,),
        requester="engineer-1",
        created_at=NOW,
    )


def test_request_is_strict_frozen_and_deterministic():
    first, second = request(), request()
    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValidationError):
        first.project_id = "project-2"
    with pytest.raises(ValidationError):
        OptimizationRequest.model_validate(
            {**first.model_dump(mode="python"), "evidence_refs": [evidence()]}
        )


def test_evidence_rejects_unsafe_reference_and_project_mismatch():
    material = evidence().model_dump(mode="python")
    material["source_reference"] = "shell command"
    with pytest.raises(ValidationError):
        OptimizationEvidenceReference(**material)
    material = request().model_dump(mode="python")
    material["evidence_refs"] = (
        evidence().model_copy(update={"project_id": "project-2"}),
    )
    with pytest.raises(ValidationError):
        OptimizationRequest(**material)
