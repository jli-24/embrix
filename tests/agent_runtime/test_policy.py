from __future__ import annotations

import pytest

from embedded_copilot.agent_runtime import (
    CapabilityPolicyGrant,
    CapabilityPolicyRejected,
    CapabilityType,
    StaticCapabilityPolicy,
    classify_request,
)

from .conftest import PROJECT, REQUESTER, knowledge_request


def grant(*capabilities: CapabilityType) -> CapabilityPolicyGrant:
    return CapabilityPolicyGrant(
        project_id=PROJECT,
        requester=REQUESTER,
        capabilities=capabilities,
    )


def test_exact_project_and_requester_grant_allows_capability() -> None:
    request = knowledge_request()
    decision = StaticCapabilityPolicy((grant(CapabilityType.KNOWLEDGE),)).evaluate(
        request, classify_request(request)
    )
    assert decision.allowed is True
    assert decision.capability is CapabilityType.KNOWLEDGE
    assert decision.grant_fingerprint is not None


def test_missing_grant_denies_by_default() -> None:
    request = knowledge_request()
    decision = StaticCapabilityPolicy(()).evaluate(request, classify_request(request))
    assert decision.allowed is False
    assert decision.grant_fingerprint is None


def test_project_isolation_denies_cross_project_request() -> None:
    request = knowledge_request()
    other = CapabilityPolicyGrant(
        project_id="project-2",
        requester=REQUESTER,
        capabilities=(CapabilityType.KNOWLEDGE,),
    )
    assert (
        not StaticCapabilityPolicy((other,))
        .evaluate(request, classify_request(request))
        .allowed
    )


def test_requester_isolation_denies_cross_requester_request() -> None:
    request = knowledge_request()
    other = CapabilityPolicyGrant(
        project_id=PROJECT,
        requester="engineer-2",
        capabilities=(CapabilityType.KNOWLEDGE,),
    )
    assert (
        not StaticCapabilityPolicy((other,))
        .evaluate(request, classify_request(request))
        .allowed
    )


def test_capability_allowlist_denies_unlisted_capability() -> None:
    request = knowledge_request()
    decision = StaticCapabilityPolicy((grant(CapabilityType.VISION),)).evaluate(
        request, classify_request(request)
    )
    assert decision.allowed is False
    assert decision.grant_fingerprint is not None


def test_duplicate_project_requester_grant_fails_closed() -> None:
    with pytest.raises(CapabilityPolicyRejected):
        StaticCapabilityPolicy(
            (
                grant(CapabilityType.KNOWLEDGE),
                grant(CapabilityType.VISION),
            )
        )


def test_policy_rejects_route_from_another_request() -> None:
    first = knowledge_request("What is UART?")
    second = knowledge_request("What is SPI?")
    with pytest.raises(CapabilityPolicyRejected):
        StaticCapabilityPolicy((grant(CapabilityType.KNOWLEDGE),)).evaluate(
            first, classify_request(second)
        )


def test_policy_has_no_wildcard_or_default_grant_api() -> None:
    assert not hasattr(StaticCapabilityPolicy, "allow_all")
    assert not hasattr(StaticCapabilityPolicy, "from_environment")
