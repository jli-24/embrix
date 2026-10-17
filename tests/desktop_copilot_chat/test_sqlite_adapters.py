from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from embedded_copilot.desktop_copilot_chat import (
    ChatAuditEvent,
    ChatAuditEventType,
    ChatEvent,
    ChatEventType,
    ChatSession,
    ChatSessionStatus,
)
from embedded_copilot.desktop_copilot_chat.adapters import (
    SQLiteChatAuditSink,
    SQLiteChatEventStore,
    SQLiteChatStore,
)


NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)


def session() -> ChatSession:
    return ChatSession(
        session_id="session-sqlite",
        tenant_id="tenant-1",
        project_id="project-1",
        requester="engineer-1",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        status=ChatSessionStatus.OPEN,
    )


def event(sequence: int) -> ChatEvent:
    return ChatEvent(
        event_id=f"chat-session-sqlite-{sequence}",
        sequence=sequence,
        session_id="session-sqlite",
        event_type=(
            ChatEventType.CHAT_STARTED
            if sequence == 1
            else ChatEventType.ANSWER_COMPLETED
        ),
        answer_chunk=None,
        answer_fingerprint=("sha256:" + "a" * 64) if sequence > 1 else None,
        timestamp=NOW,
    )


def test_sqlite_chat_store_persists_sessions_and_events(tmp_path) -> None:
    database = tmp_path / "desktop.sqlite3"
    first = SQLiteChatStore(database, event_capacity=2)
    first.save(session())
    first.append_event(event(1))
    first.append_event(event(2))

    second = SQLiteChatStore(database, event_capacity=2)
    assert second.get("session-sqlite") == session()
    assert second.read_after("session-sqlite", None) == (event(1), event(2))
    assert second.read_after("session-sqlite", "chat-session-sqlite-1") == (event(2),)
    assert second.read_after("session-sqlite", "unknown-event") is None


def test_sqlite_chat_event_store_enforces_bounded_history(tmp_path) -> None:
    store = SQLiteChatStore(tmp_path / "desktop.sqlite3", event_capacity=2)
    events = SQLiteChatEventStore(store)
    events.append(event(1))
    events.append(event(2))
    events.append(event(3))

    assert [item.sequence for item in events.read_after("session-sqlite", None) or ()] == [2, 3]
    assert events.read_after("session-sqlite", "chat-session-sqlite-1") is None


def test_sqlite_chat_store_reads_legacy_event_enum_json_before_failed_terminal(tmp_path) -> None:
    database = tmp_path / "desktop.sqlite3"
    store = SQLiteChatStore(database)
    store.append_event(event(1))

    with sqlite3.connect(database) as connection:
        body = json.loads(connection.execute(
            "SELECT body FROM desktop_chat_events WHERE event_id = ?",
            ("chat-session-sqlite-1",),
        ).fetchone()[0])
        for field in ("request_id", "trace_id", "analysis_state", "reason_code", "terminal_event"):
            body.pop(field, None)
        body["fingerprint"] = "sha256:" + "0" * 64
        connection.execute(
            "UPDATE desktop_chat_events SET body = ? WHERE event_id = ?",
            (json.dumps(body), "chat-session-sqlite-1"),
        )

    assert store.read_after("session-sqlite", None) == (event(1),)


def test_sqlite_chat_audit_is_idempotent_and_does_not_store_raw_prompt(tmp_path) -> None:
    store = SQLiteChatStore(tmp_path / "desktop.sqlite3")
    audit = SQLiteChatAuditSink(store)
    value = ChatAuditEvent(
        event_id=ChatAuditEvent.event_id_for(
            session().fingerprint,
            ChatAuditEventType.CHAT_SESSION_OPENED,
        ),
        event_type=ChatAuditEventType.CHAT_SESSION_OPENED,
        session_fingerprint=session().fingerprint,
        project_id="project-1",
        request_id=None,
        answer_fingerprint=None,
        timestamp=NOW,
    )

    assert audit.append(value) == value
    assert audit.append(value) == value
    raw = (tmp_path / "desktop.sqlite3").read_bytes()
    assert b"prompt" not in raw.lower()
    assert b"provider" not in raw.lower()
