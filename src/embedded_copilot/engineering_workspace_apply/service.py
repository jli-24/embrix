from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .contracts import content_fingerprint, deterministic_id, fingerprint
from .exceptions import ApplyConflict, ApplyRejected, ApplyUnavailable, RecoveryRequired
from .models import (
    ApplyApprovalGrant,
    ApplyOperation,
    ApplyReceipt,
    ApplyRequest,
    ApprovalGrantStatus,
    ChangeIntent,
    ChangePreview,
    ChangeSet,
    ChangeSetEntry,
    ChangeSetStatus,
    ChangeType,
    OperationStatus,
    OperationView,
    RecoveryReceipt,
    RecoveryStatus,
    RecoveryTarget,
    ValidationStatus,
    WorkspaceAuditEvent,
    WorkspaceFile,
    WorkspaceSnapshot,
    WorkspaceTarget,
)
from .ports import ResolvedWorkspaceTarget, WorkspaceClockPort, WorkspaceTargetResolverPort


_ZERO = "sha256:" + "0" * 64


def _payload_fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _copy_model(value: Any, model: type[Any]) -> Any:
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class WorkspaceApplyService:
    """Canonical v6.5 service. This is the only public write-capable application boundary."""

    def __init__(
        self,
        *,
        resolver: WorkspaceTargetResolverPort,
        store: object,
        state_root: Path,
        clock: WorkspaceClockPort,
        approval_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if not isinstance(resolver, WorkspaceTargetResolverPort) or not isinstance(clock, WorkspaceClockPort):
            raise TypeError("workspace apply dependency is invalid")
        if not callable(getattr(store, "save_changeset", None)):
            raise TypeError("workspace apply store is invalid")
        if not isinstance(state_root, Path) or state_root.resolve(strict=False) == Path(state_root.anchor):
            raise ValueError("workspace state root is invalid")
        self._resolver = resolver
        self._store = store
        self._state_root = state_root.resolve(strict=False)
        self._clock = clock
        self._approval_ttl = approval_ttl
        self._state_root.mkdir(parents=True, exist_ok=True)

    def snapshot(self, *, tenant_id: str, project_id: str, target_id: str | None = None) -> WorkspaceSnapshot:
        if self._is_tombstoned(tenant_id, project_id):
            raise ApplyRejected("project is tombstoned")
        target = self._resolve(tenant_id, project_id, target_id)
        files = self._scan(target.root)
        project_fp = self._project_fingerprint(files)
        target_view = WorkspaceTarget(
            tenant_id=tenant_id,
            project_id=project_id,
            target_id=target.target_id,
            project_fingerprint=project_fp,
        )
        target_fp = _payload_fingerprint({"target": target_view.fingerprint, "files": [item.model_dump(mode="json") for item in files]})
        return WorkspaceSnapshot(
            tenant_id=tenant_id,
            project_id=project_id,
            target_id=target.target_id,
            project_fingerprint=project_fp,
            target_fingerprint=target_fp,
            files=files,
            captured_at=self._clock.now(),
        )

    def create_changeset(
        self,
        *,
        proposal: object,
        snapshot: WorkspaceSnapshot,
        intents: tuple[ChangeIntent, ...],
        operation: ApplyOperation = ApplyOperation.WORKSPACE_APPLY,
    ) -> ChangeSet:
        checked_snapshot = _copy_model(snapshot, WorkspaceSnapshot)
        if self._is_tombstoned(checked_snapshot.tenant_id, checked_snapshot.project_id):
            raise ApplyRejected("project is tombstoned")
        proposal_status = getattr(proposal, "status", None)
        proposal_status = getattr(proposal_status, "value", proposal_status)
        if proposal_status != "APPROVED":
            raise ApplyRejected("proposal approval is required")
        proposal_id = getattr(proposal, "proposal_id", None)
        proposal_fp = getattr(proposal, "proposal_fingerprint", None)
        project_id = getattr(proposal, "project_id", None)
        tenant_id = getattr(proposal, "tenant_id", None)
        if not all(isinstance(item, str) for item in (proposal_id, proposal_fp, project_id, tenant_id)):
            raise ApplyRejected("approved proposal is invalid")
        if project_id != checked_snapshot.project_id or tenant_id != checked_snapshot.tenant_id:
            raise ApplyRejected("proposal project binding mismatch")
        current_snapshot = self.snapshot(
            tenant_id=checked_snapshot.tenant_id,
            project_id=checked_snapshot.project_id,
            target_id=checked_snapshot.target_id,
        )
        if current_snapshot.project_fingerprint != checked_snapshot.project_fingerprint or current_snapshot.target_fingerprint != checked_snapshot.target_fingerprint:
            raise ApplyConflict("workspace snapshot drift")
        if not intents:
            raise ApplyRejected("change intent is required")
        checked_intents = tuple(ChangeIntent.model_validate(copy.deepcopy(item.model_dump(mode="python"))) for item in intents)
        allowed = {ChangeType.MODIFY_FILE, ChangeType.CREATE_FILE} if operation is ApplyOperation.WORKSPACE_APPLY else {ChangeType.EDA_STRUCTURED_CHANGE}
        if any(item.change_type not in allowed for item in checked_intents):
            raise ApplyRejected("change capability is unavailable")
        current = {item.relative_file_reference: item for item in checked_snapshot.files}
        for item in checked_intents:
            existing = current.get(item.relative_file_reference)
            if item.change_type is ChangeType.MODIFY_FILE and (existing is None or not existing.exists or item.before_fingerprint != existing.content_fingerprint):
                raise ApplyRejected("change intent target fingerprint mismatch")
            if item.change_type is ChangeType.CREATE_FILE and existing is not None and existing.exists:
                raise ApplyRejected("create target already exists")
            if item.change_type is not ChangeType.EDA_STRUCTURED_CHANGE and item.after_content is None:
                raise ApplyRejected("file change material is unavailable")
        changeset_key = deterministic_id(
            "v65-change-key",
            proposal_fp,
            checked_snapshot.target_fingerprint,
            *(_payload_fingerprint(item.model_dump(mode="json")) for item in checked_intents),
        )
        changeset_id = deterministic_id("v65-changeset", changeset_key)
        stage = self._stage_path(changeset_id)
        self._copy_to_staging(self._resolve(tenant_id, project_id, checked_snapshot.target_id).root, stage)
        entries: list[ChangeSetEntry] = []
        for index, item in enumerate(checked_intents):
            if item.change_type is ChangeType.EDA_STRUCTURED_CHANGE:
                after_fp = item.after_fingerprint
            else:
                after_bytes = item.after_content.encode("utf-8")
                after_fp = content_fingerprint(after_bytes)
                if after_fp != item.after_fingerprint:
                    raise ApplyRejected("change material fingerprint mismatch")
                target_path = self._safe_path(stage, item.relative_file_reference)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(after_bytes)
                self._fsync_file(target_path)
            entries.append(
                ChangeSetEntry(
                    entry_id=deterministic_id("changeset-entry", changeset_id, str(index)),
                    relative_file_reference=item.relative_file_reference,
                    change_type=item.change_type,
                    target_symbol=item.target_symbol,
                    before_fingerprint=item.before_fingerprint,
                    after_fingerprint=after_fp,
                    safe_preview=item.safe_preview,
                    reason=item.reason,
                    source_proposal_reference=item.source_proposal_reference,
                    after_content=item.after_content if item.change_type is not ChangeType.EDA_STRUCTURED_CHANGE else None,
                )
            )
        staging_fp = self._project_fingerprint(self._scan(stage))
        expires = self._clock.now() + self._approval_ttl
        change = ChangeSet(
            changeset_id=changeset_id,
            tenant_id=tenant_id,
            project_id=project_id,
            requester_id=getattr(proposal, "requester_id", "proposal-requester"),
            proposal_id=proposal_id,
            proposal_fingerprint=proposal_fp,
            target=WorkspaceTarget(
                tenant_id=tenant_id,
                project_id=project_id,
                target_id=checked_snapshot.target_id,
                project_fingerprint=checked_snapshot.project_fingerprint,
            ),
            target_fingerprint=checked_snapshot.target_fingerprint,
            base_project_fingerprint=checked_snapshot.project_fingerprint,
            validation_fingerprint=getattr(proposal, "validation_fingerprint", _ZERO),
            evidence_fingerprints=tuple(getattr(proposal, "evidence_fingerprint", _ZERO) for _ in (0,)),
            staging_fingerprint=staging_fp,
            entries=tuple(entries),
            created_at=self._clock.now(),
            expires_at=expires,
            status=ChangeSetStatus.WAIT_APPROVAL,
        )
        return self._store.save_changeset(change)

    def preview(self, changeset: ChangeSet) -> ChangePreview:
        checked = _copy_model(changeset, ChangeSet)
        if self._is_tombstoned(checked.tenant_id, checked.project_id):
            raise ApplyRejected("project is tombstoned")
        stage = self._stage_path(checked.changeset_id)
        if not stage.is_dir():
            raise ApplyUnavailable("staging workspace is unavailable")
        current = self.snapshot(tenant_id=checked.tenant_id, project_id=checked.project_id, target_id=checked.target.target_id)
        if current.project_fingerprint != checked.base_project_fingerprint:
            raise ApplyConflict("project fingerprint drift")
        chunks: list[str] = []
        for entry in checked.entries:
            before = self._safe_path(self._resolve(checked.tenant_id, checked.project_id, checked.target.target_id).root, entry.relative_file_reference)
            after = self._safe_path(stage, entry.relative_file_reference)
            before_text = before.read_text(encoding="utf-8") if before.exists() else ""
            after_text = after.read_text(encoding="utf-8") if after.exists() else entry.safe_preview
            chunks.extend(difflib.unified_diff(before_text.splitlines(True), after_text.splitlines(True), fromfile=entry.relative_file_reference, tofile=entry.relative_file_reference))
        preview = ChangePreview(
            changeset_id=checked.changeset_id,
            changeset_fingerprint=checked.fingerprint,
            project_id=checked.project_id,
            affected_files=tuple(item.relative_file_reference for item in checked.entries),
            diff="".join(chunks)[:64 * 1024] or "No textual diff available; structured validation is required.",
            validation_status=ValidationStatus.PASSED,
            validation_fingerprint=checked.validation_fingerprint,
            risk="CONTROLLED_WORKSPACE_CHANGE",
            generated_at=self._clock.now(),
        )
        return self._store.save_preview(preview)

    def issue_apply_approval(self, *, changeset: ChangeSet, reviewer_id: str, nonce: str | None = None, expires_at: datetime | None = None) -> ApplyApprovalGrant:
        checked = _copy_model(changeset, ChangeSet)
        if self._is_tombstoned(checked.tenant_id, checked.project_id):
            raise ApplyRejected("project is tombstoned")
        now = self._clock.now()
        if now >= checked.expires_at:
            raise ApplyRejected("changeset is expired")
        if expires_at is not None and (expires_at.tzinfo is None or expires_at.utcoffset() is None or expires_at <= now):
            raise ApplyRejected("apply approval expiry is invalid")
        nonce_value = nonce or secrets.token_hex(16)
        grant = ApplyApprovalGrant(
            grant_id=deterministic_id("v65-approval", checked.fingerprint, reviewer_id, nonce_value),
            tenant_id=checked.tenant_id,
            project_id=checked.project_id,
            proposal_id=checked.proposal_id,
            proposal_fingerprint=checked.proposal_fingerprint,
            changeset_id=checked.changeset_id,
            changeset_fingerprint=checked.fingerprint,
            target_fingerprint=checked.target_fingerprint,
            project_fingerprint=checked.base_project_fingerprint,
            staging_fingerprint=checked.staging_fingerprint,
            reviewer_id=reviewer_id,
            nonce=nonce_value,
            issued_at=now,
            expires_at=min(checked.expires_at, now + self._approval_ttl, expires_at) if expires_at is not None else min(checked.expires_at, now + self._approval_ttl),
        )
        return self._store.save_approval(grant)

    def apply(self, *, changeset: ChangeSet, approval: ApplyApprovalGrant, operation_id: str | None = None) -> ApplyReceipt:
        checked = _copy_model(changeset, ChangeSet)
        grant = _copy_model(approval, ApplyApprovalGrant)
        operation = operation_id or deterministic_id("v65-apply", checked.fingerprint, grant.grant_id)
        target = self._resolve(checked.tenant_id, checked.project_id, checked.target.target_id)
        if self._is_tombstoned(checked.tenant_id, checked.project_id):
            raise ApplyRejected("project is tombstoned")
        if grant.status is not ApprovalGrantStatus.ISSUED or self._clock.now() >= grant.expires_at:
            self._reject_operation(operation, checked, "APPROVAL_EXPIRED_OR_CONSUMED")
            raise ApplyRejected("apply approval is unavailable")
        if not self._grant_matches(checked, grant):
            self._reject_operation(operation, checked, "APPROVAL_BINDING_MISMATCH")
            raise ApplyRejected("apply approval binding mismatch")
        consumed = self._store.consume_approval(checked.tenant_id, checked.project_id, grant.grant_id, now=self._clock.now().isoformat())
        if consumed is None:
            self._reject_operation(operation, checked, "REPLAY_REJECTED")
            raise ApplyConflict("approval replay rejected")
        current = self.snapshot(tenant_id=checked.tenant_id, project_id=checked.project_id, target_id=checked.target.target_id)
        if current.project_fingerprint != checked.base_project_fingerprint:
            self._reject_operation(operation, checked, "TARGET_DRIFT")
            raise ApplyConflict("target drift")
        recovery_id = deterministic_id("v65-recovery", operation)
        recovery_dir = self._state_root / "recovery" / operation
        recovery_targets: list[RecoveryTarget] = []
        try:
            recovery_dir.mkdir(parents=True, exist_ok=False)
            for entry in checked.entries:
                target_path = self._safe_path(target.root, entry.relative_file_reference)
                before_bytes = target_path.read_bytes() if target_path.exists() else b""
                recovery_path = recovery_dir / entry.entry_id
                recovery_path.write_bytes(before_bytes)
                self._fsync_file(recovery_path)
                recovery_targets.append(
                    RecoveryTarget(
                        relative_file_reference=entry.relative_file_reference,
                        recovery_object_reference=f"recovery://{operation}/{entry.entry_id}",
                        checksum=content_fingerprint(before_bytes),
                        before_fingerprint=entry.before_fingerprint,
                        after_fingerprint=entry.after_fingerprint,
                    )
                )
            recovery = RecoveryReceipt(
                recovery_id=recovery_id,
                operation_id=operation,
                project_id=checked.project_id,
                status=RecoveryStatus.AVAILABLE,
                targets=tuple(recovery_targets),
                created_at=self._clock.now(),
            )
            self._store.save_recovery(recovery)
            self._apply_entries(target.root, checked.entries)
            after = self.snapshot(tenant_id=checked.tenant_id, project_id=checked.project_id, target_id=checked.target.target_id)
            for entry in checked.entries:
                result_path = self._safe_path(target.root, entry.relative_file_reference)
                if not result_path.exists() or content_fingerprint(result_path.read_bytes()) != entry.after_fingerprint:
                    raise ApplyConflict("post-apply fingerprint mismatch")
            receipt = ApplyReceipt(
                operation_id=operation,
                project_id=checked.project_id,
                changeset_id=checked.changeset_id,
                changeset_fingerprint=checked.fingerprint,
                status=OperationStatus.APPLIED,
                recovery_status=RecoveryStatus.AVAILABLE,
                recovery_id=recovery_id,
                targets=tuple(recovery_targets),
                validation_status=ValidationStatus.PASSED,
                before_project_fingerprint=checked.base_project_fingerprint,
                after_project_fingerprint=after.project_fingerprint,
                created_at=self._clock.now(),
            )
            saved = self._store.save_receipt(receipt)
            self._audit(checked, grant, saved)
            return saved
        except Exception as error:
            try:
                self._rollback(target.root, recovery_dir, checked.entries)
            except Exception as rollback_error:
                receipt = ApplyReceipt(
                    operation_id=operation,
                    project_id=checked.project_id,
                    changeset_id=checked.changeset_id,
                    changeset_fingerprint=checked.fingerprint,
                    status=OperationStatus.FAILED_RECOVERY_REQUIRED,
                    recovery_status=RecoveryStatus.RECOVERY_REQUIRED,
                    recovery_id=recovery_id,
                    targets=tuple(recovery_targets),
                    validation_status=ValidationStatus.FAILED,
                    before_project_fingerprint=checked.base_project_fingerprint,
                    after_project_fingerprint=checked.base_project_fingerprint,
                    created_at=self._clock.now(),
                )
                self._store.save_receipt(receipt)
                raise RecoveryRequired("recovery is required") from rollback_error
            else:
                receipt = ApplyReceipt(
                    operation_id=operation,
                    project_id=checked.project_id,
                    changeset_id=checked.changeset_id,
                    changeset_fingerprint=checked.fingerprint,
                    status=OperationStatus.FAILED_ROLLED_BACK,
                    recovery_status=RecoveryStatus.RECOVERED,
                    recovery_id=recovery_id,
                    targets=tuple(recovery_targets),
                    validation_status=ValidationStatus.FAILED,
                    before_project_fingerprint=checked.base_project_fingerprint,
                    after_project_fingerprint=self.snapshot(tenant_id=checked.tenant_id, project_id=checked.project_id, target_id=checked.target.target_id).project_fingerprint,
                    created_at=self._clock.now(),
                )
                self._store.save_receipt(receipt)
                raise ApplyConflict("apply failed and was rolled back") from error

    def get_changeset(self, tenant_id: str, project_id: str, changeset_id: str) -> ChangeSet | None:
        return self._store.get_changeset(tenant_id, project_id, changeset_id)

    def list_changesets(self, tenant_id: str, project_id: str, *, limit: int = 20) -> tuple[ChangeSet, ...]:
        return tuple(self._store.list_changesets(tenant_id, project_id, limit=limit))

    def get_preview(self, project_id: str, changeset_id: str) -> ChangePreview | None:
        return self._store.get_preview(project_id, changeset_id)

    def get_approval(self, tenant_id: str, project_id: str, grant_id: str) -> ApplyApprovalGrant | None:
        return self._store.get_approval(tenant_id, project_id, grant_id)

    def get_operation(self, project_id: str, operation_id: str) -> ApplyReceipt | None:
        return self._store.get_receipt(project_id, operation_id)

    def get_receipt(self, project_id: str, operation_id: str) -> ApplyReceipt | None:
        return self._store.get_receipt(project_id, operation_id)

    def list_receipts(self, project_id: str, *, limit: int = 20) -> tuple[ApplyReceipt, ...]:
        return tuple(self._store.list_receipts(project_id, limit=limit))

    def list_audit(self, tenant_id: str, project_id: str, *, limit: int = 20) -> tuple[WorkspaceAuditEvent, ...]:
        return tuple(self._store.list_audit(tenant_id, project_id, limit=limit))

    def recover(self, *, changeset: ChangeSet, operation_id: str) -> ApplyReceipt:
        """Retry recovery for an operation explicitly marked recovery-required."""
        checked = _copy_model(changeset, ChangeSet)
        existing = self._store.get_receipt(checked.project_id, operation_id)
        if existing is None or existing.status is not OperationStatus.FAILED_RECOVERY_REQUIRED or existing.recovery_id is None:
            raise ApplyRejected("recovery is not required")
        target = self._resolve(checked.tenant_id, checked.project_id, checked.target.target_id)
        recovery_dir = self._state_root / "recovery" / operation_id
        self._rollback(target.root, recovery_dir, checked.entries)
        current = self.snapshot(tenant_id=checked.tenant_id, project_id=checked.project_id, target_id=checked.target.target_id)
        recovered = ApplyReceipt(
            operation_id=existing.operation_id,
            project_id=existing.project_id,
            changeset_id=existing.changeset_id,
            changeset_fingerprint=existing.changeset_fingerprint,
            status=OperationStatus.RECOVERED,
            recovery_status=RecoveryStatus.RECOVERED,
            recovery_id=existing.recovery_id,
            targets=existing.targets,
            validation_status=ValidationStatus.PASSED,
            before_project_fingerprint=existing.before_project_fingerprint,
            after_project_fingerprint=current.project_fingerprint,
            created_at=self._clock.now(),
        )
        return self._store.save_receipt(recovered)

    def _resolve(self, tenant_id: str, project_id: str, target_id: str | None) -> ResolvedWorkspaceTarget:
        target = self._resolver.resolve(tenant_id=tenant_id, project_id=project_id, target_id=target_id)
        root = target.root.resolve(strict=True)
        if root != target.root or not root.is_dir() or root.is_symlink():
            raise ApplyRejected("workspace target root is invalid")
        return ResolvedWorkspaceTarget(target.tenant_id, target.project_id, target.target_id, root)

    def _scan(self, root: Path) -> tuple[WorkspaceFile, ...]:
        values: list[WorkspaceFile] = []
        stack = [root]
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in sorted(entries, key=lambda item: item.name.casefold(), reverse=True):
                    path = Path(entry.path)
                    if entry.is_symlink():
                        raise ApplyRejected("symlink or junction escape")
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name == ".embedded-copilot-state":
                            continue
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        relative = path.relative_to(root).as_posix()
                        data = path.read_bytes()
                        values.append(WorkspaceFile(relative_file_reference=relative, exists=True, content_fingerprint=content_fingerprint(data), size=len(data)))
        return tuple(sorted(values, key=lambda item: item.relative_file_reference))

    @staticmethod
    def _project_fingerprint(files: tuple[WorkspaceFile, ...]) -> str:
        return _payload_fingerprint([item.model_dump(mode="json") for item in files])

    @staticmethod
    def _expected_target_fingerprint(snapshot: WorkspaceSnapshot, entries: tuple[ChangeSetEntry, ...], base: str) -> str:
        relevant = tuple((entry.relative_file_reference, next((item.content_fingerprint for item in snapshot.files if item.relative_file_reference == entry.relative_file_reference), _ZERO)) for entry in entries)
        return _payload_fingerprint({"base": base, "files": relevant})

    @staticmethod
    def _safe_path(root: Path, relative: str) -> Path:
        normalized = relative.replace("\\", "/")
        if any(part in {"", ".", ".."} for part in normalized.split("/")) or ":" in normalized or normalized.startswith("/"):
            raise ApplyRejected("path escape rejected")
        candidate = root.joinpath(*normalized.split("/"))
        cursor = root
        for part in normalized.split("/"):
            cursor = cursor / part
            if cursor.exists() and cursor.is_symlink():
                raise ApplyRejected("symlink or junction escape")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root.resolve(strict=True))
        except ValueError as error:
            raise ApplyRejected("path escape rejected") from error
        return candidate

    def _stage_path(self, operation_id: str) -> Path:
        return self._state_root / "staging" / operation_id

    def _copy_to_staging(self, root: Path, stage: Path) -> None:
        if stage.exists():
            return
        stage.mkdir(parents=True, exist_ok=False)
        for item in self._scan(root):
            source = self._safe_path(root, item.relative_file_reference)
            target = self._safe_path(stage, item.relative_file_reference)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def _apply_entries(self, root: Path, entries: tuple[ChangeSetEntry, ...]) -> None:
        for entry in entries:
            target = self._safe_path(root, entry.relative_file_reference)
            current = target.read_bytes() if target.exists() else b""
            if entry.change_type is ChangeType.MODIFY_FILE and content_fingerprint(current) != entry.before_fingerprint:
                raise ApplyConflict("target changed during apply")
            if entry.change_type is ChangeType.CREATE_FILE and target.exists():
                raise ApplyConflict("create target appeared during apply")
            if entry.after_content is None:
                raise ApplyUnavailable("change material is unavailable")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as handle:
                handle.write(entry.after_content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if content_fingerprint(target.read_bytes()) != entry.after_fingerprint:
                raise ApplyConflict("post-write fingerprint mismatch")

    def _rollback(self, root: Path, recovery_dir: Path, entries: tuple[ChangeSetEntry, ...]) -> None:
        for entry in reversed(entries):
            target = self._safe_path(root, entry.relative_file_reference)
            backup = recovery_dir / entry.entry_id
            if not backup.exists():
                raise RecoveryRequired("recovery artifact is missing")
            before = backup.read_bytes()
            if entry.before_fingerprint == _ZERO and not before:
                if target.exists():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(before)
                self._fsync_file(target)
            if target.exists() and content_fingerprint(target.read_bytes()) != entry.before_fingerprint:
                raise RecoveryRequired("rollback fingerprint mismatch")

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb+") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _grant_matches(changeset: ChangeSet, grant: ApplyApprovalGrant) -> bool:
        return (
            grant.tenant_id == changeset.tenant_id
            and grant.project_id == changeset.project_id
            and grant.proposal_id == changeset.proposal_id
            and grant.proposal_fingerprint == changeset.proposal_fingerprint
            and grant.changeset_id == changeset.changeset_id
            and grant.changeset_fingerprint == changeset.fingerprint
            and grant.target_fingerprint == changeset.target_fingerprint
            and grant.project_fingerprint == changeset.base_project_fingerprint
            and grant.staging_fingerprint == changeset.staging_fingerprint
        )

    def _is_tombstoned(self, tenant_id: str, project_id: str) -> bool:
        return bool(getattr(self._store, "is_project_tombstoned")(tenant_id, project_id))

    def _reject_operation(self, operation_id: str, changeset: ChangeSet, code: str) -> None:
        try:
            view = OperationView(
                operation_id=operation_id,
                project_id=changeset.project_id,
                changeset_id=changeset.changeset_id,
                operation=ApplyOperation.WORKSPACE_APPLY,
                status=OperationStatus.REJECTED,
                recovery_status=RecoveryStatus.NOT_REQUIRED,
                validation_status=ValidationStatus.FAILED,
                reason_code=code,
                detail=code,
                created_at=self._clock.now(),
            )
            self._store.save_operation(view)
        except Exception:
            pass

    def _audit(self, changeset: ChangeSet, approval: ApplyApprovalGrant, receipt: ApplyReceipt) -> None:
        event = WorkspaceAuditEvent(
            event_id=deterministic_id("v65-audit", receipt.operation_id, receipt.status.value),
            operation_id=receipt.operation_id,
            tenant_id=changeset.tenant_id,
            project_id=changeset.project_id,
            requester_id=changeset.requester_id,
            reviewer_id=approval.reviewer_id,
            proposal_id=changeset.proposal_id,
            changeset_id=changeset.changeset_id,
            approval_id=approval.grant_id,
            before_fingerprint=receipt.before_project_fingerprint,
            after_fingerprint=receipt.after_project_fingerprint,
            status=receipt.status,
            recovery_status=receipt.recovery_status,
            validation_status=receipt.validation_status,
            occurred_at=receipt.created_at,
        )
        self._store.append_audit(event)


__all__ = ("WorkspaceApplyService",)
