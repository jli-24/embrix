"""Run the v7.1 clarification/continuation Golden against a real running app.

This is an operator-facing HTTP probe for the packaged ``EmbeddedCopilot.exe``.
It deliberately does not fabricate provider, approval, SQLite or restart
evidence.  The process must be started by the operator, and the same
conversation/session identities are used for each continuation assertion.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class GoldenCase:
    name: str
    status: str
    detail: str
    conversation_id: str | None = None
    session_id: str | None = None
    trace_ids: tuple[str, ...] = ()


def _headers(requester: str, tenant: str, accept: str = "application/json") -> dict[str, str]:
    return {
        "Accept": accept,
        "Content-Type": "application/json",
        "X-Copilot-Requester": requester,
        "X-Environment-Tenant": tenant,
    }


def _request(
    base_url: str,
    path: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    body: object | None = None,
    last_event_id: str | None = None,
) -> tuple[int, bytes]:
    request_headers = dict(headers)
    if last_event_id:
        request_headers["Last-Event-ID"] = last_event_id
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(base_url.rstrip("/") + path, data=payload, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except URLError as error:
        raise RuntimeError("EmbeddedCopilot.exe API is unreachable") from error


def _json(status_body: tuple[int, bytes]) -> object:
    status, body = status_body
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"non-JSON response status={status}") from error
    if status >= 400:
        raise RuntimeError(json.dumps({"status": status, "body": value}, ensure_ascii=False, sort_keys=True))
    return value


def _sse(status_body: tuple[int, bytes]) -> list[dict[str, object]]:
    status, body = status_body
    if status >= 400:
        raise RuntimeError(body.decode("utf-8", errors="replace")[:512])
    events: list[dict[str, object]] = []
    for block in body.decode("utf-8", errors="replace").split("\n\n"):
        event: dict[str, object] = {"data": {}}
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("id:"):
                event["id"] = line[3:].strip()
            elif line.startswith("event:"):
                event["event"] = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if "event" in event:
            event["data"] = json.loads("\n".join(data) or "{}")
            events.append(event)
    return events


def _project(base_url: str, args: argparse.Namespace, headers: dict[str, str]) -> dict[str, object]:
    values = _json(_request(base_url, "/api/v1/copilot/projects", headers=headers))
    items = values if isinstance(values, list) else values.get("value", []) if isinstance(values, dict) else []
    if args.project_id:
        selected = next((item for item in items if isinstance(item, dict) and item.get("project_id") == args.project_id), None)
    else:
        selected = next((item for item in items if isinstance(item, dict) and item.get("name") == args.project_name), None)
    if not isinstance(selected, dict) or not isinstance(selected.get("project_id"), str):
        raise RuntimeError("requested project was not found")
    return selected


def _conversation(base_url: str, project_id: str, args: argparse.Namespace, headers: dict[str, str]) -> tuple[str, str]:
    value = _json(_request(base_url, f"/api/v1/copilot/projects/{project_id}/conversations", headers=headers, method="POST", body={}))
    if not isinstance(value, dict):
        raise RuntimeError("conversation response is invalid")
    detail = value.get("conversation")
    conversation_id = detail.get("conversation_id") if isinstance(detail, dict) else None
    session_id = value.get("session_id")
    if not isinstance(conversation_id, str) or not isinstance(session_id, str):
        raise RuntimeError("conversation did not return both conversation_id and session_id")
    return conversation_id, session_id


def _question(project: dict[str, object], args: argparse.Namespace, question: str) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "project_reference": project,
        "application_question": {
            "request_id": f"request-{uuid.uuid4()}",
            "trace_id": f"trace-{uuid.uuid4()}",
            "project_id": project["project_id"],
            "requester": args.requester,
            "question": question,
            "constraints": ["answer_detail:standard"],
            "requested_at": now,
        },
        "context_references": [],
        "requested_at": now,
    }


def _conversation_detail(base_url: str, project_id: str, conversation_id: str, headers: dict[str, str]) -> dict[str, object]:
    value = _json(_request(base_url, f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}", headers=headers))
    if not isinstance(value, dict):
        raise RuntimeError("conversation detail is invalid")
    return value.get("conversation", value) if isinstance(value.get("conversation", value), dict) else {}


def _run_case(base_url: str, project: dict[str, object], args: argparse.Namespace, *, name: str, clarification: str, continuation: str, expected_route: str) -> GoldenCase:
    headers = _headers(args.requester, args.tenant)
    project_id = str(project["project_id"])
    conversation_id, session_id = _conversation(base_url, project_id, args, headers)
    chat_project = _json(_request(base_url, f"/api/v1/copilot/chat/sessions/{session_id}/project", headers=headers))
    if not isinstance(chat_project, dict):
        raise RuntimeError("chat project projection is invalid")

    first = _question(chat_project, args, clarification)
    first_route = _json(_request(base_url, f"/api/v1/copilot/chat/sessions/{session_id}/classify", headers=headers, method="POST", body=first))
    if not isinstance(first_route, dict) or first_route.get("route") != "CLARIFICATION_REQUIRED":
        raise RuntimeError(f"{name}: ambiguous preflight did not classify as CLARIFICATION_REQUIRED")
    first_trace = str(first["application_question"]["trace_id"])
    first_status, first_body = _request(base_url, f"/api/v1/copilot/chat/sessions/{session_id}/requests", headers=headers, method="POST", body=first)
    if first_status != 422 or json.loads(first_body.decode("utf-8")).get("error_code") != "CLARIFICATION_REQUIRED":
        raise RuntimeError(f"{name}: clarification submit did not return compatible 422 terminal mapping")

    query = urlencode({"session_id": session_id, "requester": args.requester, "tenant": args.tenant})
    events = _sse(_request(base_url, f"/api/v1/copilot/chat/events?{query}", headers=_headers(args.requester, args.tenant, "text/event-stream")))
    terminals = [event for event in events if event.get("event") == "ANSWER_FAILED"]
    if len(terminals) != 1:
        raise RuntimeError(f"{name}: expected exactly one ANSWER_FAILED, got {len(terminals)}")
    terminal_data = terminals[0].get("data")
    if not isinstance(terminal_data, dict) or terminal_data.get("analysis_state") != "WAITING_FOR_CLARIFICATION" or terminal_data.get("reason_code") != "CLARIFICATION_REQUIRED":
        raise RuntimeError(f"{name}: terminal event was not projected as waiting clarification")

    stored = _conversation_detail(base_url, project_id, conversation_id, headers)
    turns = stored.get("turns", [])
    latest = turns[-1] if isinstance(turns, list) and turns else None
    if not isinstance(latest, dict) or latest.get("status") != "WAITING_FOR_CLARIFICATION" or latest.get("error_code") is not None:
        raise RuntimeError(f"{name}: SQLite projection did not persist waiting clarification")

    second = _question(chat_project, args, continuation)
    second_route = _json(_request(base_url, f"/api/v1/copilot/chat/sessions/{session_id}/classify", headers=headers, method="POST", body=second))
    if not isinstance(second_route, dict) or second_route.get("route") != expected_route:
        raise RuntimeError(f"{name}: continuation route was {second_route!r}, expected {expected_route}")
    second_status, second_body = _request(base_url, f"/api/v1/copilot/chat/sessions/{session_id}/requests", headers=headers, method="POST", body=second)
    if second_status >= 400:
        raise RuntimeError(f"{name}: continuation execution unavailable status={second_status} body={second_body.decode('utf-8', errors='replace')[:256]}")
    after = _conversation_detail(base_url, project_id, conversation_id, headers)
    after_turns = after.get("turns", [])
    if not isinstance(after_turns, list) or len(after_turns) < 2:
        raise RuntimeError(f"{name}: continuation created no second turn")
    latest_after = after_turns[-1]
    if not isinstance(latest_after, dict) or latest_after.get("continuation_of_turn_id") != latest.get("turn_id") or latest_after.get("previous_analysis_state") != "WAITING_FOR_CLARIFICATION":
        raise RuntimeError(f"{name}: continuation metadata was not structured")
    return GoldenCase(name, "PASS", "clarification, SSE terminal, SQLite persistence and structured continuation verified", conversation_id, session_id, (first_trace, str(second["application_question"]["trace_id"])))


def run(args: argparse.Namespace) -> dict[str, object]:
    headers = _headers(args.requester, args.tenant)
    project = _project(args.base_url, args, headers)
    if args.restart_check:
        if not args.conversation_id or not args.session_id:
            raise RuntimeError("--restart-check requires --conversation-id and --session-id from the first run")
        detail = _conversation_detail(args.base_url, str(project["project_id"]), args.conversation_id, headers)
        turns = detail.get("turns", [])
        if detail.get("conversation_id") != args.conversation_id or detail.get("project_id") != project["project_id"] or not isinstance(turns, list) or not turns:
            raise RuntimeError("restart recovery did not restore the same project/conversation with turns")
        return {
            "product_version": "7.1.0",
            "status": "PASS",
            "project_id": project.get("project_id"),
            "conversation_id": args.conversation_id,
            "session_id": args.session_id,
            "restart_recovery": "PASS — same project/conversation and persisted turns observed after operator restart",
        }
    cases: list[GoldenCase] = []
    definitions = (
        ("Clarification -> Greenfield", "帮我看看这个", "我要从零设计 STM32 启动架构", "GREENFIELD"),
        ("Clarification -> Brownfield", "这个函数有问题", "分析当前已有工程里的 sensor_read 调用关系", "BROWNFIELD"),
    )
    for name, clarification, continuation, route in definitions:
        try:
            cases.append(_run_case(args.base_url, project, args, name=name, clarification=clarification, continuation=continuation, expected_route=route))
        except (RuntimeError, ValueError) as error:
            cases.append(GoldenCase(name, "UNVERIFIED", str(error)))
    return {
        "product_version": "7.1.0",
        "status": "PASS" if cases and all(item.status == "PASS" for item in cases) else "UNVERIFIED",
        "project_id": project.get("project_id"),
        "cases": [asdict(item) for item in cases],
        "restart_recovery": "OPERATOR ACTION REQUIRED: restart the packaged EXE, then re-run with --conversation-id/--session-id or inspect the listed IDs in SQLite/SSE evidence",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id")
    parser.add_argument("--project-name", default="循迹智能小车")
    parser.add_argument("--requester", default="desktop-user")
    parser.add_argument("--tenant", default="desktop-tenant")
    parser.add_argument("--restart-check", action="store_true")
    parser.add_argument("--conversation-id")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except RuntimeError as error:
        print(json.dumps({"product_version": "7.1.0", "status": "UNVERIFIED", "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
