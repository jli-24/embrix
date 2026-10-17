from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from embedded_copilot.application import (
    FakeApplicationContext,
    FakeApplicationModelGateway,
    ModelPolicy,
)
from embedded_copilot.multimodal_engineering import (
    FakeMultimodalApplicationAuditSink,
    FakeMultimodalProvider,
    FixedMultimodalClock,
    MultimodalApplicationAuditEventType,
    MultimodalApplicationDependencies,
    MultimodalApplicationErrorCode,
    MultimodalApplicationRequest,
    MultimodalApplicationRoute,
    MultimodalApplicationRuntime,
    MultimodalApplicationStatus,
    MultimodalAuditConflict,
    MultimodalAuditUnavailable,
    MultimodalEngineeringService,
)

from .fixtures import NOW, artifact, base_context, question


def _runtime(*, context=None, model=None, provider=None, audit=None):
    audit_sink = audit or FakeMultimodalApplicationAuditSink()
    runtime = MultimodalApplicationRuntime(
        MultimodalApplicationDependencies(
            multimodal_service=MultimodalEngineeringService(
                provider=provider or FakeMultimodalProvider()
            ),
            context_provider=(
                FakeApplicationContext(base_context()) if context is None else context
            ),
            model_gateway=(FakeApplicationModelGateway() if model is None else model),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
            audit_sink=audit_sink,
            clock=FixedMultimodalClock(NOW + timedelta(seconds=1)),
        )
    )
    return runtime, audit_sink


def _request() -> MultimodalApplicationRequest:
    return MultimodalApplicationRequest(question=question(), artifact=artifact())


def test_overlay_runtime_completes_vision_context_answer_and_audit() -> None:
    runtime, audit = _runtime()
    result = runtime.answer(_request())
    assert result.route is MultimodalApplicationRoute.VISION
    assert result.status is MultimodalApplicationStatus.COMPLETED
    assert result.observation is not None
    assert result.context_fingerprint is not None
    assert result.answer is not None
    assert result.answer.evidence_refs
    assert (
        result.audit.event_type
        is MultimodalApplicationAuditEventType.VISION_ANALYSIS_COMPLETED
    )
    assert audit.get(result.audit.event_id) is not None


def test_overlay_runtime_supports_vision_only_context_and_async() -> None:
    runtime, audit = _runtime(context=FakeApplicationContext(None))
    result = asyncio.run(runtime.aanswer(_request()))
    assert result.status is MultimodalApplicationStatus.COMPLETED
    assert result.answer is not None
    assert audit.get(result.audit.event_id) is not None


def test_overlay_runtime_audits_provider_and_text_model_failures() -> None:
    provider = FakeMultimodalProvider(failure=RuntimeError("PRIVATE"))
    vision_runtime, vision_audit = _runtime(provider=provider)
    failed = vision_runtime.answer(_request())
    assert failed.status is MultimodalApplicationStatus.FAILED
    assert failed.error_code is MultimodalApplicationErrorCode.VISION_UNAVAILABLE
    assert vision_audit.get(failed.audit.event_id) is not None
    model_runtime, model_audit = _runtime(
        model=FakeApplicationModelGateway(failure=RuntimeError("PRIVATE"))
    )
    failed = model_runtime.answer(_request())
    assert failed.status is MultimodalApplicationStatus.FAILED
    assert failed.error_code is MultimodalApplicationErrorCode.ANSWER_UNAVAILABLE
    assert model_audit.get(failed.audit.event_id) is not None
    assert "PRIVATE" not in str(failed)


def test_overlay_runtime_maps_context_failure_without_private_details() -> None:
    class _BrokenContext:
        def get_context(self, request):
            raise RuntimeError("PRIVATE CONTEXT")

    runtime, audit = _runtime(context=_BrokenContext())
    failed = runtime.answer(_request())
    assert failed.status is MultimodalApplicationStatus.FAILED
    assert failed.error_code is MultimodalApplicationErrorCode.CONTEXT_UNAVAILABLE
    assert "PRIVATE CONTEXT" not in str(failed)
    assert audit.get(failed.audit.event_id) is not None


def test_audit_failure_prevents_result_release_and_identical_retry_is_idempotent() -> (
    None
):
    runtime, audit = _runtime()
    first = runtime.answer(_request())
    second = runtime.answer(_request())
    assert first == second
    assert len(audit.events) == 1
    broken, _ = _runtime(audit=FakeMultimodalApplicationAuditSink(fail_writes=True))
    with pytest.raises(MultimodalAuditUnavailable):
        broken.answer(_request())


def test_fake_audit_detects_same_identity_with_different_content() -> None:
    runtime, audit = _runtime()
    result = runtime.answer(_request())
    event = audit.get(result.audit.event_id)
    assert event is not None
    material = event.model_dump(mode="python")
    material["timestamp"] = material["timestamp"] + timedelta(seconds=1)
    material["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(MultimodalAuditConflict):
        audit.append(type(event).model_validate(material))


def test_tampered_audit_return_prevents_answer_release() -> None:
    class _TamperingAudit(FakeMultimodalApplicationAuditSink):
        def append(self, event):
            saved = super().append(event)
            material = saved.model_dump(mode="python")
            material["answer_fingerprint"] = "sha256:" + "f" * 64
            return saved.model_construct(**material)

    runtime, _ = _runtime(audit=_TamperingAudit())
    with pytest.raises(MultimodalAuditUnavailable):
        runtime.answer(_request())


def test_async_cancellation_creates_no_audit() -> None:
    audit = FakeMultimodalApplicationAuditSink()
    runtime, _ = _runtime(
        provider=FakeMultimodalProvider(cancel_async=True), audit=audit
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.aanswer(_request()))
    assert audit.events == ()
