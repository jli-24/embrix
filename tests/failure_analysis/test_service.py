from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.execution import BuildResult, ExecutionStatus
from embedded_copilot.failure_analysis import (
    FailureAnalysisRejected,
    FailureAnalysisService,
    FailureObservation,
)
from embedded_copilot.model_gateway import ModelPolicy, ModelResponse


def _snapshot() -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="knowledge-1",
        project_id="project-1",
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="build",
        summary="Configured build evidence",
        source_reference="knowledge:1",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=0.8,
        verification_status="VERIFIED",
    )
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="analyze build failure",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(evidence,),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.8,
        provenance=(evidence.source(),),
        verification_status="VERIFIED",
    )


def _observation() -> FailureObservation:
    result = BuildResult.create(
        request_id="build-request-1",
        status=ExecutionStatus.FAILED,
        exit_code=2,
        artifact=None,
        logs_summary="build output redacted",
    )
    return FailureObservation.from_build(
        result, project_id="project-1", target_id="target-1"
    )


class _Context:
    def __init__(self, value: EngineeringContextSnapshot | None) -> None:
        self.value = value

    def get_context(self, request):
        return self.value


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return ModelResponse(
            content="Review configured build evidence.",
            model_name=request.model_policy.model_name,
            provider="local",
            provenance=request.engineering_context.provenance,
        )

    async def agenerate(self, request):
        return self.generate(request)


def test_context_first_analysis_is_stable_and_model_note_is_non_authoritative() -> None:
    model = _Model()
    service = FailureAnalysisService(
        context_provider=_Context(_snapshot()), model_gateway=model
    )
    request = EngineeringContextRequest(project_id="project-1", task="build failed")
    analysis = service.analyze(
        _observation(),
        request,
        model_policy=ModelPolicy(primary_provider="local", model_name="test-model"),
    )
    without_note = analysis.model_dump(mode="python")
    without_note["note"] = None
    assert analysis.note is not None
    assert model.calls == 1
    assert (
        analysis.fingerprint == type(analysis).model_validate(without_note).fingerprint
    )
    assert analysis.evidence_refs == ("knowledge-1",)


def test_missing_context_fails_closed_without_model_call() -> None:
    model = _Model()
    service = FailureAnalysisService(
        context_provider=_Context(None), model_gateway=model
    )
    with pytest.raises(FailureAnalysisRejected):
        service.analyze(
            _observation(),
            EngineeringContextRequest(project_id="project-1", task="build failed"),
            model_policy=ModelPolicy(primary_provider="local", model_name="test-model"),
        )
    assert model.calls == 0


def test_observation_does_not_store_result_log_content() -> None:
    observation = _observation()
    assert "logs_summary" not in type(observation).model_fields
    assert "redacted" not in str(observation.model_dump(mode="python"))
