from embedded_copilot.model_gateway.resilience import (
    FallbackTrace,
    ModelErrorCode,
    RetryPolicy,
)


def test_retry_is_bounded_and_distinct_from_fallback() -> None:
    policy = RetryPolicy(max_attempts=2)
    assert policy.should_retry(ModelErrorCode.TIMEOUT, 0)
    assert not policy.should_retry(ModelErrorCode.TIMEOUT, 1)
    assert not policy.should_retry(ModelErrorCode.AUTHENTICATION_FAILED, 0)

    trace = FallbackTrace(requested_model="model-a", selected_model="model-a")
    fallback = trace.with_fallback(to_model="model-b", reason=ModelErrorCode.PROVIDER_UNAVAILABLE)
    assert fallback.fallback_used is True
    assert fallback.fallback_from == "model-a"
    assert fallback.fallback_to == "model-b"
    assert fallback.retry_count == 0
