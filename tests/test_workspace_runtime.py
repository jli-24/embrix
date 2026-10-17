from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import pytest
from pydantic import ValidationError

import embedded_copilot.workspace_runtime as workspace_runtime
from embedded_copilot.workspace_runtime import (
    ApprovalContext,
    ApprovalStatus,
    ApplyResult,
    ApplyStatus,
    ChangeProposal,
    ChangeOperation,
    FrozenWorkspaceSnapshot,
    ValidationResult,
    ValidationStatus,
    WorkspaceInspectionRequest,
    WorkspacePort,
    WorkspaceRuntime,
    create_workspace_runtime,
)


def _runtime(root: Path):
    runtime = create_workspace_runtime(root)
    assert isinstance(runtime, WorkspaceRuntime)
    return runtime.workspace_port()


def _proposal(
    snapshot: FrozenWorkspaceSnapshot, *, diff: str | None = None
) -> ChangeProposal:
    return ChangeProposal(
        proposal_id="proposal:1",
        workspace_snapshot_id=snapshot.snapshot_fingerprint,
        target_files=("src/main.py",),
        operation_type=ChangeOperation.MODIFY,
        diff=diff
        or (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "-value = 1\n"
            "+value = 2\n"
        ),
        reason="Correct an unverified configuration candidate.",
        created_by="engineer:1",
    )


def _approval(snapshot: FrozenWorkspaceSnapshot) -> ApprovalContext:
    return ApprovalContext(
        proposal_id="proposal:1",
        workspace_id=snapshot.workspace_id,
        workspace_snapshot_id=snapshot.snapshot_fingerprint,
        target_files=("src/main.py",),
        status=ApprovalStatus.APPROVED,
        approved_by="engineer:1",
        approved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )


def _validate(port: WorkspacePort, proposal: ChangeProposal) -> None:
    assert port.validate_change(proposal).status == "WAITING_APPROVAL"


def test_workspace_snapshot_is_frozen_order_stable_and_tamper_checked(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "src" / "util.c").write_text(
        "int value(void) { return 1; }\n", encoding="utf-8"
    )
    port = _runtime(tmp_path)

    first = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1",
            relative_paths=("src/util.c", "src/main.py"),
        )
    )
    second = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1",
            relative_paths=("src/main.py", "src/util.c"),
        )
    )

    assert first.snapshot_fingerprint == second.snapshot_fingerprint
    assert tuple(item.relative_path for item in first.files) == (
        "src/main.py",
        "src/util.c",
    )
    assert not hasattr(first.files[0], "content")
    with pytest.raises(ValidationError):
        first.workspace_id = "workspace:2"  # type: ignore[misc]
    tampered = first.model_dump(mode="python")
    tampered["workspace_id"] = "workspace:2"
    with pytest.raises(ValidationError, match="snapshot_fingerprint"):
        FrozenWorkspaceSnapshot.model_validate(tampered)


def test_result_dtos_reject_inconsistent_status_payloads() -> None:
    with pytest.raises(ValidationError):
        ValidationResult(
            status=ValidationStatus.REJECTED,
            proposal_id="proposal:1",
            workspace_id="workspace:1",
            workspace_snapshot_id="sha256:" + "0" * 64,
            target_files=("src/main.py",),
        )
    with pytest.raises(ValidationError):
        ApplyResult(
            status=ApplyStatus.APPLIED,
            proposal_id="proposal:1",
            workspace_id="workspace:1",
            target_files=("src/main.py",),
        )
    with pytest.raises(ValidationError):
        ApplyResult(
            status=ApplyStatus.REJECTED,
            proposal_id="proposal:1",
            workspace_id="workspace:1",
            target_files=("src/main.py",),
            error_code="write_failed",
            audit_event={
                "proposal_id": "proposal:1",
                "workspace_id": "workspace:1",
                "files": ("src/main.py",),
                "approved_by": "engineer:1",
                "timestamp": datetime(2026, 7, 28, tzinfo=timezone.utc),
            },
        )


def test_snapshot_changes_when_file_content_changes(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    request = WorkspaceInspectionRequest(
        workspace_id="workspace:1", relative_paths=("src/main.py",)
    )

    first = port.inspect_workspace(request)
    target.write_text("value = 2\n", encoding="utf-8")
    second = port.inspect_workspace(request)

    assert first.snapshot_fingerprint != second.snapshot_fingerprint


@pytest.mark.parametrize(
    "path",
    (
        "../private.py",
        "C:/private.py",
        ".git/config",
        ".env",
        ".env.local",
        "config.secret",
        "secrets.toml",
        "service.credentials.json",
        "user.passwords.txt",
        ".git./config",
        "src/main.py ",
        "src/main.py.",
        "src/main.py:stream",
        "src/CON",
        "src/com1.txt",
    ),
)
def test_workspace_inspection_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceInspectionRequest(workspace_id="workspace:1", relative_paths=(path,))


def test_non_sensitive_names_containing_token_text_remain_allowed() -> None:
    request = WorkspaceInspectionRequest(
        workspace_id="workspace:1",
        relative_paths=("src/tokenization.py", "src/secretary.py"),
    )

    assert request.relative_paths == ("src/tokenization.py", "src/secretary.py")


def test_runtime_errors_do_not_leak_trusted_root(tmp_path: Path) -> None:
    port = _runtime(tmp_path)

    with pytest.raises(ValueError) as captured:
        port.inspect_workspace(
            WorkspaceInspectionRequest(
                workspace_id="workspace:1",
                relative_paths=("src/missing.py",),
            )
        )

    assert str(tmp_path) not in str(captured.value)


def test_port_revalidates_constructed_contract_instances(tmp_path: Path) -> None:
    port = _runtime(tmp_path)
    unsafe_request = WorkspaceInspectionRequest.model_construct(
        workspace_id="workspace:1",
        relative_paths=("../secret.py",),
    )

    with pytest.raises(ValidationError):
        port.inspect_workspace(unsafe_request)

    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(snapshot)
    _validate(port, proposal)
    unsafe_proposal = proposal.model_copy(update={"target_files": ("../secret.py",)})
    unsafe_approval = _approval(snapshot).model_copy(
        update={
            "approved_by": "../attacker",
            "approved_at": datetime(2026, 7, 28),
        }
    )

    with pytest.raises(ValidationError):
        port.validate_change(unsafe_proposal)
    with pytest.raises(ValidationError):
        port.apply_change(proposal, unsafe_approval)
    assert target.read_bytes() == b"value = 1\n"


def test_factory_rejects_symlink_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    link = tmp_path / "workspace-link"
    root.mkdir()
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="workspace root is invalid"):
        create_workspace_runtime(link)


def test_inspection_rejects_symlink_parent_inside_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    linked = tmp_path / "linked"
    real.mkdir()
    (real / "main.py").write_text("value = 1\n", encoding="utf-8")
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    port = _runtime(tmp_path)

    with pytest.raises(ValueError):
        port.inspect_workspace(
            WorkspaceInspectionRequest(
                workspace_id="workspace:1",
                relative_paths=("linked/main.py",),
            )
        )


def test_proposal_validation_rejects_malformed_diff_and_workspace_change(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )

    malformed = _proposal(snapshot, diff="not a unified diff")
    assert port.validate_change(malformed).status == "REJECTED"

    target.write_text("value = external\n", encoding="utf-8")
    changed = port.validate_change(_proposal(snapshot))
    assert changed.status == "WORKSPACE_CHANGED"
    assert changed.error_code == "workspace_changed"


def test_validation_rejects_incorrect_hunk_counts(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )

    result = port.validate_change(
        _proposal(
            snapshot,
            diff=(
                "diff --git a/src/main.py b/src/main.py\n"
                "--- a/src/main.py\n"
                "+++ b/src/main.py\n"
                "@@ -1,9 +1,9 @@\n"
                "-value = 1\n"
                "+value = 2\n"
            ),
        )
    )

    assert result.status == "REJECTED"
    assert result.error_code == "invalid_diff"


def test_validation_rejects_bare_cr_diff_line_endings(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(snapshot)
    proposal = proposal.model_copy(update={"diff": proposal.diff.replace("\n", "\r")})

    result = port.validate_change(proposal)

    assert result.status == "REJECTED"
    assert result.error_code == "invalid_diff"


def test_patch_preserves_unicode_line_separator_as_text(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text('value = "a\u2028b"\n', encoding="utf-8", newline="")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(
        snapshot,
        diff=(
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            '-value = "a\u2028b"\n'
            '+value = "c\u2028d"\n'
        ),
    )
    _validate(port, proposal)

    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "APPLIED"
    assert target.read_text(encoding="utf-8") == 'value = "c\u2028d"\n'


def test_apply_preserves_crlf_and_rejects_mixed_newlines(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\r\n")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )

    proposal = _proposal(snapshot)
    _validate(port, proposal)
    applied = port.apply_change(proposal, _approval(snapshot))

    assert applied.status == "APPLIED"
    assert target.read_bytes() == b"value = 2\r\n"

    target.write_bytes(b"first = 1\r\nsecond = 1\n")
    mixed_snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    mixed = port.validate_change(
        _proposal(
            mixed_snapshot,
            diff=(
                "diff --git a/src/main.py b/src/main.py\n"
                "--- a/src/main.py\n"
                "+++ b/src/main.py\n"
                "@@ -1,2 +1,2 @@\n"
                " first = 1\n"
                "-second = 1\n"
                "+second = 2\n"
            ),
        )
    )

    assert mixed.status == "REJECTED"
    assert mixed.error_code == "invalid_text"


def test_apply_accepts_and_preserves_pure_lf(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )

    proposal = _proposal(snapshot)
    _validate(port, proposal)
    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "APPLIED"
    assert target.read_bytes() == b"value = 2\n"


def test_validation_rejects_patch_that_would_create_empty_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(
        snapshot,
        diff=(
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +0,0 @@\n"
            "-value = 1\n"
        ),
    )

    result = port.validate_change(proposal)

    assert result.status == "REJECTED"
    assert result.error_code == "invalid_text"
    assert target.read_bytes() == b"value = 1\n"


def test_validation_rejects_patch_output_larger_than_workspace_limit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    oversized_value = "测" * 350_000
    proposal = _proposal(
        snapshot,
        diff=(
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1 +1 @@\n"
            "-value = 1\n"
            f"+{oversized_value}\n"
        ),
    )

    result = port.validate_change(proposal)

    assert result.status == "REJECTED"
    assert result.error_code == "invalid_text"
    assert target.read_bytes() == b"value = 1\n"


def test_apply_requires_approval_then_applies_and_emits_content_free_audit(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(snapshot)
    waiting = port.validate_change(proposal)
    assert waiting.status == "WAITING_APPROVAL"

    rejected = port.apply_change(
        proposal,
        _approval(snapshot).model_copy(
            update={"status": ApprovalStatus.WAITING_APPROVAL}
        ),
    )
    assert rejected.status == "REJECTED"
    assert target.read_text(encoding="utf-8") == "value = 1\n"

    applied = port.apply_change(proposal, _approval(snapshot))
    assert applied.status == "APPLIED"
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert applied.audit_event is not None
    assert applied.audit_event.files == ("src/main.py",)
    serialized = applied.audit_event.model_dump_json()
    assert "value =" not in serialized
    assert "secret" not in serialized.casefold()
    assert snapshot.snapshot_fingerprint not in serialized
    assert not tuple(target.parent.glob("*.tmp"))
    assert not tuple(target.parent.glob("*.bak"))

    target.write_text("value = 1\n", encoding="utf-8")
    replay = port.apply_change(proposal, _approval(snapshot))
    assert replay.status == "REJECTED"
    assert replay.error_code == "approval_required"
    assert replay.audit_event is None


@pytest.mark.parametrize(
    "update",
    (
        {"proposal_id": "proposal:other"},
        {"workspace_id": "workspace:other"},
        {"workspace_snapshot_id": "sha256:" + "0" * 64},
        {"target_files": ("src/other.py",)},
        {"status": ApprovalStatus.WAITING_APPROVAL},
    ),
)
def test_apply_rejects_approval_binding_mismatch(
    tmp_path: Path, update: dict[str, object]
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(snapshot)
    _validate(port, proposal)

    result = port.apply_change(
        proposal,
        _approval(snapshot).model_copy(update=update),
    )

    assert result.status == "REJECTED"
    assert result.error_code == "approval_required"
    assert result.audit_event is None
    assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_approval_cannot_be_reused_for_replaced_proposal_content(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    original = _proposal(snapshot)
    assert port.validate_change(original).status == "WAITING_APPROVAL"
    replaced = original.model_copy(
        update={
            "diff": (
                "diff --git a/src/main.py b/src/main.py\n"
                "--- a/src/main.py\n"
                "+++ b/src/main.py\n"
                "@@ -1 +1 @@\n"
                "-value = 1\n"
                "+value = attacker\n"
            ),
            "reason": "Different unapproved change.",
        }
    )

    conflict = port.validate_change(replaced)
    result = port.apply_change(replaced, _approval(snapshot))

    assert conflict.status == "REJECTED"
    assert conflict.error_code == "proposal_conflict"
    assert result.status == "REJECTED"
    assert result.error_code == "approval_required"
    assert result.audit_event is None
    assert target.read_bytes() == b"value = 1\n"


def test_apply_rejects_identity_change_during_writer_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    original_open = Path.open
    target_reads = 0

    class MutatingReader:
        def __init__(self, wrapped: BinaryIO) -> None:
            self._wrapped = wrapped
            self._mutated = False

        def __enter__(self) -> "MutatingReader":
            self._wrapped.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._wrapped.__exit__(*args)

        def __getattr__(self, name: str) -> object:
            return getattr(self._wrapped, name)

        def read(self, *args: object) -> bytes:
            content = self._wrapped.read(*args)
            if not self._mutated:
                self._mutated = True
                with original_open(target, "wb") as handle:
                    handle.write(b"value = external\n")
            return content

    def open_with_change(path: Path, mode: str = "r", *args: object, **kwargs: object):
        nonlocal target_reads
        handle = original_open(path, mode, *args, **kwargs)
        if path == target and mode == "rb":
            target_reads += 1
            if target_reads == 2:
                return MutatingReader(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_with_change)

    proposal = _proposal(snapshot)
    _validate(port, proposal)
    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "REJECTED"
    assert result.error_code == "workspace_changed"
    assert result.audit_event is None
    assert target.read_text(encoding="utf-8") == "value = external\n"


def test_apply_rejects_parent_directory_swap_with_same_file_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    replacement = tmp_path / "replacement"
    parked = tmp_path / "parked"
    source.mkdir()
    replacement.mkdir()
    target = source / "main.py"
    target.write_text("value = 1\n", encoding="utf-8")
    try:
        os.link(target, replacement / "main.py")
    except OSError:
        pytest.skip("hardlink creation is unavailable")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    original_open = Path.open
    target_reads = 0
    parked_file_preserved: bool | None = None

    def open_after_parent_swap(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        nonlocal parked_file_preserved, target_reads
        if path == target and mode == "rb":
            target_reads += 1
            if target_reads == 2:
                source.rename(parked)
                replacement.rename(source)
                # NTFS implementations may differ in how quickly the old
                # directory view becomes observable after a rename.  Record
                # the result of the swap itself so only that non-portable
                # content-visibility assertion can be skipped below.
                parked_file_preserved = (parked / "main.py").exists()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_parent_swap)

    proposal = _proposal(snapshot)
    _validate(port, proposal)
    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "REJECTED"
    assert result.error_code == "workspace_changed"
    assert result.audit_event is None
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    if parked_file_preserved is False and os.name == "nt":
        pytest.skip(
            "Windows NTFS directory-swap semantics do not preserve the parked "
            "directory view for this filesystem configuration"
        )
    assert parked_file_preserved is True
    assert (parked / "main.py").read_text(encoding="utf-8") == "value = 1\n"


def test_multi_file_write_rolls_back_when_a_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "src" / "first.py"
    second = tmp_path / "src" / "second.py"
    first.parent.mkdir()
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1",
            relative_paths=("src/first.py", "src/second.py"),
        )
    )
    proposal = ChangeProposal(
        proposal_id="proposal:2",
        workspace_snapshot_id=snapshot.snapshot_fingerprint,
        target_files=("src/first.py", "src/second.py"),
        operation_type=ChangeOperation.MODIFY,
        diff=(
            "diff --git a/src/first.py b/src/first.py\n"
            "--- a/src/first.py\n"
            "+++ b/src/first.py\n"
            "@@ -1 +1 @@\n"
            "-first = 1\n"
            "+first = 2\n"
            "diff --git a/src/second.py b/src/second.py\n"
            "--- a/src/second.py\n"
            "+++ b/src/second.py\n"
            "@@ -1 +1 @@\n"
            "-second = 1\n"
            "+second = 2\n"
        ),
        reason="Apply reviewed text corrections.",
        created_by="engineer:1",
    )
    approval = ApprovalContext(
        proposal_id=proposal.proposal_id,
        workspace_id=snapshot.workspace_id,
        workspace_snapshot_id=snapshot.snapshot_fingerprint,
        target_files=proposal.target_files,
        status=ApprovalStatus.APPROVED,
        approved_by="engineer:1",
        approved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    original_replace = os.replace

    def fail_second_replace(source: str | bytes, destination: str | bytes) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if "workspace" in source_path.name and destination_path == second:
            raise OSError("simulated replacement failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second_replace)

    _validate(port, proposal)
    result = port.apply_change(proposal, approval)

    assert result.status == "REJECTED"
    assert result.error_code == "write_failed"
    assert first.read_text(encoding="utf-8") == "first = 1\n"
    assert second.read_text(encoding="utf-8") == "second = 1\n"


def test_rollback_failure_is_reported_and_preserves_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "src" / "first.py"
    second = tmp_path / "src" / "second.py"
    first.parent.mkdir()
    first.write_text("first = 1\n", encoding="utf-8")
    second.write_text("second = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1",
            relative_paths=("src/first.py", "src/second.py"),
        )
    )
    proposal = ChangeProposal(
        proposal_id="proposal:rollback",
        workspace_snapshot_id=snapshot.snapshot_fingerprint,
        target_files=("src/first.py", "src/second.py"),
        diff=(
            "diff --git a/src/first.py b/src/first.py\n"
            "--- a/src/first.py\n"
            "+++ b/src/first.py\n"
            "@@ -1 +1 @@\n"
            "-first = 1\n"
            "+first = 2\n"
            "diff --git a/src/second.py b/src/second.py\n"
            "--- a/src/second.py\n"
            "+++ b/src/second.py\n"
            "@@ -1 +1 @@\n"
            "-second = 1\n"
            "+second = 2\n"
        ),
        reason="Exercise recovery failure handling.",
        created_by="engineer:1",
    )
    approval = ApprovalContext(
        proposal_id=proposal.proposal_id,
        workspace_id=snapshot.workspace_id,
        workspace_snapshot_id=snapshot.snapshot_fingerprint,
        target_files=proposal.target_files,
        status=ApprovalStatus.APPROVED,
        approved_by="engineer:1",
        approved_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    original_replace = os.replace

    def fail_write_and_rollback(source: str | bytes, destination: str | bytes) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.name.endswith(".tmp") and destination_path == second:
            raise OSError("simulated write failure")
        if source_path.name.endswith(".bak") and destination_path == first:
            raise OSError("simulated rollback failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_write_and_rollback)

    _validate(port, proposal)
    result = port.apply_change(proposal, approval)

    assert result.status == "REJECTED"
    assert result.error_code == "rollback_failed"
    assert result.audit_event is None
    assert tuple(first.parent.glob("*.bak"))


def test_cleanup_failure_is_reported_as_rollback_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(snapshot)
    original_replace = os.replace
    original_unlink = Path.unlink

    def fail_replace(source: str | bytes, destination: str | bytes) -> None:
        if Path(source).name.endswith(".tmp"):
            raise OSError("simulated replacement failure")
        original_replace(source, destination)

    def fail_temporary_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.endswith(".tmp"):
            raise PermissionError("simulated cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_temporary_cleanup)
    _validate(port, proposal)

    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "REJECTED"
    assert result.error_code == "rollback_failed"
    assert result.audit_event is None
    assert target.read_bytes() == b"value = 1\n"
    assert tuple(target.parent.glob("*.bak"))


def test_existing_recovery_backup_is_never_deleted_on_name_collision(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    proposal = _proposal(snapshot)
    token = hashlib.sha256(
        (
            proposal.proposal_id
            + "\x00"
            + snapshot.snapshot_fingerprint
            + "\x00"
            + proposal.target_files[0]
        ).encode("utf-8")
    ).hexdigest()[:20]
    backup = target.with_name(f".{target.name}.workspace-{token}.bak")
    backup.write_bytes(b"recovery material")

    _validate(port, proposal)
    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "REJECTED"
    assert result.error_code == "write_failed"
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert backup.read_bytes() == b"recovery material"


def test_artifact_creation_failure_removes_new_content_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )

    def fail_fsync(_: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    proposal = _proposal(snapshot)
    _validate(port, proposal)
    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "REJECTED"
    assert result.error_code == "write_failed"
    assert result.audit_event is None
    assert target.read_text(encoding="utf-8") == "value = 1\n"
    assert not tuple(target.parent.glob("*.tmp"))
    assert not tuple(target.parent.glob("*.bak"))


def test_temporary_and_backup_files_are_created_with_private_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    port = _runtime(tmp_path)
    snapshot = port.inspect_workspace(
        WorkspaceInspectionRequest(
            workspace_id="workspace:1", relative_paths=("src/main.py",)
        )
    )
    requested_modes: list[int] = []
    original_open = os.open

    def open_private(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(path).name.startswith(".main.py.workspace-"):
            requested_modes.append(mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", open_private)

    proposal = _proposal(snapshot)
    _validate(port, proposal)
    result = port.apply_change(proposal, _approval(snapshot))

    assert result.status == "APPLIED"
    assert requested_modes == [0o600, 0o600]


def test_public_runtime_exposes_only_workspace_port(tmp_path: Path) -> None:
    assert {
        name
        for name, value in WorkspaceRuntime.__dict__.items()
        if callable(value) and not name.startswith("_")
    } == {"workspace_port"}
    assert "WorkspaceRuntime" in workspace_runtime.__all__
    assert "create_workspace_runtime" in workspace_runtime.__all__
    assert not hasattr(_runtime(tmp_path), "write")
    assert not hasattr(_runtime(tmp_path), "generate")
