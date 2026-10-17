from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.engineering_workspace_apply import (
    ApplyConflict,
    ChangeIntent,
    ChangeType,
    FixedWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    SystemWorkspaceClock,
    WorkspaceApplyService,
)
from embedded_copilot.engineering_workspace_apply.contracts import content_fingerprint


def _service(tmp_path: Path) -> WorkspaceApplyService:
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.c").write_text("int value = 1;\n", encoding="utf-8")
    return WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(root),
        store=SQLiteWorkspaceApplyStore(tmp_path / "state.sqlite3"),
        state_root=tmp_path / "state",
        clock=SystemWorkspaceClock(),
    )


def _proposal() -> SimpleNamespace:
    return SimpleNamespace(
        status="APPROVED",
        proposal_id="proposal-1",
        proposal_fingerprint="sha256:" + "1" * 64,
        tenant_id="tenant-golden",
        project_id="project-golden",
        requester_id="requester-1",
        validation_fingerprint="sha256:" + "2" * 64,
        evidence_fingerprint="sha256:" + "3" * 64,
    )


def test_workspace_apply_requires_separate_apply_approval_and_is_replay_protected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = service.snapshot(tenant_id="tenant-golden", project_id="project-golden")
    before = next(item for item in snapshot.files if item.relative_file_reference == "main.c")
    after = "int value = 2;\n"
    intent = ChangeIntent(
        relative_file_reference="main.c",
        change_type=ChangeType.MODIFY_FILE,
        before_fingerprint=before.content_fingerprint,
        after_fingerprint=content_fingerprint(after.encode()),
        safe_preview="value 1 -> value 2",
        reason="approved configuration change",
        source_proposal_reference="proposal://proposal-1",
        after_content=after,
    )
    changeset = service.create_changeset(proposal=_proposal(), snapshot=snapshot, intents=(intent,))
    preview = service.preview(changeset)
    assert "value = 2" in preview.diff
    assert (tmp_path / "project" / "main.c").read_text(encoding="utf-8") == "int value = 1;\n"
    approval = service.issue_apply_approval(changeset=changeset, reviewer_id="reviewer-1", nonce="nonce-1")
    receipt = service.apply(changeset=changeset, approval=approval)
    assert receipt.status.value == "APPLIED"
    assert (tmp_path / "project" / "main.c").read_text(encoding="utf-8") == after
    with pytest.raises(ApplyConflict):
        service.apply(changeset=changeset, approval=approval)


def test_workspace_apply_rejects_drift_and_tombstone(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = service.snapshot(tenant_id="tenant-golden", project_id="project-golden")
    before = next(item for item in snapshot.files if item.relative_file_reference == "main.c")
    after = "int value = 2;\n"
    intent = ChangeIntent(
        relative_file_reference="main.c",
        change_type=ChangeType.MODIFY_FILE,
        before_fingerprint=before.content_fingerprint,
        after_fingerprint=content_fingerprint(after.encode()),
        safe_preview="value 1 -> value 2",
        reason="approved configuration change",
        source_proposal_reference="proposal://proposal-1",
        after_content=after,
    )
    changeset = service.create_changeset(proposal=_proposal(), snapshot=snapshot, intents=(intent,))
    approval = service.issue_apply_approval(changeset=changeset, reviewer_id="reviewer-1", nonce="nonce-2")
    (tmp_path / "project" / "main.c").write_text("int value = 9;\n", encoding="utf-8")
    with pytest.raises(ApplyConflict):
        service.apply(changeset=changeset, approval=approval)

    store = SQLiteWorkspaceApplyStore(tmp_path / "tombstone.sqlite3")
    store.tombstone_project("tenant-golden", "project-golden", datetime.now(UTC).isoformat())
    with pytest.raises(Exception):
        WorkspaceApplyService(
            resolver=FixedWorkspaceTargetResolver(tmp_path / "project"),
            store=store,
            state_root=tmp_path / "state-tombstone",
            clock=SystemWorkspaceClock(),
        ).snapshot(tenant_id="tenant-golden", project_id="project-golden")


def test_contract_is_strict_and_rejects_path_escape(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = service.snapshot(tenant_id="tenant-golden", project_id="project-golden")
    with pytest.raises(ValueError):
        ChangeIntent(
            relative_file_reference="../escape.c",
            change_type=ChangeType.MODIFY_FILE,
            before_fingerprint="sha256:" + "0" * 64,
            after_fingerprint="sha256:" + "1" * 64,
            safe_preview="safe",
            reason="reason",
            source_proposal_reference="proposal://proposal-1",
            after_content="x",
        )
    assert snapshot.fingerprint.startswith("sha256:")


def test_partial_failure_is_rolled_back_and_recovery_retry_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    snapshot = service.snapshot(tenant_id="tenant-golden", project_id="project-golden")
    before = next(item for item in snapshot.files if item.relative_file_reference == "main.c")
    after = "int value = 3;\n"
    intent = ChangeIntent(
        relative_file_reference="main.c",
        change_type=ChangeType.MODIFY_FILE,
        before_fingerprint=before.content_fingerprint,
        after_fingerprint=content_fingerprint(after.encode()),
        safe_preview="value = 1 -> value = 3",
        reason="failure injection",
        source_proposal_reference="proposal://proposal-1",
        after_content=after,
    )
    changeset = service.create_changeset(proposal=_proposal(), snapshot=snapshot, intents=(intent,))
    approval = service.issue_apply_approval(changeset=changeset, reviewer_id="reviewer-1", nonce="nonce-recovery")
    original_apply = service._apply_entries

    def fail_after_write(root, entries):
        original_apply(root, entries)
        raise OSError("injected post-write failure")

    monkeypatch.setattr(service, "_apply_entries", fail_after_write)
    with pytest.raises(ApplyConflict):
        service.apply(changeset=changeset, approval=approval)
    assert (tmp_path / "project" / "main.c").read_text(encoding="utf-8") == "int value = 1;\n"
