from __future__ import annotations

import asyncio
import threading

import pytest

from embedded_copilot.agent_runtime import (
    AgentCapabilityRequest,
    CapabilityAsyncMode,
    CapabilityAuditEventType,
    CapabilityAuditUnavailable,
    CapabilityBindingReference,
    CapabilityErrorCode,
    CapabilityPolicyGrant,
    CapabilityResultStatus,
    CapabilityRouter,
    CapabilityType,
    KnowledgeInput,
    StaticCapabilityPolicy,
    classify_request,
)
from embedded_copilot.agent_runtime.adapters import (
    FakeCapabilityAuditSink,
    FakeCapabilityBinding,
    FakeCapabilityPolicy,
    FakeCapabilityRegistry,
    FixedCapabilityClock,
)

from .conftest import (
    NOW,
    PROJECT,
    REQUESTER,
    application_request,
    knowledge_output,
    knowledge_request,
    knowledge_router,
)


@pytest.mark.parametrize(
    ("question", "capability"),
    [
        ("error: HardFault backtrace", CapabilityType.DEBUG_ANALYSIS),
        ("repair this failure", CapabilityType.REPAIR_ANALYSIS),
        ("optimize the power trade-off", CapabilityType.OPTIMIZATION_ANALYSIS),
        ("inspect PCB netlist", CapabilityType.PCB_ANALYSIS),
        ("analyze this image screenshot", CapabilityType.VISION),
        ("explain this STM32 HAL firmware driver", CapabilityType.FIRMWARE_ANALYSIS),
        ("what is this datasheet register?", CapabilityType.KNOWLEDGE),
    ],
)
def test_classifier_recognizes_all_seven_capabilities(
    question: str, capability: CapabilityType
) -> None:
    decision = classify_request(
        AgentCapabilityRequest(
            application_request=application_request(question),
            payload=KnowledgeInput(),
        )
    )
    assert decision.selected_capability is capability


def test_classifier_uses_fixed_priority_for_multiple_matches() -> None:
    decision = classify_request(
        AgentCapabilityRequest(
            application_request=application_request(
                "debug log: repair and optimize PCB image firmware; explain it"
            ),
            payload=KnowledgeInput(),
        )
    )
    assert decision.candidates == (
        CapabilityType.DEBUG_ANALYSIS,
        CapabilityType.REPAIR_ANALYSIS,
        CapabilityType.OPTIMIZATION_ANALYSIS,
        CapabilityType.PCB_ANALYSIS,
        CapabilityType.VISION,
        CapabilityType.FIRMWARE_ANALYSIS,
        CapabilityType.KNOWLEDGE,
    )
    assert decision.selected_capability is CapabilityType.DEBUG_ANALYSIS


def test_classifier_normalizes_case_whitespace_and_chinese_markers() -> None:
    request = AgentCapabilityRequest(
        application_request=application_request("  功耗优化   PCB  "),
        payload=KnowledgeInput(),
    )
    assert (
        classify_request(request).selected_capability
        is CapabilityType.OPTIMIZATION_ANALYSIS
    )


def test_zero_match_returns_audited_typed_rejection_without_binding_call() -> None:
    router, binding, audit = knowledge_router()
    result = router.invoke(knowledge_request("hello there"))
    assert result.status is CapabilityResultStatus.REJECTED
    assert result.error_code is CapabilityErrorCode.ROUTE_NOT_FOUND
    assert binding.call_count == 0
    assert audit.events[0].event_type is CapabilityAuditEventType.CAPABILITY_REJECTED


def test_route_payload_mismatch_returns_rejection_without_binding_call() -> None:
    router, binding, _ = knowledge_router()
    result = router.invoke(knowledge_request("analyze this image"))
    assert result.status is CapabilityResultStatus.REJECTED
    assert result.error_code is CapabilityErrorCode.PAYLOAD_ROUTE_MISMATCH
    assert binding.call_count == 0


def test_policy_denial_returns_rejection_without_binding_call() -> None:
    policy = FakeCapabilityPolicy(
        (
            CapabilityPolicyGrant(
                project_id=PROJECT,
                requester=REQUESTER,
                capabilities=(CapabilityType.VISION,),
            ),
        )
    )
    router, binding, _ = knowledge_router(policy=policy)
    result = router.invoke(knowledge_request())
    assert result.status is CapabilityResultStatus.REJECTED
    assert result.error_code is CapabilityErrorCode.POLICY_DENIED
    assert binding.call_count == 0


def test_sync_completion_returns_typed_output_and_mandatory_audit() -> None:
    router, binding, audit = knowledge_router()
    result = router.invoke(knowledge_request())
    assert result.status is CapabilityResultStatus.COMPLETED
    assert result.output == knowledge_output()
    assert result.audit.fingerprint == audit.events[0].fingerprint
    assert binding.call_count == 1


def test_identical_retry_reuses_deterministic_audit_event() -> None:
    router, binding, audit = knowledge_router()
    first = router.invoke(knowledge_request())
    second = router.invoke(knowledge_request())
    assert first == second
    assert binding.call_count == 2
    assert len(audit.events) == 1


def test_binding_rejection_is_sanitized_and_audited() -> None:
    binding = FakeCapabilityBinding(
        capability=CapabilityType.KNOWLEDGE,
        output=knowledge_output(),
        failure=ValueError("PRIVATE_TOKEN"),
    )
    router, _, audit = knowledge_router(binding=binding)
    result = router.invoke(knowledge_request())
    assert result.status is CapabilityResultStatus.FAILED
    assert result.error_code is CapabilityErrorCode.BINDING_FAILED
    assert "PRIVATE_TOKEN" not in str(result)
    assert audit.events[0].event_type is CapabilityAuditEventType.CAPABILITY_FAILED


def test_audit_write_failure_prevents_result_release() -> None:
    router, _, _ = knowledge_router(audit=FakeCapabilityAuditSink(fail_writes=True))
    with pytest.raises(CapabilityAuditUnavailable):
        router.invoke(knowledge_request())


def test_async_native_completion_matches_sync_semantics() -> None:
    sync_router, _, _ = knowledge_router()
    async_binding = FakeCapabilityBinding(
        capability=CapabilityType.KNOWLEDGE,
        output=knowledge_output(),
        async_mode=CapabilityAsyncMode.NATIVE_ASYNC,
    )
    async_router, _, _ = knowledge_router(binding=async_binding)
    sync_result = sync_router.invoke(knowledge_request())
    async_result = asyncio.run(async_router.ainvoke(knowledge_request()))
    assert async_result.status is sync_result.status
    assert async_result.output == sync_result.output
    assert async_result.error_code is sync_result.error_code
    assert async_result.route == sync_result.route


def test_native_async_cancellation_is_audited_and_propagated() -> None:
    binding = FakeCapabilityBinding(
        capability=CapabilityType.KNOWLEDGE,
        output=knowledge_output(),
        async_mode=CapabilityAsyncMode.NATIVE_ASYNC,
        cancel_async=True,
    )
    router, _, audit = knowledge_router(binding=binding)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(router.ainvoke(knowledge_request()))
    assert audit.events[0].event_type is CapabilityAuditEventType.CAPABILITY_CANCELLED
    assert audit.events[0].output_fingerprint is None


def test_thread_bridge_waits_for_output_then_audits_cancellation() -> None:
    started = threading.Event()
    release = threading.Event()
    output = knowledge_output()

    class BlockingBinding:
        @property
        def reference(self):
            return CapabilityBindingReference(
                capability=CapabilityType.KNOWLEDGE,
                binding_id="blocking-knowledge",
                binding_version="v4.3.2",
                async_mode=CapabilityAsyncMode.THREAD_BRIDGE,
            )

        def invoke(self, request):
            started.set()
            if not release.wait(timeout=2):
                raise RuntimeError("worker timeout")
            return output

        async def ainvoke(self, request):
            return await asyncio.to_thread(self.invoke, request)

    grant = CapabilityPolicyGrant(
        project_id=PROJECT,
        requester=REQUESTER,
        capabilities=(CapabilityType.KNOWLEDGE,),
    )
    audit = FakeCapabilityAuditSink()
    router = CapabilityRouter(
        policy=StaticCapabilityPolicy((grant,)),
        registry=FakeCapabilityRegistry((BlockingBinding(),)),
        audit_sink=audit,
        clock=FixedCapabilityClock(NOW),
    )

    async def run() -> None:
        task = asyncio.create_task(router.ainvoke(knowledge_request()))
        assert await asyncio.to_thread(started.wait, 1)
        asyncio.get_running_loop().call_later(0.05, release.set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert audit.events[0].event_type is CapabilityAuditEventType.CAPABILITY_CANCELLED
    assert audit.events[0].output_fingerprint == output.fingerprint


def test_router_rejects_non_request_object_at_boundary() -> None:
    router, _, _ = knowledge_router()
    with pytest.raises(TypeError):
        router.invoke(object())  # type: ignore[arg-type]
