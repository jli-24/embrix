"""Run one redacted CREATE_NEW/STM32 canonical Golden against the v7.1 EXE."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import sqlite3
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

PROVIDER_BUDGET = 1
GOLDEN_QUESTION = "电竞智能小车工程分析"
_GREENFIELD_ROUTES = {"GREENFIELD", "GREENFIELD_DISCOVERY", "GREENFIELD_DESIGN"}
_HEALTH_STATUSES = {"ok", "degraded", "unavailable"}
_EVIDENCE_STAGES = (
    "E0_EVIDENCE_BUILD_ENTER",
    "E1_REQUIREMENT_CONTEXT_BIND",
    "E2_PROJECT_CONTEXT_BIND",
    "E3_EVIDENCE_COLLECTION",
    "E4_EVIDENCE_VALIDATE",
    "E5_APPLICATION_ANSWER_COMMIT",
)


class GoldenUnverified(RuntimeError):
    """A required directly observed Golden precondition was absent."""


def request_id_hash(request_id: str) -> str:
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def project_is_canonical(
    project: object,
    *,
    expected_project_id: str | None = None,
) -> bool:
    """Validate a CREATE_NEW STM32 project without a production-row identity.

    ``expected_project_id`` is used only after the isolated CREATE_NEW call to
    bind subsequent projections.  Leaving it unset accepts any well-formed
    isolated project identifier, which is essential for an empty state DB.
    """

    if not isinstance(project, dict):
        return False
    project_id = project.get("project_id")
    if not isinstance(project_id, str) or not project_id.startswith("project-"):
        return False
    if expected_project_id is not None and project_id != expected_project_id:
        return False
    return (
        project.get("creation_mode") == "CREATE_NEW"
        and project.get("project_type") == "STM32"
    )


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GoldenUnverified(f"{field} is invalid")
    return value


def _items(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("value"), list):
        return value["value"]
    return []


def _response_mapping(value: object, *, field: str) -> dict[str, Any]:
    """Accept either a direct Project Catalog view or a wrapped projection."""

    checked = _mapping(value, field=field)
    nested = checked.get(field.casefold())
    return _mapping(nested, field=field) if isinstance(nested, dict) else checked


def create_isolated_project(
    client: LoopbackClient,
    *,
    project_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, object]]:
    """Create and resolve a Golden project through the public CREATE_NEW flow.

    This function deliberately does not inspect SQLite or accept a project ID
    from formal production state.  It uses only the live Project Catalog API,
    so an empty isolated database is a valid starting point.
    """

    locations_status, locations_value = client.request(
        "/api/v1/copilot/projects/locations"
    )
    if locations_status >= 400:
        raise GoldenUnverified("isolated project locations request was rejected")
    locations = [
        item
        for item in _items(locations_value)
        if isinstance(item, dict)
        and isinstance(item.get("location_id"), str)
        and isinstance(item.get("fingerprint"), str)
        and item.get("availability") in {"AVAILABLE", "ACTIVE"}
    ]
    if not locations:
        raise GoldenUnverified("isolated project location is unavailable")
    location = sorted(
        locations,
        key=lambda item: (str(item["location_id"]), str(item["fingerprint"])),
    )[0]
    name = project_name or f"v71-recovery-seed-{uuid.uuid4().hex[:12]}"
    preview = client.require(
        "/api/v1/copilot/projects/previews",
        method="POST",
        body={
            "name": name,
            "project_type": "STM32",
            "parent_location": {
                "location_id": location["location_id"],
                "fingerprint": location["fingerprint"],
            },
            "creation_mode": "CREATE_NEW",
        },
        field="isolated project preview",
    )
    preview_id = preview.get("preview_id")
    preview_fingerprint = preview.get("fingerprint")
    if not isinstance(preview_id, str) or not isinstance(preview_fingerprint, str):
        raise GoldenUnverified("isolated project preview identity is invalid")
    created_value = client.require(
        "/api/v1/copilot/projects",
        method="POST",
        body={
            "preview_id": preview_id,
            "preview_fingerprint": preview_fingerprint,
            "confirmed_at": datetime.now(UTC).isoformat(),
        },
        field="isolated project create",
    )
    project = _response_mapping(created_value, field="PROJECT")
    project_id = project.get("project_id")
    if not project_is_canonical(project) or not isinstance(project_id, str):
        raise GoldenUnverified("isolated project create returned an invalid identity")
    resolved = client.require(
        f"/api/v1/copilot/projects/{project_id}",
        field="isolated project resolve",
    )
    resolved_project = _response_mapping(resolved, field="PROJECT")
    if not project_is_canonical(resolved_project, expected_project_id=project_id):
        raise GoldenUnverified("isolated project resolve identity mismatch")
    return resolved_project, {
        "isolated_project_created": True,
        "isolated_project_id_hash": hashlib.sha256(project_id.encode("utf-8")).hexdigest(),
        "project_creation_path": "CREATE_NEW",
        "project_resolve": "PASS",
    }


class LoopbackClient:
    def __init__(self, handoff: str) -> None:
        parsed = urlsplit(handoff)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.fragment:
            raise GoldenUnverified("bootstrap handoff is invalid")
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.nonce = parsed.fragment
        self._opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @classmethod
    def from_file(cls, path: Path) -> LoopbackClient:
        try:
            return cls(path.read_text(encoding="utf-8").strip())
        except OSError as error:
            raise GoldenUnverified("bootstrap handoff is unavailable") from error

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: object | None = None,
        accept: str = "application/json",
    ) -> tuple[int, object]:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=payload,
            headers={"Accept": accept, "Content-Type": "application/json"},
            method=method,
        )
        try:
            with self._opener.open(request, timeout=180) as response:
                status = response.status
                raw = response.read()
        except HTTPError as error:
            status = error.code
            raw = error.read()
        except URLError as error:
            raise GoldenUnverified("packaged EXE API is unreachable") from error
        try:
            return status, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return status, {"error": "NON_JSON_RESPONSE"}

    def require(
        self,
        path: str,
        *,
        method: str = "GET",
        body: object | None = None,
        field: str,
    ) -> dict[str, Any]:
        status, value = self.request(path, method=method, body=body)
        if status >= 400:
            code = value.get("error", "HTTP_ERROR") if isinstance(value, dict) else "HTTP_ERROR"
            raise GoldenUnverified(f"{field} rejected status={status} code={code}")
        return _mapping(value, field=field)


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _trace_rows(database: Path, trace_id: str) -> list[dict[str, Any]]:
    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            values = connection.execute(
                "SELECT body FROM desktop_chat_trace_events WHERE trace_id = ? ORDER BY rowid",
                (trace_id,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise GoldenUnverified("trace database is unavailable") from error
    rows: list[dict[str, Any]] = []
    for (body,) in values:
        try:
            value = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _trace_status(by_stage: dict[str, dict[str, Any]], stage: str) -> str:
    value = by_stage.get(stage)
    if value is None:
        return "UNOBSERVED"
    gate = value.get("gate_result")
    return "PASS" if gate in {"PASS", "TERMINAL", "EMPTY_PROJECT", "GREENFIELD_CONTEXT", "NOT_APPLICABLE_FOR_GREENFIELD"} else str(gate or "UNOBSERVED")


def _diagnostic_status(
    rows: list[dict[str, Any]],
    stage: str,
) -> str:
    for row in reversed(rows):
        observed = next(
            (
                row.get(field)
                for field in (
                    "application_runtime_stage",
                    "application_runtime_outer_stage",
                    "binding_engineering_qa_stage",
                    "engineering_qa_stage",
                    "prompt_pipeline_stage",
                    "stage",
                )
                if row.get(field) is not None
            ),
            None,
        )
        if observed == stage:
            return str(row.get("telemetry_status", row.get("status", "UNOBSERVED")))
    return "UNOBSERVED"


def _validation_vector(rows: list[dict[str, Any]]) -> dict[str, object]:
    allowed_fields = (
        "location_hash",
        "error_type",
        "received_type",
        "message_code",
    )
    row = next(
        (
            item
            for item in reversed(rows)
            if item.get("event_name")
            == "model_request_validation_boundary_telemetry"
        ),
        None,
    )
    if row is None:
        return {"validation_error_count": 0, "validation_errors": []}
    errors = row.get("validation_errors")
    sanitized = [
        {field: item[field] for field in allowed_fields if field in item}
        for item in errors
        if isinstance(item, dict)
    ] if isinstance(errors, list) else []
    count = row.get("validation_error_count")
    return {
        "validation_error_count": count if isinstance(count, int) else len(sanitized),
        "validation_errors": sanitized,
    }


def _first_bad_stage(
    trace: dict[str, dict[str, Any]],
    runtime_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    response_rows: list[dict[str, Any]],
) -> str:
    ordered_runtime = (
        "A0_REQUEST_ENTER",
        "A1_CONVERSATION_LOAD",
        "A2_PROJECT_CONTEXT_BIND",
        "A3_REQUIREMENT_CONTEXT_BIND",
        "A4_TASK_MODE_RESOLVE",
        "A5_ANSWER_BINDING_CREATE",
        "B0_BINDING_ANSWER_CALL_START",
        "B1_ENGINEERING_QA_SERVICE_RESOLVE_START",
        "B2_ENGINEERING_QA_SERVICE_RESOLVE_END",
        "B3_ENGINEERING_QA_METHOD_CALL_START",
        "Q0_ENGINEERING_QA_ENTER",
        "Q1_PROMPT_SERVICE_RESOLVE",
        "Q2_PROMPT_BUILD_REQUEST",
        "A6_PROMPT_REQUEST_BUILD",
        "A7_PROMPT_BUILDER_ENTER",
        "P5_MODEL_REQUEST_CREATED",
        "Q3_MODEL_REQUEST_CONSTRUCTION",
        "Q4_ENGINEERING_QA_EXCEPTION",
        "B4_ENGINEERING_QA_EXCEPTION",
        "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
        "X4_OUTER_EXCEPTION_MAPPING",
    )
    for stage in ordered_runtime:
        if _diagnostic_status(runtime_rows, stage) == "FAIL":
            return stage
    for stage in _EVIDENCE_STAGES:
        if any(row.get("evidence_stage") == stage and row.get("telemetry_status") == "FAIL" for row in evidence_rows):
            return stage
    for row in response_rows:
        if row.get("telemetry_status") == "FAIL" or row.get("validation_status") == "FAIL":
            return str(row.get("response_stage", "PROVIDER_RESPONSE"))
    gateway = trace.get("MODEL_GATEWAY")
    if gateway is not None and gateway.get("gate_result") != "PASS":
        return "MODEL_GATEWAY"
    return "-"


def _exception_fields(
    gateway: dict[str, Any] | None,
    runtime_rows: list[dict[str, Any]],
) -> dict[str, str]:
    fields = (
        "original_exception_type",
        "original_exception_hash",
        "exception_layer",
        "exception_stage",
        "traceback_fingerprint",
    )
    values: dict[str, str] = {}
    for field in fields:
        raw = None if gateway is None else gateway.get(field)
        if raw in (None, "-"):
            raw = next(
                (
                    row.get(field)
                    for row in reversed(runtime_rows)
                    if row.get("telemetry_status") == "FAIL" and row.get(field) not in (None, "-")
                ),
                None,
            )
        values[field] = str(raw) if raw not in (None, "") else "-"
    return values


def _question(project: dict[str, Any], session_id: str) -> tuple[dict[str, Any], str, str]:
    project_id = project.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise GoldenUnverified("isolated project identity is unavailable")
    now = datetime.now(UTC).isoformat()
    request_id = f"request-{uuid.uuid4()}"
    trace_id = f"trace-{uuid.uuid4()}"
    return (
        {
            "project_reference": project,
            "application_question": {
                "request_id": request_id,
                "trace_id": trace_id,
                "project_id": project_id,
                "requester": "desktop-user",
                "question": GOLDEN_QUESTION,
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


def _write_resume(
    path: Path,
    *,
    conversation_id: str,
    session_id: str,
    request_id: str,
    trace_id: str,
    answer_sha256: str,
    project_context_sha256: str,
    requirement_context_sha256: str,
    trace_sha256: str,
    project_id: str | None = None,
) -> None:
    """Write local-only restart coordinates; Final Golden redacts them on output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "conversation_id": conversation_id,
                "session_id": session_id,
                "request_id": request_id,
                "trace_id": trace_id,
                **({"project_id": project_id} if isinstance(project_id, str) and project_id else {}),
                "answer_sha256": answer_sha256,
                "project_context_sha256": project_context_sha256,
                "requirement_context_sha256": requirement_context_sha256,
                "trace_sha256": trace_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> dict[str, object]:
    if not args.approve_external_transmission or args.provider_budget != PROVIDER_BUDGET:
        raise GoldenUnverified("exactly one external transmission was not approved")
    client = getattr(args, "loopback_client", None)
    if client is None:
        client = LoopbackClient.from_file(args.bootstrap_url_file)
    health_status, health_value = client.request("/api/v1/health")
    health_payload = health_value if isinstance(health_value, dict) else {}
    health = (
        "PASS"
        if health_status == 200 and health_payload.get("status") in _HEALTH_STATUSES
        else "FAIL"
    )
    if health != "PASS":
        raise GoldenUnverified("health gate failed")
    if not bool(getattr(args, "bootstrap_already_exchanged", False)):
        client.require(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            method="POST",
            body={"nonce": client.nonce},
            field="bootstrap exchange",
        )
    project, project_metadata = create_isolated_project(client)
    project_id = str(project["project_id"])
    created = client.require(
        f"/api/v1/copilot/projects/{project_id}/conversations",
        method="POST",
        body={},
        field="conversation creation",
    )
    conversation = _mapping(created.get("conversation"), field="conversation")
    conversation_id = str(conversation.get("conversation_id"))
    session_id = str(created.get("session_id"))
    chat_project = client.require(
        f"/api/v1/copilot/chat/sessions/{session_id}/project",
        field="chat project",
    )
    payload, request_id, trace_id = _question(chat_project, session_id)
    classification = client.require(
        f"/api/v1/copilot/chat/sessions/{session_id}/classify",
        method="POST",
        body=payload,
        field="classification",
    )
    if classification.get("route") not in _GREENFIELD_ROUTES:
        raise GoldenUnverified("classification is not Greenfield")
    receipt = client.require(
        "/api/v1/copilot/desktop/system/provider/transmission-approvals",
        method="POST",
        body={
            "session_id": session_id,
            "project_id": project_id,
            "request_id": request_id,
            "context_fingerprints": [],
            "decision": "APPROVED",
        },
        field="transmission approval",
    )
    receipt_ok = (
        receipt.get("request_id") == request_id
        and receipt.get("project_id") == project_id
        and receipt.get("decision") == "APPROVED"
        and receipt.get("consumed") is False
    )
    if not receipt_ok:
        raise GoldenUnverified("approval receipt binding failed")
    approval_hash = request_id_hash(request_id)
    print("approval_receipt_created=true", flush=True)
    print(f"approval_request_id_hash={approval_hash}", flush=True)
    print(f"provider_budget_remaining={PROVIDER_BUDGET}", flush=True)
    if not args.no_wait:
        print("provider_execution_gate=WAITING_FOR_EXECUTE", flush=True)
        if input().strip() != "EXECUTE":
            raise GoldenUnverified("Provider execution was not confirmed")

    answer_status, _answer_value = client.request(
        f"/api/v1/copilot/chat/sessions/{session_id}/requests",
        method="POST",
        body=payload,
    )
    detail_status, detail_value = client.request(
        f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}"
    )
    detail_payload = detail_value if detail_status < 400 and isinstance(detail_value, dict) else {}
    detail = detail_payload.get("conversation", detail_payload)
    detail = detail if isinstance(detail, dict) else {}
    turns = detail.get("turns", []) if isinstance(detail.get("turns", []), list) else []
    turn = next(
        (
            item
            for item in turns
            if isinstance(item, dict) and item.get("request_id") == request_id
        ),
        {},
    )
    trace_rows = _trace_rows(args.state_database, trace_id)
    by_stage = {
        str(item.get("stage")): item
        for item in trace_rows
        if isinstance(item.get("stage"), str)
    }
    runtime_rows = _read_json_lines(args.logs_directory / "runtime-boundary-telemetry.log")
    all_request_rows = _read_json_lines(args.logs_directory / "provider-request-boundary-telemetry.log")
    all_response_rows = _read_json_lines(args.logs_directory / "provider-response-boundary-telemetry.log")
    request_baseline = getattr(args, "provider_request_baseline", 0)
    response_baseline = getattr(args, "provider_response_baseline", 0)
    if (
        type(request_baseline) is not int
        or request_baseline < 0
        or type(response_baseline) is not int
        or response_baseline < 0
        or request_baseline > len(all_request_rows)
        or response_baseline > len(all_response_rows)
    ):
        raise GoldenUnverified("provider telemetry baseline is invalid")
    request_rows = all_request_rows[request_baseline:]
    response_rows = all_response_rows[response_baseline:]
    evidence_rows = _read_json_lines(args.logs_directory / "application-evidence-boundary-telemetry.log")
    early_rows = _read_json_lines(args.logs_directory / "early-startup-diagnostic.log")
    early = {str(item.get("stage")): item for item in early_rows}
    startup = "PASS" if all(
        early.get(stage, {}).get("status") == "PASS"
        for stage in ("E0_PROCESS_ENTRY", "E1_RUNTIME_IMPORT", "E1_LAUNCHER_INIT", "E1_BACKEND_PREPARE")
    ) else "FAIL"
    request_record = request_rows[-1] if request_rows else {}
    response_record = response_rows[-1] if response_rows else {}
    gateway = by_stage.get("MODEL_GATEWAY")
    answer_text = turn.get("answer_text") if isinstance(turn, dict) else None
    answer_length = len(answer_text) if isinstance(answer_text, str) else response_record.get("answer_text_length", "UNOBSERVED")
    validation_failure = next(
        (
            str(row.get("validation_error_code", row.get("reason_code")))
            for row in response_rows
            if (row.get("validation_status") == "FAIL" or row.get("telemetry_status") == "FAIL")
            and row.get("validation_error_code", row.get("reason_code")) not in (None, "-")
        ),
        "-",
    )
    validation = _validation_vector(runtime_rows)
    exception = _exception_fields(gateway, runtime_rows)
    terminal = by_stage.get("SSE_TERMINAL", {}).get("terminal_event")
    succeeded = (
        answer_status < 400
        and isinstance(answer_length, int)
        and answer_length > 0
        and terminal == "ANSWER_COMPLETED"
        and _trace_status(by_stage, "MODEL_GATEWAY") == "PASS"
        and len(request_rows) == 1
        and validation_failure == "-"
        and all(value == "-" for value in exception.values())
    )
    report: dict[str, object] = {
        "status": "PASS" if succeeded else "FAIL",
        "golden": "CANONICAL_ORIGINAL_EXCEPTION_CAPTURE",
        "project_id_hash": hashlib.sha256(project_id.encode("utf-8")).hexdigest(),
        **project_metadata,
        "approval_receipt_created": True,
        "approval_request_id_hash": approval_hash,
        "provider_budget_remaining_before_invocation": PROVIDER_BUDGET,
        "Startup": {
            "startup": startup,
            "bootstrap": "PASS",
            "health": health,
        },
        "Application": {
            "chat_submit": _trace_status(by_stage, "UI_SUBMIT"),
            "conversation": _trace_status(by_stage, "CONVERSATION_RESOLVE"),
            "project_resolve": _trace_status(by_stage, "PROJECT_RESOLVE"),
            "context_build": _trace_status(by_stage, "CONTEXT_BUILD"),
            "requirement_context": "PASS" if isinstance(detail.get("requirement_context"), dict) else "UNOBSERVED",
        },
        "EngineeringQA": {
            "engineering_qa_enter": _diagnostic_status(runtime_rows, "Q0_ENGINEERING_QA_ENTER"),
            "prompt_builder_enter": _diagnostic_status(runtime_rows, "A7_PROMPT_BUILDER_ENTER"),
            "model_request_created": _diagnostic_status(runtime_rows, "P5_MODEL_REQUEST_CREATED"),
        },
        "Gateway": {
            "gateway_invocation_count": 0 if gateway is None else gateway.get("gateway_invocation_count", 0),
            "provider_invocation_count": len(request_rows),
        },
        "Validation": validation,
        "Exception": exception,
        "Request": {
            "message_count": request_record.get("request_message_count", "UNOBSERVED"),
            "system_message_present": request_record.get("system_message_present", "UNOBSERVED"),
            "user_message_present": request_record.get("user_message_present", "UNOBSERVED"),
            "context_message_present": request_record.get("context_message_present", "UNOBSERVED"),
        },
        "Response": {
            "answer_text_length": answer_length,
            "validation_failure": validation_failure,
        },
        "Evidence": {
            f"E{index}": next(
                (
                    str(row.get("telemetry_status", "UNOBSERVED"))
                    for row in reversed(evidence_rows)
                    if row.get("evidence_stage") == stage
                ),
                "UNOBSERVED",
            )
            for index, stage in enumerate(_EVIDENCE_STAGES)
        },
        "first_bad_stage": _first_bad_stage(by_stage, runtime_rows, evidence_rows, response_rows),
        "root_cause": (
            "-"
            if succeeded
            else next(
                (
                    str(item.get("message_code"))
                    for item in validation["validation_errors"]
                    if isinstance(item, dict) and item.get("message_code")
                ),
                "UNVERIFIED",
            )
        ),
        "changed_files": [],
        "fix_description": "-",
        "answer_http_status": answer_status,
    }
    resume_file = getattr(args, "resume_file", None)
    if succeeded and isinstance(resume_file, Path):
        def _sha256_json(value: object) -> str:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()

        _write_resume(
            resume_file,
            conversation_id=conversation_id,
            session_id=session_id,
            request_id=request_id,
            trace_id=trace_id,
            answer_sha256=hashlib.sha256(
                str(answer_text).encode("utf-8")
            ).hexdigest(),
            project_context_sha256=_sha256_json(chat_project),
            requirement_context_sha256=_sha256_json(detail.get("requirement_context")),
            trace_sha256=_sha256_json(trace_rows),
            project_id=project_id,
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-url-file", type=Path, required=True)
    parser.add_argument("--state-database", type=Path, required=True)
    parser.add_argument("--logs-directory", type=Path, required=True)
    parser.add_argument("--approve-external-transmission", action="store_true")
    parser.add_argument("--provider-budget", type=int, default=0)
    parser.add_argument("--no-wait", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--resume-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(args)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if report["status"] == "PASS" else 2
    except GoldenUnverified as error:
        print(
            json.dumps(
                {"status": "UNVERIFIED", "reason": str(error)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
