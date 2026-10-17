from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import UTC, datetime

from pydantic import ValidationError

from embedded_copilot.application import (
    ApplicationAuditEvent,
    ApplicationAuditSinkPort,
    ApplicationAuditConflict,
    ApplicationAuditUnavailable,
)

from ..exceptions import ChatConflict, ChatUnavailable
from ..models import ChatAuditEvent, ChatEvent, ChatSession, ChatTraceEvent


def _copy(value: object, model: type) -> object:
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def _event_body(body: str) -> ChatEvent:
    try:
        return ChatEvent.model_validate_json(body)
    except ValidationError:
        material = json.loads(body)
    for field in ("request_id", "trace_id", "analysis_state", "reason_code", "terminal_event"):
        material.setdefault(field, None)
    material["fingerprint"] = "sha256:" + "0" * 64
    return ChatEvent.model_validate_json(json.dumps(material, ensure_ascii=False))


def _execute(connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...] = ()):
    return getattr(connection, "execute")(statement, parameters)


def _executescript(connection: sqlite3.Connection, script: str):
    return getattr(connection, "executescript")(script)


class SystemChatClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SQLiteChatStore:
    """Persistent implementation of the existing Chat session/audit/event Ports."""

    __slots__ = ("_capacity", "_database", "_lock")

    def __init__(self, database: object, *, event_capacity: int = 32) -> None:
        if type(event_capacity) is not int or event_capacity < 1:
            raise ValueError("event capacity is invalid")
        self._database = str(database)
        self._capacity = event_capacity
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5)
        _execute(connection, "PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            _executescript(connection,
                """
                CREATE TABLE IF NOT EXISTS desktop_chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS desktop_chat_audit (
                    event_id TEXT PRIMARY KEY,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS desktop_chat_events (
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    body TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_desktop_chat_events_session
                    ON desktop_chat_events(session_id, sequence);
                CREATE TABLE IF NOT EXISTS desktop_chat_trace_events (
                    trace_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    body TEXT NOT NULL,
                    PRIMARY KEY (trace_id, stage)
                );
                CREATE INDEX IF NOT EXISTS idx_desktop_chat_trace_events_trace
                    ON desktop_chat_trace_events(trace_id, stage);
                """
            )

    def save(self, session: ChatSession) -> ChatSession:
        checked = ChatSession.model_validate(copy.deepcopy(session.model_dump(mode="python")))
        with self._lock, self._connect() as connection:
            row = _execute(connection,
                "SELECT body FROM desktop_chat_sessions WHERE session_id = ?",
                (checked.session_id,),
            ).fetchone()
            if row is not None:
                existing = ChatSession.model_validate_json(row[0])
                immutable = ("tenant_id", "project_id", "requester", "created_at", "expires_at")
                if any(getattr(existing, name) != getattr(checked, name) for name in immutable):
                    raise ChatConflict("chat session conflict")
            _execute(connection,
                "INSERT INTO desktop_chat_sessions(session_id, body) VALUES(?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET body=excluded.body",
                (checked.session_id, checked.model_dump_json()),
            )
        return _copy(checked, ChatSession)

    def get(self, session_id: str) -> ChatSession | None:
        with self._lock, self._connect() as connection:
            row = _execute(connection,
                "SELECT body FROM desktop_chat_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return None if row is None else ChatSession.model_validate_json(row[0])

    def append(self, event: ChatAuditEvent) -> ChatAuditEvent:
        checked = ChatAuditEvent.model_validate(copy.deepcopy(event.model_dump(mode="python")))
        with self._lock, self._connect() as connection:
            row = _execute(connection,
                "SELECT body FROM desktop_chat_audit WHERE event_id = ?",
                (checked.event_id,),
            ).fetchone()
            if row is not None:
                existing = ChatAuditEvent.model_validate_json(row[0])
                if existing != checked:
                    raise ChatConflict("chat audit conflict")
                return _copy(existing, ChatAuditEvent)
            _execute(connection,
                "INSERT INTO desktop_chat_audit(event_id, body) VALUES(?, ?)",
                (checked.event_id, checked.model_dump_json()),
            )
        return _copy(checked, ChatAuditEvent)

    def get_audit(self, event_id: str) -> ChatAuditEvent | None:
        with self._lock, self._connect() as connection:
            row = _execute(connection,
                "SELECT body FROM desktop_chat_audit WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return None if row is None else ChatAuditEvent.model_validate_json(row[0])

    def append_event(self, event: ChatEvent) -> ChatEvent:
        checked = ChatEvent.model_validate(copy.deepcopy(event.model_dump(mode="python")))
        with self._lock, self._connect() as connection:
            row = _execute(connection,
                "SELECT body FROM desktop_chat_events WHERE event_id = ?",
                (checked.event_id,),
            ).fetchone()
            if row is not None:
                existing = _event_body(row[0])
                if existing != checked:
                    raise ChatConflict("chat event conflict")
                return _copy(existing, ChatEvent)
            _execute(connection,
                "INSERT INTO desktop_chat_events(session_id, sequence, event_id, body) VALUES(?, ?, ?, ?)",
                (checked.session_id, checked.sequence, checked.event_id, checked.model_dump_json()),
            )
            _execute(connection,
                "DELETE FROM desktop_chat_events WHERE session_id = ? AND sequence <= "
                "(SELECT COALESCE(MAX(sequence), 0) - ? FROM desktop_chat_events WHERE session_id = ?)",
                (checked.session_id, self._capacity, checked.session_id),
            )
        return _copy(checked, ChatEvent)

    def read_after(self, session_id: str, last_event_id: str | None) -> tuple[ChatEvent, ...] | None:
        with self._lock, self._connect() as connection:
            if last_event_id is None:
                rows = _execute(connection,
                    "SELECT body FROM desktop_chat_events WHERE session_id = ? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
            else:
                marker = _execute(connection,
                    "SELECT sequence FROM desktop_chat_events WHERE session_id = ? AND event_id = ?",
                    (session_id, last_event_id),
                ).fetchone()
                if marker is None:
                    return None
                rows = _execute(connection,
                    "SELECT body FROM desktop_chat_events WHERE session_id = ? AND sequence > ? ORDER BY sequence",
                    (session_id, marker[0]),
                ).fetchall()
        return tuple(_event_body(row[0]) for row in rows)

    def append_trace(self, event: ChatTraceEvent) -> ChatTraceEvent:
        checked = ChatTraceEvent.model_validate(copy.deepcopy(event.model_dump(mode="python")))
        with self._lock, self._connect() as connection:
            row = _execute(connection,
                "SELECT body FROM desktop_chat_trace_events WHERE event_id = ?",
                (checked.event_id,),
            ).fetchone()
            if row is not None:
                existing = ChatTraceEvent.model_validate_json(row[0])
                if existing != checked:
                    raise ChatConflict("chat trace conflict")
                return _copy(existing, ChatTraceEvent)
            _execute(connection,
                "INSERT INTO desktop_chat_trace_events(trace_id, stage, event_id, body) VALUES(?, ?, ?, ?)",
                (checked.trace_id, checked.stage.value, checked.event_id, checked.model_dump_json()),
            )
        return _copy(checked, ChatTraceEvent)

    def read_trace(self, trace_id: str) -> tuple[ChatTraceEvent, ...]:
        with self._lock, self._connect() as connection:
            rows = _execute(connection,
                "SELECT body FROM desktop_chat_trace_events WHERE trace_id = ? ORDER BY rowid",
                (trace_id,),
            ).fetchall()
        return tuple(ChatTraceEvent.model_validate_json(row[0]) for row in rows)


class SQLiteChatAuditSink:
    __slots__ = ("_store",)

    def __init__(self, store: SQLiteChatStore) -> None:
        self._store = store

    def append(self, event: ChatAuditEvent) -> ChatAuditEvent:
        try:
            return self._store.append(event)
        except ChatConflict:
            raise
        except Exception as error:
            raise ChatUnavailable("chat audit store is unavailable") from error

    def get(self, event_id: str) -> ChatAuditEvent | None:
        try:
            return self._store.get_audit(event_id)
        except Exception as error:
            raise ChatUnavailable("chat audit store is unavailable") from error


class SQLiteChatEventStore:
    __slots__ = ("_store",)

    def __init__(self, store: SQLiteChatStore) -> None:
        self._store = store

    def append(self, event: ChatEvent) -> ChatEvent:
        try:
            return self._store.append_event(event)
        except ChatConflict:
            raise
        except Exception as error:
            raise ChatUnavailable("chat event store is unavailable") from error

    def read_after(self, session_id: str, last_event_id: str | None) -> tuple[ChatEvent, ...] | None:
        try:
            return self._store.read_after(session_id, last_event_id)
        except Exception as error:
            raise ChatUnavailable("chat event store is unavailable") from error


class SQLiteChatTraceStore:
    __slots__ = ("_store",)

    def __init__(self, store: SQLiteChatStore) -> None:
        self._store = store

    def append(self, event: ChatTraceEvent) -> ChatTraceEvent:
        try:
            return self._store.append_trace(event)
        except ChatConflict:
            raise
        except Exception as error:
            raise ChatUnavailable("chat trace store is unavailable") from error

    def read(self, trace_id: str) -> tuple[ChatTraceEvent, ...]:
        try:
            return self._store.read_trace(trace_id)
        except Exception as error:
            raise ChatUnavailable("chat trace store is unavailable") from error


class SQLiteApplicationAuditSink(ApplicationAuditSinkPort):
    """Persistent application audit without raw question, answer or provider payloads."""

    __slots__ = ("_database", "_lock")

    def __init__(self, database: object) -> None:
        self._database = str(database)
        self._lock = threading.RLock()
        with sqlite3.connect(self._database) as connection:
            _execute(connection,
                "CREATE TABLE IF NOT EXISTS application_audit (event_id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )

    def append(self, event: ApplicationAuditEvent) -> ApplicationAuditEvent:
        checked = ApplicationAuditEvent.model_validate(copy.deepcopy(event.model_dump(mode="python")))
        try:
            with self._lock, sqlite3.connect(self._database) as connection:
                row = _execute(connection,
                    "SELECT body FROM application_audit WHERE event_id = ?",
                    (checked.event_id,),
                ).fetchone()
                if row is not None:
                    existing = ApplicationAuditEvent.model_validate_json(row[0])
                    if existing != checked:
                        raise ApplicationAuditConflict()
                    return existing
                _execute(connection,
                    "INSERT INTO application_audit(event_id, body) VALUES(?, ?)",
                    (checked.event_id, checked.model_dump_json()),
                )
        except ApplicationAuditConflict:
            raise
        except Exception as error:
            raise ApplicationAuditUnavailable() from error
        return checked

    def get(self, event_id: str) -> ApplicationAuditEvent | None:
        try:
            with self._lock, sqlite3.connect(self._database) as connection:
                row = _execute(connection,
                    "SELECT body FROM application_audit WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            return None if row is None else ApplicationAuditEvent.model_validate_json(row[0])
        except Exception as error:
            raise ApplicationAuditUnavailable() from error


__all__ = (
    "SQLiteApplicationAuditSink",
    "SQLiteChatAuditSink",
    "SQLiteChatEventStore",
    "SQLiteChatTraceStore",
    "SQLiteChatStore",
    "SystemChatClock",
)
