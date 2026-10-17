from __future__ import annotations

from fastapi.testclient import TestClient

from embedded_copilot.application.circuit_publication import PredesignDecisionCommand
from embedded_copilot.project_catalog import ProjectCreationRequest, ProjectType
from tests.api.test_circuit_intent_routes import _app
from tests.project_catalog.test_circuit_catalog_persistence import NOW, OWNER, TENANT
from tests.project_catalog.test_predesign_publication import (
    _approved_catalog,
    _command,
    _service,
)


def test_predesign_api_preview_and_decision_do_not_require_erc(tmp_path) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    service = _service(catalog)
    url = f"/api/v1/copilot/projects/{project_id}/circuit-intents"
    with TestClient(_app(service, requester=OWNER, tenant_id=TENANT)) as client:
        preview = client.post(url + "/preview", json=_command(detail).model_dump(mode="json"))
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["purpose"] == "PREDESIGN_APPROVAL"
        assert body["completion"] == "DESIGN_ONLY"
        assert "evidence_receipt_id" not in body
        decision = PredesignDecisionCommand(
            purpose="PREDESIGN_APPROVAL", proposal_id=body["proposal_id"],
            proposal_fingerprint=body["proposal_fingerprint"],
            source_fingerprint=body["source_fingerprint"], decision="APPROVE",
        )
        decided = client.post(url + "/decision", json=decision.model_dump(mode="json"))
        assert decided.status_code == 200, decided.text
        assert decided.json()["completion"] == "DESIGN_ONLY"
        assert decided.json()["object_fingerprint"]
        rejected = client.post(url + "/preview", json={
            **_command(detail).model_dump(mode="json"), "verified": True,
        })
        assert rejected.status_code == 422


def test_predesign_api_rejects_same_tenant_foreign_project_and_conversation(tmp_path) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    preview = catalog.preview(
        ProjectCreationRequest(
            name="Other Circuit Fixture", relative_path="other-circuit-fixture",
            project_type=ProjectType.STM32,
        ), tenant_id=TENANT, requester=OWNER,
    )
    other = catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=NOW,
        tenant_id=TENANT, requester=OWNER,
    )
    other_conversation = catalog.create_conversation(
        project_id, tenant_id=TENANT, requester=OWNER,
    ).conversation
    service = _service(catalog)
    own_url = f"/api/v1/copilot/projects/{project_id}/circuit-intents"
    other_url = f"/api/v1/copilot/projects/{other.project_id}/circuit-intents"
    command = _command(detail)
    with TestClient(_app(service, requester=OWNER, tenant_id=TENANT)) as client:
        foreign_project = client.post(
            other_url + "/preview", json=command.model_dump(mode="json"),
        )
        assert foreign_project.status_code == 404, foreign_project.text
        foreign_conversation = client.post(
            own_url + "/preview", json=command.model_copy(update={
                "conversation_id": other_conversation.conversation_id,
            }).model_dump(mode="json"),
        )
        assert foreign_conversation.status_code == 422, foreign_conversation.text
        own_preview = client.post(own_url + "/preview", json=command.model_dump(mode="json"))
        assert own_preview.status_code == 200, own_preview.text
        candidate = own_preview.json()
        decision = PredesignDecisionCommand(
            purpose="PREDESIGN_APPROVAL", proposal_id=candidate["proposal_id"],
            proposal_fingerprint=candidate["proposal_fingerprint"],
            source_fingerprint=candidate["source_fingerprint"], decision="APPROVE",
        )
        foreign_decision = client.post(
            other_url + "/decision", json=decision.model_dump(mode="json"),
        )
        assert foreign_decision.status_code == 503, foreign_decision.text
        assert foreign_decision.json()["error"] == "PROJECT_CATALOG_UNAVAILABLE"
    with TestClient(_app(service, requester="other-user", tenant_id=TENANT)) as client:
        wrong_owner = client.post(own_url + "/decision", json=decision.model_dump(mode="json"))
        assert wrong_owner.status_code == 403, wrong_owner.text
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()
    assert catalog.current_circuit_intents(other.project_id, tenant_id=TENANT, requester=OWNER) == ()
    assert catalog.predesign_proposal(
        project_id, candidate["proposal_id"], tenant_id=TENANT, requester=OWNER,
    ).decision is None
