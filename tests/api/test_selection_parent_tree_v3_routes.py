from __future__ import annotations

import json

import pytest

from tests.api.test_selection_revision_routes import client_for
from tests.project_catalog.test_selection_parent_tree_v3 import (
    _choose_tree,
    _fixture,
    _parents,
)


def test_v3_http_parent_tree_strict_queries_create_approve_and_legacy_reads(
    tmp_path, monkeypatch
):
    catalog, detail, records, _ = _fixture(tmp_path, monkeypatch)
    client = client_for(catalog, detail)
    path = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/hardware-selection"
    before = catalog._repository._database.read_bytes()
    response = client.get(
        path + "/currentness", params={"view": "PREVIEW", "schema_version": 3}
    )
    assert response.status_code == 200, response.text
    initial = response.json()
    parents = _parents(initial, records[1])
    parent_json = [
        {"slot_id": a, "source_id": b, "source_fingerprint": c} for a, b, c in parents
    ]
    response = client.get(
        path + "/currentness",
        params={
            "view": "PREVIEW",
            "schema_version": 3,
            "parent_choices": json.dumps(parent_json),
        },
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["schema_version"] == 3
    assert catalog._repository._database.read_bytes() == before
    for params in (
        {
            "view": "PREVIEW",
            "schema_version": 2,
            "parent_choices": json.dumps(parent_json),
        },
        {"view": "CURRENT", "schema_version": 3},
        {"view": "PREVIEW", "schema_version": 3, "parent_choices": "[]"},
        {
            "view": "PREVIEW",
            "schema_version": 3,
            "parent_choices": json.dumps(parent_json + parent_json),
        },
        {
            "view": "PREVIEW",
            "schema_version": 3,
            "parent_choices": '[{"slot_id":"a","slot_id":"b","source_id":"x","source_fingerprint":"'
            + records[1].fingerprint
            + '"}]',
        },
        {
            "view": "PREVIEW",
            "schema_version": 3,
            "parent_choices": "[" + " " * 16384 + "]",
        },
    ):
        assert client.get(path + "/currentness", params=params).status_code in (
            409,
            422,
        )
        assert catalog._repository._database.read_bytes() == before
    choices = [
        {"slot_id": a, "source_id": b, "source_fingerprint": c}
        for a, b, c in _choose_tree(preview)
    ]
    selector = preview["expected_head"]
    body = {
        "schema_version": 3,
        "request_id": "http-parent-v3",
        "expected_head_revision_id": selector[0],
        "expected_head_selection_id": selector[1],
        "expected_head_selection_fingerprint": selector[2],
        "preview_fingerprint": preview["preview_fingerprint"],
        "parent_choices": parent_json,
        "selections": choices,
    }
    invalid = dict(body, parent_choices=parent_json[:-1])
    assert client.post(path + "/proposal", json=invalid).status_code in (409, 422)
    assert catalog._repository._database.read_bytes() == before
    response = client.post(path + "/proposal", json=body)
    assert response.status_code == 200, response.text
    revision = response.json()
    assert revision["schema_version"] == 2
    head = revision["head"]
    response = client.post(
        path + "/decision",
        json={
            "schema_version": 2,
            "revision_id": head["revision_id"],
            "selection_id": head["selection_id"],
            "selection_fingerprint": head["selection_fingerprint"],
            "expected_decision_fingerprint": None,
            "decision": "APPROVED",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["head"]["status"] == "APPROVED"
    assert client.get(path + "/currentness").json()["schema_version"] == 2
    assert (
        client.get(path + "/currentness", params={"view": "PREVIEW"}).json()[
            "schema_version"
        ]
        == 2
    )
    client.close()


@pytest.mark.parametrize("model_changes_parent", [False, True])
def test_v3_engineering_model_uses_exact_parent_tree_and_rejects_model_root_change(
    tmp_path, monkeypatch, model_changes_parent
):
    from embedded_copilot.model_gateway_storage import SQLiteModelGatewayStore
    from tests.api.test_s05_model_candidate_routes import (
        _qualified_candidate_http_service,
    )

    catalog, detail, records, _ = _fixture(tmp_path, monkeypatch)
    client = client_for(catalog, detail)
    path = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/hardware-selection"
    initial = client.get(
        path + "/currentness", params={"view": "PREVIEW", "schema_version": 3}
    ).json()
    parents = _parents(initial, records[1])
    parent_json = [
        {"slot_id": a, "source_id": b, "source_fingerprint": c} for a, b, c in parents
    ]
    preview = client.get(
        path + "/currentness",
        params={
            "view": "PREVIEW",
            "schema_version": 3,
            "parent_choices": json.dumps(parent_json),
        },
    ).json()
    choices = [
        {"slot_id": a, "source_id": b, "source_fingerprint": c}
        for a, b, c in _choose_tree(preview)
    ]
    if model_changes_parent:
        selected_parent = next(
            x for x in choices if x["source_id"] == records[1].source_id
        )
        selected_parent.update(
            source_id=records[0].source_id, source_fingerprint=records[0].fingerprint
        )
    before = catalog._repository._database.read_bytes()
    binding = next(
        x
        for x in detail.engineering_plan.model_operation_bindings
        if x.operation_id == "HARDWARE_SELECTION_PROPOSAL"
    )
    head = preview["expected_head"]
    body = {
        "schema_version": 3,
        "request_id": "v3-governed-model",
        "plan_id": detail.engineering_plan.plan_id,
        "phase_id": binding.phase_id,
        "plan_task_id": binding.plan_task_id,
        "expected_head_revision_id": head[0],
        "expected_head_selection_id": head[1],
        "expected_head_selection_fingerprint": head[2],
        "preview_fingerprint": preview["preview_fingerprint"],
        "parent_choices": parent_json,
    }
    store = SQLiteModelGatewayStore(tmp_path / "v3-model.sqlite3")
    service, calls = _qualified_candidate_http_service(
        tmp_path, json.dumps({"selections": choices}), store
    )
    client.app.state.model_invocation_service = service
    response = client.post(path + "/model-candidate-proposal", json=body)
    assert response.status_code == (409 if model_changes_parent else 200), response.text
    assert len(calls) == 1
    if model_changes_parent:
        assert catalog._repository._database.read_bytes() == before
        assert client.get(path + "/currentness").json()["kind"] == "LEGACY"
        client.close()
        return
    assert response.json()["status"] == "REVIEW"
    current = client.get(path + "/currentness").json()
    selected = current["selection"]["proposal"]["selected_components"]
    assert any(x["source"]["source_id"] == records[1].source_id for x in selected)
    assert current["head"]["status"] == "REVIEW"
    assert (
        client.get(path + "/currentness", params={"view": "HISTORY"}).json()[
            "revisions"
        ][0]["head"]["status"]
        == "REVIEW"
    )
    client.close()
