from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_copilot import approve_engineering_plan
from embedded_copilot.engineering_copilot import EngineeringPlan
from embedded_copilot.project_catalog.models import ConversationDetail
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
import pytest
from pydantic import ValidationError


def _old_conversation_body() -> dict:
    fixture = Path(__file__).parents[1] / "engineering_copilot" / "fixtures" / "s05_v1_plan_review.json"
    old_plan = EngineeringPlan.model_validate(json.loads(fixture.read_text(encoding="utf-8"))["plan"], strict=False)
    plan, review = approve_engineering_plan(
        old_plan, reviewer="user-1", decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    detail = ConversationDetail(
        conversation_id="conversation-1", project_id="project-1", tenant_id="tenant-1", requester="user-1",
        title="legacy plan", turns=(), created_at=datetime(2026, 9, 2, tzinfo=UTC),
        updated_at=datetime(2026, 9, 2, tzinfo=UTC), engineering_plan=plan,
        engineering_plan_review=review,
    )
    body = detail.model_dump(mode="json")
    body["engineering_plan"].pop("model_operation_schema_version")
    body["engineering_plan"].pop("model_operation_bindings")
    encoded = json.dumps({key: value for key, value in body.items() if key != "fingerprint"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    body["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return body


def test_old_sqlite_plan_review_and_conversation_fingerprints_survive_read_save_read(tmp_path) -> None:
    database = tmp_path / "legacy.sqlite3"
    repository = SQLiteProjectCatalogRepository(database)
    body = _old_conversation_body()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO conversations(conversation_id,project_id,tenant_id,requester,updated_at,body) VALUES(?,?,?,?,?,?)",
            ("conversation-1", "project-1", "tenant-1", "user-1", "2026-09-02T00:00:00+00:00", json.dumps(body)),
        )
    loaded = repository.conversation("conversation-1")
    assert loaded is not None and loaded.engineering_plan is not None and loaded.engineering_plan_review is not None
    assert loaded.fingerprint == body["fingerprint"]
    assert loaded.engineering_plan.fingerprint == body["engineering_plan"]["fingerprint"]
    assert loaded.engineering_plan_review.fingerprint == body["engineering_plan_review"]["fingerprint"]
    assert loaded.engineering_plan_review.proposal_fingerprint == loaded.engineering_plan.content_fingerprint
    repository.save_conversation(loaded)
    with sqlite3.connect(database) as connection:
        saved = json.loads(connection.execute("SELECT body FROM conversations WHERE conversation_id=?", ("conversation-1",)).fetchone()[0])
    assert saved["fingerprint"] == body["fingerprint"]
    assert "model_operation_schema_version" not in saved["engineering_plan"]
    assert "model_operation_bindings" not in saved["engineering_plan"]
    reloaded = repository.conversation("conversation-1")
    assert reloaded is not None
    assert reloaded.fingerprint == body["fingerprint"]
    assert reloaded.engineering_plan.fingerprint == body["engineering_plan"]["fingerprint"]


@pytest.mark.parametrize("target", ["plan", "conversation"])
def test_old_sqlite_fingerprint_tamper_is_rejected(tmp_path, target: str) -> None:
    database = tmp_path / "tampered.sqlite3"
    repository = SQLiteProjectCatalogRepository(database)
    body = _old_conversation_body()
    if target == "plan":
        body["engineering_plan"]["title"] = "tampered"
    else:
        body["title"] = "tampered"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO conversations(conversation_id,project_id,tenant_id,requester,updated_at,body) VALUES(?,?,?,?,?,?)",
            ("conversation-1", "project-1", "tenant-1", "user-1", "2026-09-02T00:00:00+00:00", json.dumps(body)),
        )
    with pytest.raises((ValidationError, ValueError), match="fingerprint"):
        repository.conversation("conversation-1")


def test_old_sqlite_missing_later_projection_field_still_reads(tmp_path) -> None:
    database = tmp_path / "older.sqlite3"
    repository = SQLiteProjectCatalogRepository(database)
    body = _old_conversation_body()
    body.pop("hardware_selection")
    canonical = json.dumps({key: value for key, value in body.items() if key != "fingerprint"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    body["fingerprint"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO conversations(conversation_id,project_id,tenant_id,requester,updated_at,body) VALUES(?,?,?,?,?,?)",
            ("conversation-1", "project-1", "tenant-1", "user-1", "2026-09-02T00:00:00+00:00", json.dumps(body)),
        )
    loaded = repository.conversation("conversation-1")
    assert loaded is not None
    assert loaded.fingerprint == body["fingerprint"]
    repository.save_conversation(loaded)
    reloaded = repository.conversation("conversation-1")
    assert reloaded is not None and reloaded.fingerprint == body["fingerprint"]
