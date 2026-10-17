"""Synthetic offline proof for the actual no-send and sync/async transport paths."""

import sqlite3

import pytest
from pydantic import SecretStr

from embedded_copilot.model_gateway.invocation.budget import digest
from tests.model_gateway.test_counter_bundle_integration import chain, run


@pytest.mark.parametrize("asynchronous", [False, True])
def test_no_send_preparation_matches_actual_bound_transport(
    tmp_path, monkeypatch, asynchronous
):
    service, store, request, authority, events = chain(tmp_path, monkeypatch)
    request = type(request).model_validate(
        {
            **request.model_dump(mode="python"),
            "task_type": "HARDWARE_SELECTION_PROPOSAL",
        }
    )
    original = SecretStr.get_secret_value

    def reject_secret(_self):
        raise AssertionError("preview must not retrieve a credential value")

    monkeypatch.setattr(SecretStr, "get_secret_value", reject_secret)
    context = service.prepare_consumption_context(request, authority_source=authority)
    assert events == []
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_attempts"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM model_invocation_budget_claims"
            ).fetchone()[0]
            == 0
        )
    monkeypatch.setattr(SecretStr, "get_secret_value", original)
    result = run(service, request, authority, asynchronous)
    assert result.status.value == "SUCCESS"
    assert [path for path, _ in events] == ["responses/input_tokens", "responses"]
    assert events[-1][1] == context.final_transport_bytes
    assert digest(events[-1][1]) == context.final_transport_sha256


def test_preparation_rejects_stale_authority_without_attempt(tmp_path, monkeypatch):
    service, store, request, authority, events = chain(tmp_path, monkeypatch)
    request = type(request).model_validate(
        {
            **request.model_dump(mode="python"),
            "task_type": "HARDWARE_SELECTION_PROPOSAL",
        }
    )
    authority.stale = True

    # The authority test seam may choose its own rejection marker.
    def reject(_reference):
        from embedded_copilot.model_gateway.invocation.contracts import (
            TaskAuthorityFailure,
        )

        raise TaskAuthorityFailure()

    authority.revalidate = reject
    with pytest.raises(ValueError):
        service.prepare_consumption_context(request, authority_source=authority)
    assert events == []
    assert store.get_attempt_for_project(request.project_id, request.request_id) is None
