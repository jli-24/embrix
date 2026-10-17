"""Verify the existing Desktop Copilot chat SSE lifecycle against a running API.

This operator script creates one real conversation and submits one question. It
does not create projects, select a provider, or provide an answer. Provider and
Context availability remain server-side concerns and are reported as structured
HTTP failures.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _headers(requester: str, tenant: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
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
) -> tuple[int, dict[str, str], bytes]:
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(base_url.rstrip("/") + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    except URLError as error:
        raise RuntimeError("chat API is unreachable") from error


def _json(response: tuple[int, dict[str, str], bytes]) -> object:
    status, _, body = response
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"API returned non-JSON status={status}") from error
    if status >= 400:
        raise RuntimeError(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return value


def _sse(response: tuple[int, dict[str, str], bytes]) -> list[dict[str, object]]:
    status, _, body = response
    if status >= 400:
        raise RuntimeError(body.decode("utf-8", errors="replace")[:256])
    events: list[dict[str, object]] = []
    for block in body.decode("utf-8", errors="replace").split("\n\n"):
        fields: dict[str, object] = {"data": ""}
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("id:"):
                fields["id"] = line[3:].strip()
            elif line.startswith("event:"):
                fields["event"] = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if "event" not in fields:
            continue
        fields["data"] = json.loads("\n".join(data) or "{}")
        events.append(fields)
    return events


def _event_ids(events: list[dict[str, object]]) -> list[str]:
    return [value for event in events if isinstance(value := event.get("id"), str) and value]


def verify(args: argparse.Namespace) -> int:
    headers = _headers(args.requester, args.tenant)
    projects = _json(_request(args.base_url, "/api/v1/copilot/projects", headers=headers))
    project_values = (
        projects
        if isinstance(projects, list)
        else projects.get("value", [])
        if isinstance(projects, dict) and isinstance(projects.get("value"), list)
        else []
    )
    if args.project_id:
        project = next(
            (
                item
                for item in project_values
                if isinstance(item, dict) and item.get("project_id") == args.project_id
            ),
            None,
        )
    else:
        project = next(
            (
                item
                for item in project_values
                if isinstance(item, dict) and item.get("name") == args.project_name
            ),
            None,
        )
    if not isinstance(project, dict) or not isinstance(project.get("project_id"), str):
        raise RuntimeError("requested project was not found")
    project_id = project["project_id"]

    conversation = _json(
        _request(
            args.base_url,
            f"/api/v1/copilot/projects/{project_id}/conversations",
            headers=headers,
            method="POST",
            body={},
        )
    )
    if not isinstance(conversation, dict):
        raise RuntimeError("conversation response is invalid")
    detail = conversation.get("conversation")
    conversation_id = detail.get("conversation_id") if isinstance(detail, dict) else None
    if not isinstance(conversation_id, str):
        raise RuntimeError("conversation did not return an identity")
    session_id = conversation.get("session_id")
    if not isinstance(session_id, str):
        raise RuntimeError("conversation did not return a session")
    if conversation.get("chat_available") is not True:
        raise RuntimeError(json.dumps({"status": "unavailable", "conversation": conversation}, ensure_ascii=False))

    chat_headers = {**headers, "Accept": "application/json"}
    chat_project = _json(_request(args.base_url, f"/api/v1/copilot/chat/sessions/{session_id}/project", headers=chat_headers))
    if not isinstance(chat_project, dict):
        raise RuntimeError("chat project response is invalid")
    references = _json(_request(args.base_url, f"/api/v1/copilot/chat/sessions/{session_id}/references", headers=chat_headers))
    if not isinstance(references, list):
        references = []
    question = {
        "request_id": f"request-{uuid.uuid4()}",
        "trace_id": f"trace-{uuid.uuid4()}",
        "project_id": project_id,
        "requester": args.requester,
        "question": args.question,
        "constraints": ["answer_detail:standard"],
        "requested_at": datetime.now(UTC).isoformat(),
    }
    answer = _json(
        _request(
            args.base_url,
            f"/api/v1/copilot/chat/sessions/{session_id}/requests",
            headers=chat_headers,
            method="POST",
            body={
                "project_reference": chat_project,
                "application_question": question,
                "context_references": references,
                "requested_at": question["requested_at"],
            },
        )
    )
    if not isinstance(answer, dict):
        raise RuntimeError("chat answer response is invalid")
    answer_text = answer.get("answer_text")
    evidence = answer.get("evidence_details")
    if not isinstance(answer_text, str) or not answer_text.strip() or not isinstance(evidence, list) or not evidence:
        raise RuntimeError("chat answer lacks grounded evidence")

    first = _sse(_request(args.base_url, f"/api/v1/copilot/chat/events?session_id={session_id}&requester={args.requester}&tenant={args.tenant}", headers={**headers, "Accept": "text/event-stream"}))
    names = [event.get("event") for event in first]
    required = {"CHAT_STARTED", "ANSWER_CHUNK", "ANSWER_COMPLETED"}
    if not required.issubset(set(names)):
        raise RuntimeError(json.dumps({"status": "invalid_events", "events": names}, ensure_ascii=False))
    ids = _event_ids(first)
    if not ids:
        raise RuntimeError("SSE returned no event cursor")

    replay = _sse(
        _request(
            args.base_url,
            f"/api/v1/copilot/chat/events?session_id={session_id}&requester={args.requester}&tenant={args.tenant}",
            headers={**headers, "Accept": "text/event-stream", "Last-Event-ID": ids[0]},
        )
    )
    if replay and any(event.get("id") == ids[0] for event in replay):
        raise RuntimeError("SSE replay included the cursor event")

    resync = _sse(
        _request(
            args.base_url,
            f"/api/v1/copilot/chat/events?session_id={session_id}&requester={args.requester}&tenant={args.tenant}",
            headers={**headers, "Accept": "text/event-stream", "Last-Event-ID": "missing-cursor"},
        )
    )
    if not any(event.get("event") == "RESYNC_REQUIRED" for event in resync):
        raise RuntimeError("SSE invalid cursor did not request resync")
    print(json.dumps({
        "status": "verified",
        "project_id": project_id,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "trace_id": question["trace_id"],
        "events": names,
        "evidence_references": [
            item.get("reference_id")
            for item in evidence
            if isinstance(item, dict) and isinstance(item.get("reference_id"), str)
        ],
        "replay_events": [event.get("event") for event in replay],
        "resync": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id")
    parser.add_argument("--project-name", default="循迹智能小车")
    parser.add_argument("--question", default="为什么 I2C 读取失败？")
    parser.add_argument("--requester", default="desktop-user")
    parser.add_argument("--tenant", default="desktop-tenant")
    args = parser.parse_args()
    try:
        return verify(args)
    except RuntimeError as error:
        print(json.dumps({"status": "unavailable", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
