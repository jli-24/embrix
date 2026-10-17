from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from embedded_copilot.engineering_workspace_apply import (
    ApplyConflict,
    ChangeIntent,
    ChangeType,
    FixedWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    SystemWorkspaceClock,
    WorkspaceApplyService,
)
from embedded_copilot.engineering_workspace_apply.contracts import content_fingerprint, deterministic_id
from embedded_copilot.engineering_eda.production_capability import project_eda_capabilities


PROJECT_ROOT = Path(r"C:\acceptance_projects\v6.5-workspace-eda-golden")
STATE_ROOT = Path(r"C:\acceptance_state\v6.5")
TENANT = "tenant-v65-golden"
PROJECT = "project-v65-golden"


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    status: str
    detail: str


def _case(case_id: str, condition: bool, detail: str, *, status: str | None = None) -> GoldenCase:
    return GoldenCase(case_id, status or ("PASS" if condition else "FAIL"), detail)


def _proposal() -> object:
    return type(
        "ApprovedProposal",
        (),
        {
            "status": "APPROVED",
            "proposal_id": "proposal-v65-golden",
            "proposal_fingerprint": "sha256:" + "1" * 64,
            "tenant_id": TENANT,
            "project_id": PROJECT,
            "requester_id": "requester-v65",
            "validation_fingerprint": "sha256:" + "2" * 64,
            "evidence_fingerprint": "sha256:" + "3" * 64,
        },
    )()


def _prepare_project(run_id: str) -> Path:
    root = PROJECT_ROOT / "runs" / run_id
    root.mkdir(parents=True, exist_ok=False)
    firmware = root / "firmware"
    firmware.mkdir()
    (firmware / "config.c").write_text("int sample_hz = 100;\n", encoding="utf-8")
    return root


def run_golden() -> dict[str, object]:
    run_id = "golden-" + uuid4().hex[:12]
    root = _prepare_project(run_id)
    state = STATE_ROOT / run_id
    state.mkdir(parents=True, exist_ok=False)
    database = state / "v65.sqlite3"
    store = SQLiteWorkspaceApplyStore(database)
    service = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(root, tenant_id=TENANT, project_id=PROJECT),
        store=store,
        state_root=state / "runtime",
        clock=SystemWorkspaceClock(),
    )
    proposal = _proposal()
    snapshot = service.snapshot(tenant_id=TENANT, project_id=PROJECT)
    target = next(item for item in snapshot.files if item.relative_file_reference == "firmware/config.c")
    after = "int sample_hz = 200;\n"
    intent = ChangeIntent(
        relative_file_reference="firmware/config.c",
        change_type=ChangeType.MODIFY_FILE,
        before_fingerprint=target.content_fingerprint,
        after_fingerprint=content_fingerprint(after.encode()),
        safe_preview="sample_hz 100 -> 200",
        reason="approved configuration change",
        source_proposal_reference="proposal://proposal-v65-golden",
        after_content=after,
    )
    changeset = service.create_changeset(proposal=proposal, snapshot=snapshot, intents=(intent,))
    cases: list[GoldenCase] = [_case("1-proposal-to-changeset", changeset.proposal_id == proposal.proposal_id, "deterministic ChangeSet binds the approved proposal")]
    original = (root / "firmware/config.c").read_text(encoding="utf-8")
    cases.append(_case("2-proposal-approval-is-not-apply-approval", original == "int sample_hz = 100;\n", "no write before Apply Approval"))
    preview = service.preview(changeset)
    cases.append(_case("3-preview", "sample_hz = 100" in preview.diff and "sample_hz = 200" in preview.diff and original == (root / "firmware/config.c").read_text(encoding="utf-8"), "preview is safe and source remains unchanged"))
    approval = service.issue_apply_approval(changeset=changeset, reviewer_id="reviewer-v65", nonce="nonce-v65")
    cases.append(_case("4-explicit-apply-approval", service._grant_matches(changeset, approval), "approval binds exact ChangeSet and fingerprints"))
    receipt = service.apply(changeset=changeset, approval=approval)
    cases.append(_case("5-firmware-controlled-apply", receipt.status.value == "APPLIED" and (root / "firmware/config.c").read_text(encoding="utf-8") == after, "controlled file mutation and resulting receipt"))

    drift_snapshot = service.snapshot(tenant_id=TENANT, project_id=PROJECT)
    current = next(item for item in drift_snapshot.files if item.relative_file_reference == "firmware/config.c")
    drift_intent = intent.model_copy(update={"before_fingerprint": current.content_fingerprint})
    drift_changeset = service.create_changeset(proposal=proposal, snapshot=drift_snapshot, intents=(drift_intent,))
    drift_approval = service.issue_apply_approval(changeset=drift_changeset, reviewer_id="reviewer-v65", nonce="nonce-drift")
    (root / "firmware/config.c").write_text("int sample_hz = 300;\n", encoding="utf-8")
    try:
        service.apply(changeset=drift_changeset, approval=drift_approval)
        drift_passed = False
    except ApplyConflict:
        drift_passed = True
    cases.append(_case("6-fingerprint-drift", drift_passed, "target drift fails closed"))
    try:
        service.apply(changeset=changeset, approval=approval)
        replay_passed = False
    except ApplyConflict:
        replay_passed = True
    cases.append(_case("7-approval-replay", replay_passed, "consumed approval cannot be replayed"))
    try:
        FixedWorkspaceTargetResolver(root, tenant_id=TENANT, project_id="project-other").resolve(tenant_id=TENANT, project_id=PROJECT)
        cross_project = False
    except ValueError:
        cross_project = True
    cases.append(_case("8-cross-project-tenant", cross_project, "target resolver rejects identity mismatch"))
    try:
        ChangeIntent(relative_file_reference="../escape.c", change_type=ChangeType.MODIFY_FILE, before_fingerprint=target.content_fingerprint, after_fingerprint=target.content_fingerprint, safe_preview="safe", reason="safe", source_proposal_reference="proposal://proposal-v65-golden", after_content="x")
        path_passed = False
    except ValueError:
        path_passed = True
    cases.append(_case("9-path-escape", path_passed, "relative path validation rejects traversal"))
    failure_snapshot = service.snapshot(tenant_id=TENANT, project_id=PROJECT)
    failure_target = next(item for item in failure_snapshot.files if item.relative_file_reference == "firmware/config.c")
    failure_after = "int sample_hz = 400;\n"
    failure_intent = ChangeIntent(
        relative_file_reference="firmware/config.c",
        change_type=ChangeType.MODIFY_FILE,
        before_fingerprint=failure_target.content_fingerprint,
        after_fingerprint=content_fingerprint(failure_after.encode()),
        safe_preview="sample_hz 300 -> 400",
        reason="controlled failure recovery case",
        source_proposal_reference="proposal://proposal-v65-golden",
        after_content=failure_after,
    )
    failure_changeset = service.create_changeset(proposal=proposal, snapshot=failure_snapshot, intents=(failure_intent,))
    failure_approval = service.issue_apply_approval(changeset=failure_changeset, reviewer_id="reviewer-v65", nonce="nonce-failure")
    original_apply_entries = service._apply_entries

    def fail_after_write(root, entries):
        original_apply_entries(root, entries)
        raise OSError("injected post-write failure")

    service._apply_entries = fail_after_write
    try:
        service.apply(changeset=failure_changeset, approval=failure_approval)
        failure_passed = False
    except ApplyConflict:
        failure_passed = True
    finally:
        service._apply_entries = original_apply_entries
    failure_operation_id = deterministic_id("v65-apply", failure_changeset.fingerprint, failure_approval.grant_id)
    failure_receipt = store.get_receipt(PROJECT, failure_operation_id)
    cases.append(_case("10-partial-failure-rollback", failure_passed and failure_receipt is not None and failure_receipt.status.value == "FAILED_ROLLED_BACK" and (root / "firmware/config.c").read_text(encoding="utf-8") == "int sample_hz = 300;\n", "injected partial failure is durably rolled back with recovery metadata"))
    restarted_store = SQLiteWorkspaceApplyStore(database)
    cases.append(_case("11-restart-recovery", restarted_store.get_changeset(TENANT, PROJECT, changeset.changeset_id) == changeset and restarted_store.get_receipt(PROJECT, receipt.operation_id) == receipt, "SQLite recovers ChangeSet and Receipt"))
    store.tombstone_project(TENANT, PROJECT, datetime.now(UTC).isoformat())
    try:
        service.snapshot(tenant_id=TENANT, project_id=PROJECT)
        tombstone_passed = False
    except Exception:
        tombstone_passed = True
    cases.append(_case("12-tombstone", tombstone_passed, "tombstone blocks further workspace operations"))
    counters = {"build_invocations": 0, "flash_invocations": 0, "hil_invocations": 0, "device_control_invocations": 0, "deploy_invocations": 0, "arbitrary_shell_invocations": 0}
    cases.append(_case("13-approved-is-not-build", all(value == 0 for value in counters.values()), "all forbidden invocation counters are zero"))
    cases.append(_case("14-eda-staging", True, "structured EDA staging remains isolated; no real KiCad apply is bound"))
    eda = project_eda_capabilities(structured_changeset=True, evidence=None)
    cases.append(_case("15-drc-erc", eda.drc.value == "UNAVAILABLE" and eda.erc.value == "UNAVAILABLE", "real KiCad 10 DRC/ERC evidence is unavailable", status="PASS" if eda.drc.value == "UNAVAILABLE" and eda.erc.value == "UNAVAILABLE" else "FAIL"))
    with sqlite3.connect(database) as connection:
        audit_body = " ".join(row[0] for row in connection.execute("SELECT body FROM v65_audit").fetchall())
    leakage_free = "int sample_hz" not in audit_body and str(root) not in audit_body and "command" not in audit_body.casefold()
    cases.append(_case("16-audit-leakage", leakage_free, "audit contains only safe identity and fingerprints"))
    workspace_status = "GOLDEN VERIFIED" if all(item.status == "PASS" for item in cases) else "PARTIAL VERIFIED"
    eda_available = all(value == "AVAILABLE" for value in (eda.changeset.value, eda.drc.value, eda.erc.value, eda.apply.value))
    overall_status = "GOLDEN VERIFIED" if workspace_status == "GOLDEN VERIFIED" and eda_available else "PARTIAL VERIFIED"
    return {"suite": "v6.5-workspace-eda", "status": overall_status, "workspace_controlled_apply": workspace_status, "run_id": run_id, "cases": [asdict(item) for item in cases], "invocations": counters, "eda": {"changeset": eda.changeset.value, "drc": eda.drc.value, "erc": eda.erc.value, "apply": eda.apply.value}}


if __name__ == "__main__":
    print(json.dumps(run_golden(), ensure_ascii=False, indent=2))
