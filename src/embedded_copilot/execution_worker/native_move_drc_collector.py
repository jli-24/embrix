"""Post-APPLY DRC capture through the existing fixed Worker process boundary.

Only a trusted composition supplies frozen closure files and a DRC phase. This
module does not create a baseline authority or retry a native invocation.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService

from .adapters import SafeProcessRunner
from .artifacts import ArtifactStore
from .contracts import (
    ApprovalStatus,
    ArtifactIdentity,
    KiCadNativeFootprintMoveOperation,
    NativeMoveDrcPolicy,
    NativeMoveDrcResultV1,
    OperationStatus,
)
from .drc_baseline import BaselineClosureFile, PcbDrcBaselineCollector, _encoded, _sha
from .native_drc_provenance import (
    DRC_ALLOWED_ENV_NAMES,
    DRC_ARGV_TEMPLATE,
    NORMALIZER_ID,
    NORMALIZER_VERSION,
    RAW_VERSION,
    build_drc_v2_context,
    validate_drc_v2_context,
)
from .native_move import NativeReadbackPhase, native_drc_comparison_fingerprint

COLLECTOR_ID = "KICAD_DRC_COLLECTOR"
COLLECTOR_VERSION = "1"


class NativeMoveDrcCollector:
    """Collect one post-APPLY raw report with canonical authority and provenance."""

    def __init__(
        self, *, workspace: object, artifact_store: object, store: object, catalog: object,
        authorize_phase: Callable[[str, KiCadNativeFootprintMoveOperation, object, datetime], bool],
        tool_bundle: tuple[BaselineClosureFile, ...], rules: tuple[BaselineClosureFile, ...],
        dependencies: tuple[BaselineClosureFile, ...], environment: tuple[tuple[str, str], ...],
        report_root: Path, runner: SafeProcessRunner, clock: Callable[[], datetime],
    ) -> None:
        self.workspace = workspace
        self.artifact_store = artifact_store
        self.store = store
        self.catalog = catalog
        self.authorize_phase = authorize_phase
        self.tool_bundle = tool_bundle
        self.rules = rules
        self.dependencies = dependencies
        self.environment = environment
        self.report_root = Path(report_root)
        self.runner = runner
        self.clock = clock

    def _closure(self, policy: NativeMoveDrcPolicy) -> tuple[dict[str, bytes], dict]:
        if (type(self.runner) is not SafeProcessRunner
                or type(self.environment) is not tuple or not self.environment
                or self.environment != tuple(sorted(self.environment))
                or len({name for name, _ in self.environment}) != len(self.environment)
                or any(name not in DRC_ALLOWED_ENV_NAMES or type(value) is not str or "\x00" in value
                       for name, value in self.environment)):
            raise ValueError("frozen DRC environment or runner unavailable")
        executable = [item for item in self.tool_bundle if item.role == "EXECUTABLE"]
        if (len(executable) != 1 or executable[0].sha256 != policy.executable_sha256
                or executable[0].absolute_path.name.casefold() not in {"kicad-cli", "kicad-cli.exe"}
                or policy.tool_version != RAW_VERSION):
            raise ValueError("frozen DRC executable unavailable")
        values = {
            "tool_bundle": PcbDrcBaselineCollector._manifest(self.tool_bundle),
            "rules_manifest": PcbDrcBaselineCollector._manifest(self.rules),
            "dependency_manifest": PcbDrcBaselineCollector._manifest(self.dependencies),
            "environment": _encoded({"schema_version": 1, "entries": [
                {"name": name, "value": value} for name, value in self.environment]}),
        }
        baseline = json.loads(self.artifact_store.read(policy.baseline_report).decode("utf-8"))
        validate_drc_v2_context(baseline, read_artifact=self.artifact_store.read, policy=policy)
        provenance = json.loads(self.artifact_store.read(
            ArtifactIdentity.model_validate(baseline["provenance"])).decode("utf-8"))
        if (provenance["collector_id"], provenance["collector_version"]) != (COLLECTOR_ID, COLLECTOR_VERSION):
            raise ValueError("baseline collector identity is not supported by post-APPLY capture")
        for role, content in values.items():
            identity = ArtifactIdentity.model_validate(provenance[role])
            if self.artifact_store.read(identity) != content:
                raise ValueError("frozen DRC closure differs from baseline")
        return values, provenance

    def _report_directory(self, policy: NativeMoveDrcPolicy) -> Path:
        root = self.workspace._resolve(
            policy.baseline_report.tenant_id, policy.baseline_report.project_id,
            policy.baseline_report.target_id,
        ).root.resolve(strict=True)
        output = self.report_root.resolve(strict=False)
        if (not self.report_root.is_absolute() or output == root or output.is_relative_to(root)
                or root.is_relative_to(output) or any(
                    item.exists() and (item.is_symlink() or getattr(item.lstat(), "st_file_attributes", 0) & 0x400)
                    for item in (output, *output.parents))):
            raise ValueError("DRC report root overlaps Workspace or is a reparse path")
        return output

    def ready_for(self, policy: NativeMoveDrcPolicy) -> bool:
        try:
            return (type(policy) is NativeMoveDrcPolicy
                    and isinstance(self.workspace, WorkspaceApplyService)
                    and isinstance(self.artifact_store, ArtifactStore)
                    and callable(self.authorize_phase)
                    and callable(self.clock) and callable(getattr(self.artifact_store, "put", None))
                    and callable(getattr(self.store, "get_operation", None))
                    and callable(getattr(self.catalog, "revalidate_native_move_readback", None))
                    and bool(self._report_directory(policy))
                    and bool(self._closure(policy)))
        except Exception:  # noqa: BLE001 - trusted composition is fail closed
            return False

    def _authorized(
        self, operation: KiCadNativeFootprintMoveOperation, board: Path,
        policy: NativeMoveDrcPolicy, phase: NativeReadbackPhase,
    ) -> bool:
        try:
            if (type(operation) is not KiCadNativeFootprintMoveOperation
                    or type(policy) is not NativeMoveDrcPolicy
                    or type(phase) is not NativeReadbackPhase
                    or phase.is_cancelled() or phase.timeout_seconds() < 1
                    or phase.policy_timeout_seconds > policy.timeout_seconds):
                return False
            context = operation.production_context
            if context is None or context.drc_policy != policy:
                return False
            canonical = self.store.get_operation(operation.tenant_id, operation.project_id, operation.operation_id)
            status = self.store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
            if (canonical != operation or status is None or status.status is not OperationStatus.RUNNING
                    or status.cancellation_requested or not status.approval_id):
                return False
            approval = self.store.get_approval(operation.tenant_id, operation.project_id, status.approval_id)
            now = self.clock()
            if (approval is None or approval.status is not ApprovalStatus.CONSUMED
                    or approval.operation_id != operation.operation_id
                    or approval.operation_fingerprint != operation.fingerprint
                    or approval.reviewer_id != operation.requester_id
                    or now >= min(operation.expires_at, approval.expires_at, phase.deadline)
                    or self.authorize_phase("DRC", operation, approval, now) is not True):
                return False
            binding = self.catalog._native_move_readback_binding(operation)
            if binding is None or self.catalog.revalidate_native_move_readback(operation, approval) is not True:
                return False
            target = self.workspace._resolve(operation.tenant_id, operation.project_id, operation.target_id).root
            path = self.workspace._safe_path(target, operation.pcb_relative_path)
            if Path(board) != path or not path.is_file() or _sha(path.read_bytes()) != context.changeset.entries[0].after_fingerprint:
                return False
            # Catalog and Workspace reads may be slow; close their authority window.
            refreshed = self.store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
            return (self.store.get_operation(operation.tenant_id, operation.project_id, operation.operation_id) == operation
                    and self.store.get_approval(operation.tenant_id, operation.project_id, status.approval_id) == approval
                    and refreshed is not None and refreshed.status is OperationStatus.RUNNING
                    and not refreshed.cancellation_requested and refreshed.approval_id == status.approval_id
                    and self.catalog._native_move_readback_binding(operation) == binding
                    and self.authorize_phase("DRC", operation, approval, self.clock()) is True
                    and self.clock() < min(operation.expires_at, approval.expires_at, phase.deadline)
                    and not phase.is_cancelled() and phase.timeout_seconds() >= 1)
        except Exception:  # noqa: BLE001 - canonical evidence and authority fail closed
            return False

    def compare(
        self, operation: KiCadNativeFootprintMoveOperation, board: Path,
        policy: NativeMoveDrcPolicy, phase: NativeReadbackPhase,
    ) -> NativeMoveDrcResultV1:
        if not self._authorized(operation, board, policy, phase):
            raise PermissionError("post-APPLY DRC phase unavailable")
        values, _ = self._closure(policy)
        root = self.workspace._resolve(operation.tenant_id, operation.project_id, operation.target_id).root.resolve(strict=True)
        output = self._report_directory(policy)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", operation.operation_id) is None:
            raise ValueError("DRC operation identity unavailable")
        report = output / (operation.operation_id + ".json")
        if report.exists():
            raise ValueError("DRC report already exists; no replay")
        snapshot = self.workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id,
                                           target_id=operation.target_id)
        pcb_bytes = Path(board).read_bytes()
        if _sha(pcb_bytes) != operation.production_context.changeset.entries[0].after_fingerprint:
            raise ValueError("post-APPLY PCB changed")
        prl_path = self.workspace._safe_path(root, operation.production_context.side_effect_policy.prl_relative_path)
        prl_bytes = prl_path.read_bytes() if prl_path.is_file() else None
        if prl_bytes is not None and prl_path.is_symlink():
            raise ValueError("post-APPLY PRL is not a regular Workspace file")
        workspace_bytes = _encoded({"schema_version": 1, "entries": [
            {"role": "FILE", "relative_path": item.relative_file_reference,
             "sha256": item.content_fingerprint, "byte_size": item.size, "source": "canonical-workspace"}
            for item in sorted(snapshot.files, key=lambda item: item.relative_file_reference)]})
        prefix = "drc-after-" + hashlib.sha256(operation.operation_id.encode()).hexdigest()[:32] + "-"
        def put(role: str, content: bytes, kind: str = "LOG", *,
                envelope: bool = False) -> ArtifactIdentity:
            identity = ArtifactIdentity(
                artifact_id=prefix + role, tenant_id=operation.tenant_id, project_id=operation.project_id,
                target_id=operation.target_id,
                workspace_fingerprint=(operation.workspace_fingerprint if envelope
                                       else snapshot.project_fingerprint),
                build_operation_id=operation.operation_id, toolchain_id=policy.tool_id,
                toolchain_version=policy.tool_version, build_configuration="DRC_POST_APPLY",
                artifact_type=kind, artifact_sha256=_sha(content), size_bytes=len(content),
                created_at=self.clock(),
            )
            return self.artifact_store.put(identity, content)
        refs = {role: put(role.replace("_", "-"), content,
                          "MANIFEST" if role.endswith("manifest") or role == "tool_bundle" else "LOG")
                for role, content in values.items()}
        refs["input_pcb"] = put("input-pcb", pcb_bytes)
        refs["workspace_manifest"] = put("workspace-manifest", workspace_bytes, "MANIFEST")
        prl_before = None if prl_bytes is None else put("prl-before", prl_bytes)
        if not self._authorized(operation, board, policy, phase) or self._closure(policy)[0] != values:
            raise PermissionError("post-APPLY DRC authority or closure changed")
        timeout = phase.timeout_seconds()
        if timeout < 1:
            raise TimeoutError("post-APPLY DRC deadline expired")
        output.mkdir(parents=True, exist_ok=True)
        if report.exists():
            raise ValueError("DRC report already exists; no replay")
        executable = next(item.absolute_path for item in self.tool_bundle if item.role == "EXECUTABLE")
        collector = self
        class DispatchPhase:
            def is_cancelled(self) -> bool:
                # Closure I/O can be slow; authority must be the final check.
                return (not collector.ready_for(policy)
                        or not collector._authorized(operation, board, policy, phase))
        # FixedSafeProcessRunner checks this immediately before Popen and while running.
        if not self._authorized(operation, board, policy, phase):
            raise PermissionError("post-APPLY DRC authority expired before dispatch")
        try:
            exit_code, _, _ = self.runner.run_drc_baseline(
                (str(executable), *DRC_ARGV_TEMPLATE[:5], str(report), str(board)),
                cwd=root, timeout_seconds=timeout, environment=dict(self.environment),
                cancellation=DispatchPhase(),
            )
        finally:
            # Preserve actual raw output and post-run state even on timeout/cancel/exit failure.
            if report.is_file() and not report.is_symlink():
                put("raw-report", report.read_bytes())
            try:
                after = self.workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id,
                                                target_id=operation.target_id)
                put("workspace-after", _encoded({"schema_version": 1, "entries": [
                    {"role": "FILE", "relative_path": item.relative_file_reference,
                     "sha256": item.content_fingerprint, "byte_size": item.size, "source": "canonical-workspace"}
                    for item in sorted(after.files, key=lambda item: item.relative_file_reference)]}), "MANIFEST")
                if prl_path.is_file() and not prl_path.is_symlink():
                    put("prl-after-observed", prl_path.read_bytes())
            except (OSError, RuntimeError, TypeError, ValueError):
                pass
        raw = report.read_bytes()
        raw_ref = put("raw-report", raw)
        after = self.workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id,
                                        target_id=operation.target_id)
        current_prl = prl_path.read_bytes() if prl_path.is_file() else None
        prl_after = None if current_prl is None else put("prl-after", current_prl)
        if (exit_code != 0 or snapshot.files != after.files or prl_bytes != current_prl
                or not self._authorized(operation, board, policy, phase)
                or self._closure(policy)[0] != values):
            raise ValueError("post-APPLY DRC failed or changed its inputs")
        provenance = {
            "schema_version": 2, "collector_id": COLLECTOR_ID,
            "collector_version": COLLECTOR_VERSION,
            "tool_id": policy.tool_id, "tool_version": policy.tool_version,
            "executable_sha256": policy.executable_sha256,
            "normalizer_id": NORMALIZER_ID, "normalizer_version": NORMALIZER_VERSION,
            **{role: identity.model_dump(mode="json") for role, identity in refs.items()},
            "argv_template": list(DRC_ARGV_TEMPLATE),
            "argv_slots": {
                "pcb": {"relative_path": operation.pcb_relative_path,
                        "artifact": refs["input_pcb"].model_dump(mode="json")},
                "report": {"role": "DRC_REPORT", "relative_path": "after-reports/" + report.name,
                           "artifact": raw_ref.model_dump(mode="json")},
            },
            "prl_relative_path": operation.production_context.side_effect_policy.prl_relative_path,
            "prl_before": None if prl_before is None else prl_before.model_dump(mode="json"),
            "prl_after": None if prl_after is None else prl_after.model_dump(mode="json"),
            "raw_sha256": raw_ref.artifact_sha256, "scope": policy.comparison_scope,
            "policy": {"zone_fill_policy": policy.zone_fill_policy,
                       "schematic_parity": policy.schematic_parity,
                       "included_severities": list(policy.included_severities),
                       "ignored_checks": list(policy.ignored_checks)},
        }
        provenance_ref = put("provenance", _encoded(provenance))
        context = build_drc_v2_context(raw_ref, provenance_ref,
                                       read_artifact=self.artifact_store.read, policy=policy)
        report_ref = put("context", _encoded(context), envelope=True)
        if not self._authorized(operation, board, policy, phase):
            raise PermissionError("post-APPLY DRC authority expired after persistence")
        return NativeMoveDrcResultV1(
            status="COMPARABILITY_UNKNOWN", report=report_ref, tool_id=policy.tool_id,
            tool_version=policy.tool_version, executable_sha256=policy.executable_sha256,
            comparison_context_fingerprint=native_drc_comparison_fingerprint(operation, snapshot.project_fingerprint),
            baseline_report_sha256=policy.baseline_report.artifact_sha256,
        )
