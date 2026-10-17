"""Formal route preparation and secret-free two-role API projection tests."""

import json

import pytest

from embedded_copilot.api.model_gateway_models import (
    InvocationAttemptView,
    InvocationReceiptView,
)
from tests.api.test_s05_model_candidate_routes import (
    Catalog,
    Invocation,
    _body,
    _client,
)
from tests.model_gateway.test_counter_bundle_integration import chain, run


@pytest.mark.parametrize("bundle", [False, True])
def test_formal_hardware_entry_freezes_only_explicit_bundle_text_profile(bundle):
    catalog = Catalog()
    invocation = Invocation(
        '{"selections":[{"slot_id":"slot-1","source_id":"part-1","source_fingerprint":"sha256:'
        + "a" * 64
        + '"}]}'
    )
    invocation.counter_bundle_enabled = bundle
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/model-candidate-proposal",
        json=_body(catalog),
        headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert response.status_code == 200, response.text
    request = invocation.last_request
    if bundle:
        assert request.generation_options.max_tokens == 2048
        assert request.generation_options.service_tier == "default"
        assert request.generation_options.structured_output_schema == (
            ("type", "object"),
        )
        assert request.payload.messages == ()
        assert json.loads(request.payload.task)["choices"][0]["source_id"] == "part-1"
    else:
        assert request.generation_options.max_tokens == 1024
        assert request.generation_options.service_tier is None
        assert request.payload.task == "HARDWARE_SELECTION_PROPOSAL"
        assert request.payload.messages


@pytest.mark.parametrize(
    "kwargs,counts",
    [
        ({}, (1, 1, 2)),
        ({"counter_status": 429}, (1, 0, 1)),
        ({"fail_role": "responses/input_tokens"}, (None, 0, None)),
        ({"fail_role": "responses"}, (1, None, None)),
    ],
)
def test_actual_v7_v4_projection_keeps_role_unknown(
    tmp_path, monkeypatch, kwargs, counts
):
    service, store, request, authority, _ = chain(tmp_path, monkeypatch, **kwargs)
    result = run(service, request, authority)
    assert result.receipt is not None, result
    attempt = store.get_attempt_for_project(request.project_id, request.request_id)
    for value in (
        InvocationReceiptView.from_domain(result.receipt),
        InvocationAttemptView.from_domain(attempt),
    ):
        projected = value.model_dump(mode="json")
        assert projected["budget_phase"] == "TERMINAL"
        assert (
            projected["counter_transport_send_count"],
            projected["generation_transport_send_count"],
            projected["total_external_request_count"],
        ) == counts
        assert projected["transport_send_count"] == counts[1]
        assert "raw_response_base64" not in projected
        assert "count_request_base64" not in projected


def test_default_production_assembly_wires_grant_but_not_qualifications(tmp_path):
    from embedded_copilot.model_gateway.invocation.consumption_source import (
        ConsumptionGrantSource,
    )
    from embedded_copilot.model_gateway.invocation.qualified_bundle_producer import (
        QualifiedCounterBundleProducer,
    )
    from tests.api.test_production_host_assembly import _default_runtime

    _, _, app = _default_runtime(tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(app):
        service = app.state.model_invocation_service
        assert service.counter_bundle_enabled is True
        assert type(service._budget_bundle_source) is QualifiedCounterBundleProducer
        grant_source, *qualifications = service._budget_bundle_source._sources
        assert type(grant_source) is ConsumptionGrantSource
        assert qualifications == [None, None, None, None]
        assert grant_source.consumption_store is service._receipt_store
        consumption = app.state.model_consumption_service
        assert consumption._store is grant_source.consumption_store
        assert grant_source._account_source is consumption._account_source is None
