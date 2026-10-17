from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

from embedded_copilot.api.main import _desktop_capability_values
from embedded_copilot.model_gateway import ModelSelectionError


class _Selection:
    def __init__(self) -> None:
        self.outcome: object = SimpleNamespace(selected_model=object(), selection_reason="STAGE_POLICY")
        self.calls = []

    def select_governed_model(self, profile, *, request_override=None):
        self.calls.append((profile.project_id, profile.stage.value, request_override))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_chat_model_capability_uses_current_project_governed_selection_without_provider_call() -> None:
    app = FastAPI()
    app.state.runtime_capability_statuses = {"chat_model": "unavailable"}
    selection = _Selection()
    app.state.model_selection_resolver = selection

    first = {item["capability"]: item for item in _desktop_capability_values(app, "project-1")}["CHAT_MODEL"]
    assert first["status"] == "AVAILABLE"
    assert first["reason_code"] == "GOVERNED_SELECTION_READY"
    assert selection.calls == [("project-1", "REQUIREMENT_ANALYSIS", None)]

    selection.outcome = ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
    changed = {item["capability"]: item for item in _desktop_capability_values(app, "project-1")}["CHAT_MODEL"]
    assert changed["status"] == "UNAVAILABLE"
    assert changed["reason_code"] == "MODEL_CREDENTIAL_GATE_FAILED"
    assert len(selection.calls) == 2


def test_chat_model_capability_preserves_unverified_and_pending_states() -> None:
    app = FastAPI()
    app.state.runtime_capability_statuses = {}
    selection = _Selection()
    app.state.model_selection_resolver = selection
    selection.outcome = ModelSelectionError("MODEL_AVAILABILITY_GATE_FAILED", insufficient=True)
    unverified = {item["capability"]: item for item in _desktop_capability_values(app, "project-1")}["CHAT_MODEL"]
    assert unverified["status"] == "UNVERIFIED"
    selection.outcome = SimpleNamespace(selected_model=None, selection_reason="AUTO_ROUTER_PENDING")
    pending = {item["capability"]: item for item in _desktop_capability_values(app, "project-1")}["CHAT_MODEL"]
    assert pending["status"] == "UNVERIFIED"
    selection.outcome = SimpleNamespace(selected_model=None, selection_reason="CANDIDATE_GROUP_FAILED")
    failed = {item["capability"]: item for item in _desktop_capability_values(app, "project-1")}["CHAT_MODEL"]
    assert failed["status"] == "UNAVAILABLE"
    selection.outcome = SimpleNamespace(selected_model=None, selection_reason="AUTO_INSUFFICIENT_EVIDENCE")
    insufficient = {item["capability"]: item for item in _desktop_capability_values(app, "project-1")}["CHAT_MODEL"]
    assert insufficient["status"] == "UNVERIFIED"
    assert insufficient["reason_code"] == "INSUFFICIENT_EVIDENCE"


def test_chat_model_capability_fails_closed_when_selection_projection_cannot_be_read() -> None:
    app = FastAPI()
    app.state.runtime_capability_statuses = {}
    selection = _Selection()
    selection.outcome = RuntimeError("local selection store failed")
    app.state.model_selection_resolver = selection
    capability = {item["capability"]: item for item in _desktop_capability_values(app, "project-1")}["CHAT_MODEL"]
    assert capability["status"] == "UNAVAILABLE"
    assert capability["reason_code"] == "MODEL_SELECTION_UNAVAILABLE"
    assert "local selection store failed" not in str(capability)
