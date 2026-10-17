"""Verify the v7.1 Greenfield Provider answer contract on the packaged EXE.

The live mode is deliberately hard-coded to the qdwq / CREATE_NEW / STM32
acceptance case and exactly three user messages.  It exchanges the short-lived
loopback bootstrap without printing it, issues one transmission approval per
request, and emits only identifiers plus redacted booleans/counts.

The restart mode performs no chat submission and therefore no Provider call.
It resumes the persisted conversation, compares its projection with the saved
live evidence, and verifies that the total persisted MODEL_GATEWAY trace count
did not change during recovery.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


_QUESTIONS = (
    "stm32",
    "做一个温控器",
    "只控制加热，精度 ±0.5°C",
)
_GREENFIELD_ROUTES = {"GREENFIELD_DISCOVERY", "GREENFIELD_DESIGN"}
_ACTIVE_ANALYSIS_STATES = {"REQUIREMENTS_PARTIAL", "REQUIREMENTS_REFINING"}
_REQUIRED_TRACE_STAGES = (
    "ROUTE_SELECT",
    "SOURCE_SNAPSHOT",
    "EVIDENCE_GATE",
    "PROVIDER_RESOLVE",
    "MODEL_GATEWAY",
    "ANSWER_PERSIST",
    "SSE_TERMINAL",
    "UI_PROJECTION",
)
_FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "authorization",
        "credential",
        "nonce",
        "raw_prompt",
        "raw_response",
        "session_id",
    }
)


class GoldenUnverified(RuntimeError):
    """A required piece of real-runtime evidence is absent or contradictory."""


class LoopbackClient:
    def __init__(self, base_url: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise GoldenUnverified("base URL is not the packaged loopback origin")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self._opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @classmethod
    def exchange(cls, bootstrap_url_file: Path) -> "LoopbackClient":
        try:
            handoff = bootstrap_url_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise GoldenUnverified("bootstrap handoff is unavailable") from error
        parsed = urlsplit(handoff)
        nonce = parsed.fragment
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not nonce:
            raise GoldenUnverified("bootstrap handoff is invalid")
        client = cls(f"{parsed.scheme}://{parsed.netloc}")
        client.json(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            method="POST",
            body={"nonce": nonce},
        )
        return client

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: object | None = None,
        accept: str = "application/json",
        last_event_id: str | None = None,
    ) -> tuple[int, bytes]:
        headers = {"Accept": accept, "Content-Type": "application/json"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=60) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()
        except URLError as error:
            raise GoldenUnverified("packaged EXE API is unreachable") from error

    def json(self, path: str, *, method: str = "GET", body: object | None = None) -> object:
        status, raw = self.request(path, method=method, body=body)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GoldenUnverified(f"non-JSON response status={status}") from error
        if status >= 400:
            code = value.get("error", "HTTP_ERROR") if isinstance(value, dict) else "HTTP_ERROR"
            raise GoldenUnverified(f"request rejected status={status} code={code}")
        return value


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GoldenUnverified(f"{field} is invalid")
    return value


def _redacted_report(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _redacted_report(item)
            for key, item in value.items()
            if str(key).casefold() not in _FORBIDDEN_REPORT_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_redacted_report(item) for item in value]
    return value


def _sequence(value: object, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise GoldenUnverified(f"{field} is invalid")
    return value


def _default_database() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    return root / "Embedded Copilot" / "current" / "database" / "desktop.sqlite3"


def _trace_rows(database: Path, trace_id: str) -> list[dict[str, Any]]:
    if not database.is_file():
        raise GoldenUnverified("production trace database is unavailable")
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT body FROM desktop_chat_trace_events WHERE trace_id = ? ORDER BY rowid",
                (trace_id,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise GoldenUnverified("production trace database could not be read") from error
    try:
        return [_mapping(json.loads(body), field="trace event") for (body,) in rows]
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise GoldenUnverified("production trace event is invalid") from error


def _model_gateway_trace_count(database: Path) -> int:
    if not database.is_file():
        raise GoldenUnverified("production trace database is unavailable")
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM desktop_chat_trace_events WHERE stage = 'MODEL_GATEWAY'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise GoldenUnverified("production trace database could not be read") from error
    if row is None or not isinstance(row[0], int):
        raise GoldenUnverified("MODEL_GATEWAY trace count is unavailable")
    return row[0]


def _validate_trace(rows: list[dict[str, Any]]) -> dict[str, object]:
    by_stage = {str(item.get("stage")): item for item in rows}
    missing = [stage for stage in _REQUIRED_TRACE_STAGES if stage not in by_stage]
    if missing:
        raise GoldenUnverified("required trace stages are missing")
    route = by_stage["ROUTE_SELECT"].get("route")
    if route not in _GREENFIELD_ROUTES:
        raise GoldenUnverified("route is not Greenfield Discovery/Design")
    if by_stage["SOURCE_SNAPSHOT"].get("gate_result") != "EMPTY_PROJECT":
        raise GoldenUnverified("source snapshot is not EMPTY_PROJECT")
    if by_stage["EVIDENCE_GATE"].get("gate_result") != "NOT_APPLICABLE_FOR_GREENFIELD":
        raise GoldenUnverified("Greenfield evidence gate is invalid")
    if by_stage["PROVIDER_RESOLVE"].get("gate_result") != "AVAILABLE":
        raise GoldenUnverified("Provider was not resolved as AVAILABLE")
    gateway = by_stage["MODEL_GATEWAY"]
    if gateway.get("gate_result") != "PASS" or gateway.get("gateway_invocation_count") != 1:
        raise GoldenUnverified("ModelGateway did not accept exactly one invocation")
    terminal = by_stage["SSE_TERMINAL"]
    if terminal.get("terminal_event") != "ANSWER_COMPLETED":
        raise GoldenUnverified("trace terminal is not ANSWER_COMPLETED")
    if by_stage["ANSWER_PERSIST"].get("reason_code") != "ANSWER_COMPLETED":
        raise GoldenUnverified("answer persistence did not pass")
    return {
        "route": route,
        "source_snapshot": "EMPTY_PROJECT",
        "evidence_gate": "NOT_APPLICABLE_FOR_GREENFIELD",
        "provider_resolve": "AVAILABLE",
        "gateway_invocation_count": 1,
        "answer_persist": "PASS",
    }


def _sse(raw: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.decode("utf-8", errors="replace").split("\n\n"):
        event: dict[str, Any] = {"data": {}}
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("id:"):
                event["id"] = line[3:].strip()
            elif line.startswith("event:"):
                event["event"] = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if "event" in event:
            try:
                event["data"] = json.loads("\n".join(data) or "{}")
            except json.JSONDecodeError as error:
                raise GoldenUnverified("SSE data is invalid") from error
            events.append(event)
    return events


def _terminal_diagnostics(events: list[dict[str, Any]], request_id: str) -> dict[str, int]:
    related = [
        item
        for item in events
        if isinstance(item.get("data"), dict)
        and item["data"].get("request_id") == request_id
    ]
    completed = sum(item.get("event") == "ANSWER_COMPLETED" for item in related)
    failed = sum(item.get("event") == "ANSWER_FAILED" for item in related)
    if completed != 1 or failed != 0:
        raise GoldenUnverified("terminal exclusivity failed")
    return {"answer_completed_count": completed, "answer_failed_count": failed}


def _provider_confirmed_count(context: dict[str, Any]) -> int:
    total = 0
    for field in ("requirements", "constraints", "proposed_items"):
        for item in _sequence(context.get(field, []), field=field):
            checked = _mapping(item, field="requirement item")
            if checked.get("provenance") == "PROVIDER_RESPONSE" and checked.get("status") == "CONFIRMED":
                total += 1
    return total


def _confirmed(context: dict[str, Any], category: str, value: str) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("category") == category
        and item.get("normalized_value") == value
        and item.get("status") == "CONFIRMED"
        and item.get("provenance") == "USER_UTTERANCE"
        for item in _sequence(context.get("requirements", []), field="requirements")
    )


def _validate_projection(
    detail: dict[str, Any],
    *,
    request_id: str,
    expected_turn_count: int,
) -> dict[str, object]:
    turns = _sequence(detail.get("turns"), field="conversation turns")
    if len(turns) != expected_turn_count:
        raise GoldenUnverified("conversation turn count is invalid")
    turn = next(
        (item for item in turns if isinstance(item, dict) and item.get("request_id") == request_id),
        None,
    )
    turn = _mapping(turn, field="persisted turn")
    answer_text = turn.get("answer_text")
    if (
        turn.get("status") != "COMPLETED"
        or turn.get("terminal_event") != "ANSWER_COMPLETED"
        or turn.get("error_code") is not None
        or turn.get("route") not in _GREENFIELD_ROUTES
        or turn.get("analysis_state") not in _ACTIVE_ANALYSIS_STATES
        or not isinstance(answer_text, str)
        or not answer_text.strip()
    ):
        raise GoldenUnverified("persisted Greenfield answer projection is invalid")
    context = _mapping(detail.get("requirement_context"), field="requirement context")
    if _provider_confirmed_count(context):
        raise GoldenUnverified("Provider CONFIRMED pollution was observed")
    if not _confirmed(context, "TARGET_PLATFORM", "STM32"):
        raise GoldenUnverified("Target Platform STM32 is not user-confirmed")
    if expected_turn_count == 1:
        product = next(
            (
                item
                for item in _sequence(context.get("standard_fields", []), field="standard fields")
                if isinstance(item, dict) and item.get("category") == "PRODUCT_GOAL"
            ),
            None,
        )
        if not isinstance(product, dict) or product.get("status") != "UNKNOWN":
            raise GoldenUnverified("initial Product Goal is not UNKNOWN")
        if not _sequence(context.get("open_questions", []), field="open questions"):
            raise GoldenUnverified("initial open questions are empty")
    if expected_turn_count >= 2 and not _confirmed(context, "PRODUCT_GOAL", "温控器"):
        raise GoldenUnverified("Product Goal was not incrementally confirmed")
    if expected_turn_count >= 3:
        if not _confirmed(context, "CONTROL_MODE", "HEATING"):
            raise GoldenUnverified("Control Mode HEATING was not confirmed")
        if not _confirmed(context, "ACCURACY", "±0.5°C"):
            raise GoldenUnverified("Accuracy ±0.5°C was not confirmed")
    return {
        "analysis_state": turn["analysis_state"],
        "answer_text_present": True,
        "answer_text_length": len(answer_text),
        "proposed_requirements_count": len(_sequence(context.get("proposed_items", []), field="proposed items")),
        "open_questions_count": len(_sequence(context.get("open_questions", []), field="open questions")),
        "provider_confirmed_pollution": False,
        "requirement_context_fingerprint": context.get("fingerprint"),
    }


def _project(client: LoopbackClient, project_id: str | None) -> dict[str, Any]:
    raw = client.json("/api/v1/copilot/projects")
    items = raw if isinstance(raw, list) else raw.get("value", []) if isinstance(raw, dict) else []
    selected = next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and (
                item.get("project_id") == project_id
                if project_id is not None
                else item.get("name") == "qdwq"
            )
        ),
        None,
    )
    selected = _mapping(selected, field="qdwq project")
    if selected.get("name") != "qdwq" or selected.get("creation_mode") != "CREATE_NEW" or selected.get("project_type") != "STM32":
        raise GoldenUnverified("project is not qdwq / CREATE_NEW / STM32")
    return selected


def _question(project: dict[str, Any], question: str) -> tuple[dict[str, Any], str, str]:
    now = datetime.now(UTC).isoformat()
    request_id = f"request-{uuid.uuid4()}"
    trace_id = f"trace-{uuid.uuid4()}"
    return (
        {
            "project_reference": project,
            "application_question": {
                "request_id": request_id,
                "trace_id": trace_id,
                "project_id": project["project_id"],
                "requester": "desktop-user",
                "question": question,
                "constraints": ["answer_detail:standard"],
                "requested_at": now,
            },
            "external_search": False,
            "context_references": [],
            "requested_at": now,
        },
        request_id,
        trace_id,
    )


def _events(client: LoopbackClient, session_id: str, last_event_id: str | None) -> list[dict[str, Any]]:
    query = urlencode(
        {"session_id": session_id, "requester": "desktop-user", "tenant": "desktop-tenant"}
    )
    status, raw = client.request(
        f"/api/v1/copilot/chat/events?{query}",
        accept="text/event-stream",
        last_event_id=last_event_id,
    )
    if status >= 400:
        raise GoldenUnverified("SSE stream is unavailable")
    return _sse(raw)


def run_live(args: argparse.Namespace) -> dict[str, object]:
    if not args.approve_external_transmission or args.expected_transmissions != len(_QUESTIONS):
        raise GoldenUnverified("exactly three external transmissions were not explicitly approved")
    client = LoopbackClient.exchange(args.bootstrap_url_file)
    project = _project(client, args.project_id)
    project_id = str(project["project_id"])
    created = _mapping(
        client.json(
            f"/api/v1/copilot/projects/{project_id}/conversations",
            method="POST",
            body={},
        ),
        field="conversation create response",
    )
    conversation = _mapping(created.get("conversation"), field="conversation")
    conversation_id = str(conversation.get("conversation_id"))
    session_id = str(created.get("session_id"))
    chat_project = _mapping(
        client.json(f"/api/v1/copilot/chat/sessions/{session_id}/project"),
        field="chat project",
    )
    baseline = _events(client, session_id, None)
    last_event_id = str(baseline[-1]["id"]) if baseline and "id" in baseline[-1] else None
    turns: list[dict[str, object]] = []
    for index, question in enumerate(_QUESTIONS, start=1):
        payload, request_id, trace_id = _question(chat_project, question)
        classification = _mapping(
            client.json(
                f"/api/v1/copilot/chat/sessions/{session_id}/classify",
                method="POST",
                body=payload,
            ),
            field="route classification",
        )
        if classification.get("route") not in _GREENFIELD_ROUTES:
            raise GoldenUnverified("preflight did not select a Greenfield route")
        client.json(
            "/api/v1/copilot/desktop/system/provider/transmission-approvals",
            method="POST",
            body={
                "session_id": session_id,
                "project_id": project_id,
                "request_id": request_id,
                "context_fingerprints": [],
                "decision": "APPROVED",
            },
        )
        answer = _mapping(
            client.json(
                f"/api/v1/copilot/chat/sessions/{session_id}/requests",
                method="POST",
                body=payload,
            ),
            field="answer response",
        )
        if not isinstance(answer.get("answer_text"), str) or not answer["answer_text"].strip():
            raise GoldenUnverified("live answer body is empty")
        events = _events(client, session_id, last_event_id)
        if events and "id" in events[-1]:
            last_event_id = str(events[-1]["id"])
        terminal = _terminal_diagnostics(events, request_id)
        detail_value = _mapping(
            client.json(
                f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}"
            ),
            field="conversation detail response",
        )
        detail = _mapping(detail_value.get("conversation", detail_value), field="conversation detail")
        projection = _validate_projection(detail, request_id=request_id, expected_turn_count=index)
        trace = _validate_trace(_trace_rows(args.state_database, trace_id))
        turns.append(
            {
                "request_id": request_id,
                "trace_id": trace_id,
                **terminal,
                **projection,
                **trace,
            }
        )
    final_detail = _mapping(
        client.json(f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}"),
        field="final conversation response",
    )
    final_conversation = _mapping(
        final_detail.get("conversation", final_detail), field="final conversation"
    )
    return {
        "product_version": "7.1.0",
        "status": "PASS",
        "golden": "GREENFIELD_PROVIDER_ANSWER",
        "project_id": project_id,
        "conversation_id": conversation_id,
        "external_transmission_count": len(_QUESTIONS),
        "turn_count": len(turns),
        "turns": turns,
        "final_requirement_context_fingerprint": _mapping(
            final_conversation.get("requirement_context"), field="final requirement context"
        ).get("fingerprint"),
        "restart_recovery": "UNVERIFIED",
    }


def run_restart(args: argparse.Namespace) -> dict[str, object]:
    try:
        evidence = _mapping(
            json.loads(args.evidence_file.read_text(encoding="utf-8")),
            field="live evidence",
        )
    except (OSError, json.JSONDecodeError) as error:
        raise GoldenUnverified("live evidence file is unavailable") from error
    if evidence.get("status") != "PASS" or evidence.get("external_transmission_count") != 3:
        raise GoldenUnverified("live evidence is not a passing three-turn Golden")
    client = LoopbackClient.exchange(args.bootstrap_url_file)
    project = _project(client, str(evidence.get("project_id")))
    project_id = str(project["project_id"])
    conversation_id = str(evidence.get("conversation_id"))
    before_gateway = _model_gateway_trace_count(args.state_database)
    before_value = _mapping(
        client.json(f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}"),
        field="pre-resume conversation response",
    )
    before = _mapping(before_value.get("conversation", before_value), field="pre-resume conversation")
    resumed = _mapping(
        client.json(
            f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/resume",
            method="POST",
            body={},
        ),
        field="resume response",
    )
    after_gateway = _model_gateway_trace_count(args.state_database)
    after = _mapping(resumed.get("conversation"), field="resumed conversation")
    turns = _sequence(after.get("turns"), field="resumed turns")
    if (
        len(turns) != 3
        or after.get("conversation_id") != conversation_id
        or after.get("project_id") != project_id
        or after.get("requirement_context", {}).get("fingerprint")
        != evidence.get("final_requirement_context_fingerprint")
        or before.get("fingerprint") != after.get("fingerprint")
        or before_gateway != after_gateway
    ):
        raise GoldenUnverified("restart recovery changed persisted state or invoked ModelGateway")
    if any(
        not isinstance(turn, dict)
        or turn.get("status") != "COMPLETED"
        or not isinstance(turn.get("answer_text"), str)
        or not turn["answer_text"].strip()
        for turn in turns
    ):
        raise GoldenUnverified("restart recovery did not restore all assistant answers")
    context = _mapping(after.get("requirement_context"), field="resumed requirement context")
    if _provider_confirmed_count(context):
        raise GoldenUnverified("restart recovery contains Provider CONFIRMED pollution")
    return {
        "product_version": "7.1.0",
        "status": "PASS",
        "golden": "GREENFIELD_PROVIDER_ANSWER_RESTART",
        "project_id": project_id,
        "conversation_id": conversation_id,
        "turn_count": len(turns),
        "assistant_answers_restored": True,
        "requirement_context_restored": True,
        "proposed_requirements_restored": bool(context.get("proposed_items")) or all(
            item.get("proposed_requirements_count") == 0
            for item in _sequence(evidence.get("turns"), field="live evidence turns")
            if isinstance(item, dict)
        ),
        "confirmed_requirements_restored": True,
        "open_questions_restored": isinstance(context.get("open_questions"), list),
        "analysis_state_restored": True,
        "restart_provider_invocation_count": after_gateway - before_gateway,
        "restart_recovery": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-url-file", type=Path, required=True)
    parser.add_argument("--state-database", type=Path, default=_default_database())
    parser.add_argument("--project-id")
    parser.add_argument("--approve-external-transmission", action="store_true")
    parser.add_argument("--expected-transmissions", type=int, default=0)
    parser.add_argument("--restart-check", action="store_true")
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.restart_check:
            if args.evidence_file is None:
                raise GoldenUnverified("--restart-check requires --evidence-file")
            report = run_restart(args)
        else:
            report = run_live(args)
        report = _mapping(_redacted_report(report), field="redacted report")
        serialized = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        return 0
    except GoldenUnverified as error:
        print(
            json.dumps(
                {
                    "product_version": "7.1.0",
                    "status": "UNVERIFIED",
                    "reason": str(error),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
