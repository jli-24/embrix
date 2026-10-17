"""Narrow C2-R production seam. No native tool is registered by default."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from embedded_copilot.engineering_workspace_apply import (
    ChangeSet,
    WorkspaceApplyService,
)
from embedded_copilot.pcb.parser.kicad import (
    _atom,
    _children,
    _head,
    _parse_s_expression,
    stage_kicad_footprint_xy_bytes,
)
from embedded_copilot.pcb.parser.models import PCBParserLimits

from .contracts import (
    ApprovalStatus,
    ArtifactIdentity,
    KiCadNativeFootprintMoveOperation,
    NativeMoveDrcPolicy,
    NativeMoveDrcResultV1,
    NativeMoveProductionResultV1,
    NativeMoveReadBackV1,
    NativeMoveReaderPolicy,
    NativeMoveSideEffectPolicy,
    OperationApprovalGrant,
    RecoveryState,
)
from .native_drc_provenance import compare_drc_v2_contexts, validate_drc_v2_context
from .native_move_projection import READER_ID, source_projection
from .ports import AdapterResult


@dataclass(frozen=True, slots=True)
class ReviewedNativeMoveProposal:
    proposal_id: str
    proposal_fingerprint: str
    tenant_id: str
    project_id: str
    requester_id: str
    validation_fingerprint: str
    evidence_fingerprint: str

    @property
    def status(self) -> str:
        return "APPROVED"


@dataclass(frozen=True, slots=True)
class NativeMoveProductionSetup:
    workspace: WorkspaceApplyService
    reader_policy: NativeMoveReaderPolicy
    drc_policy: NativeMoveDrcPolicy
    side_effect_policy: NativeMoveSideEffectPolicy
    artifact_store: object
    validate_policies: Callable[[NativeMoveReaderPolicy, NativeMoveDrcPolicy, NativeMoveSideEffectPolicy], bool]
    authorize_phase: Callable[[str, KiCadNativeFootprintMoveOperation, OperationApprovalGrant, datetime], bool]
    reader: object
    drc: object

    def available(self) -> bool:
        try:
            baseline = self.artifact_store.read(self.drc_policy.baseline_report)
            baseline_document = json.loads(baseline.decode("utf-8"))
            validate_drc_v2_context(
                baseline_document, read_artifact=self.artifact_store.read, policy=self.drc_policy,
            )
            baseline_raw = ArtifactIdentity.model_validate(baseline_document["raw_report"])
            if any(getattr(self.drc_policy.baseline_report, field) != getattr(baseline_raw, field)
                   for field in ("tenant_id", "project_id", "target_id", "workspace_fingerprint",
                                 "build_operation_id", "build_configuration")):
                return False
            if self.reader_policy.reader_id == READER_ID:
                from .native_move_pcbnew_reader import NativePcbnewReader
                reader_ready = (
                    type(self.reader) is NativePcbnewReader
                    and self.reader.workspace is self.workspace
                    and self.reader.artifact_store is self.artifact_store
                    and self.reader.authorize_phase is self.authorize_phase
                    and self.reader.ready_for(self.reader_policy)
                )
            else:
                reader_ready = callable(getattr(self.reader, "read_back", None))
            from .native_move_drc_collector import NativeMoveDrcCollector
            drc_ready = callable(getattr(self.drc, "compare", None))
            if isinstance(self.drc, NativeMoveDrcCollector):
                drc_ready = (
                    type(self.drc) is NativeMoveDrcCollector
                    and self.drc.workspace is self.workspace
                    and self.drc.artifact_store is self.artifact_store
                    and self.drc.authorize_phase is self.authorize_phase
                    and self.drc.ready_for(self.drc_policy)
                )
            return (
                self.drc_policy.baseline_report.toolchain_id == self.drc_policy.tool_id
                and self.drc_policy.baseline_report.toolchain_version == self.drc_policy.tool_version
                and isinstance(self.workspace, WorkspaceApplyService)
                and callable(self.validate_policies)
                and self.validate_policies(self.reader_policy, self.drc_policy, self.side_effect_policy) is True
                and isinstance(baseline, bytes)
                and "sha256:" + hashlib.sha256(baseline).hexdigest() == self.drc_policy.baseline_report.artifact_sha256
                and reader_ready
                and drc_ready
                and callable(self.authorize_phase)
            )
        except Exception:  # noqa: BLE001 - trusted reader availability must fail closed
            return False

    def staging_current(self, changeset: ChangeSet) -> bool:
        try:
            canonical = self.workspace.get_changeset(changeset.tenant_id, changeset.project_id, changeset.changeset_id)
            if canonical != changeset:
                return False
            stage = self.workspace._stage_path(changeset.changeset_id)
            if not stage.is_dir():
                return False
            files = self.workspace._scan(stage)
            if self.workspace._project_fingerprint(files) != changeset.staging_fingerprint:
                return False
            entry = changeset.entries[0]
            staged = self.workspace._safe_path(stage, entry.relative_file_reference).read_bytes()
            return (
                entry.after_content is not None
                and staged == entry.after_content.encode("utf-8")
                and "sha256:" + hashlib.sha256(staged).hexdigest() == entry.after_fingerprint
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _same_files(left, right) -> bool:
    return (
        left.tenant_id, left.project_id, left.target_id,
        left.project_fingerprint, left.target_fingerprint, left.files,
    ) == (
        right.tenant_id, right.project_id, right.target_id,
        right.project_fingerprint, right.target_fingerprint, right.files,
    )


def _contains_sensitive_value(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            name = key.casefold()
            if any(token in name for token in ("path", "credential", "password", "token", "secret", "ssh_key", "repo_type", "repo_username")) and child not in (None, "", [], {}):
                return True
            if _contains_sensitive_value(child):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_value(item) for item in value)
    return False


def _prl_schema(document: object, policy: NativeMoveSideEffectPolicy) -> bool:
    if not isinstance(document, dict) or not set(document) <= {"board", "net_inspector_panel", "schematic", "project", "open_jobsets", "meta", "git"}:
        return False
    board = document.get("board", {})
    board_keys = {"active_layer", "active_layer_preset", "auto_track_width", "hidden_netclasses", "hidden_nets", "high_contrast_mode", "net_color_mode", "opacity", "prototype_zone_fills", "selection_filter", "visible_items", "visible_layers", "zone_display_mode"}
    panel_keys = {"col_hidden", "col_order", "col_widths", "custom_group_rules", "expanded_rows", "filter_by_net_name", "filter_by_netclass", "filter_text", "group_by_constraint", "group_by_netclass", "show_time_domain_details", "show_unconnected_nets", "show_zero_pad_nets", "sort_ascending", "sorting_column"}
    board_selection = {"dimensions", "footprints", "graphics", "keepouts", "lockedItems", "otherItems", "pads", "text", "tracks", "vias", "zones"}
    schematic_selection = {"graphics", "images", "labels", "lockedItems", "otherItems", "pins", "ruleAreas", "symbols", "text", "wires"}
    panel = document.get("net_inspector_panel", {})
    schematic = document.get("schematic", {})
    project = document.get("project", {})
    meta = document.get("meta", {})
    git = document.get("git", {})
    if any(not isinstance(value, dict) for value in (board, panel, schematic, project, meta, git)):
        return False
    if not (set(board) <= board_keys and set(panel) <= panel_keys and set(schematic) <= {"selection_filter", "hierarchy_collapsed"}
            and set(project) <= {"files"} and set(meta) <= {"filename", "version"}
            and set(git) <= {"integration_disabled", "repo_type", "repo_username", "ssh_key"}):
        return False
    if any(not isinstance(board.get(key, {}), dict) for key in ("opacity", "selection_filter")) or not isinstance(schematic.get("selection_filter", {}), dict):
        return False
    if not (set(board.get("opacity", {})) <= {"images", "pads", "shapes", "tracks", "vias", "zones"}
            and set(board.get("selection_filter", {})) <= board_selection
            and set(schematic.get("selection_filter", {})) <= schematic_selection):
        return False
    int_keys = {"active_layer", "high_contrast_mode", "net_color_mode", "zone_display_mode"}
    bool_keys = {"auto_track_width", "prototype_zone_fills"}
    str_keys = {"active_layer_preset", "visible_layers"}
    list_keys = {"hidden_netclasses", "hidden_nets", "visible_items"}
    if any(type(board[key]) is not int for key in int_keys & board.keys()):
        return False
    if any(type(board[key]) is not bool for key in bool_keys & board.keys()):
        return False
    if any(not isinstance(board[key], str) for key in str_keys & board.keys()):
        return False
    if any(not isinstance(board[key], list) or any(not isinstance(item, str) for item in board[key]) for key in list_keys & board.keys()):
        return False
    if any(type(value) not in (int, float) or not 0 <= value <= 1 for value in board.get("opacity", {}).values()):
        return False
    if any(type(value) is not bool for value in board.get("selection_filter", {}).values()):
        return False
    if any(type(value) is not bool for value in schematic.get("selection_filter", {}).values()):
        return False
    if "hierarchy_collapsed" in schematic and schematic["hierarchy_collapsed"] != []:
        return False
    panel_lists = {"col_hidden", "col_order", "col_widths", "custom_group_rules", "expanded_rows"}
    panel_bools = panel_keys - panel_lists - {"filter_text", "sorting_column"}
    if any(panel[key] != [] for key in panel_lists & panel.keys()):
        return False
    if any(type(panel[key]) is not bool for key in panel_bools & panel.keys()):
        return False
    if "filter_text" in panel and not isinstance(panel["filter_text"], str):
        return False
    if "sorting_column" in panel and type(panel["sorting_column"]) is not int:
        return False
    if "version" in meta and meta["version"] != 5:
        return False
    if "integration_disabled" in git and type(git["integration_disabled"]) is not bool:
        return False
    if document.get("open_jobsets", []) != [] or project.get("files", []) != []:
        return False
    if meta.get("filename", policy.prl_relative_path.rsplit("/", 1)[-1]) != policy.prl_relative_path.rsplit("/", 1)[-1]:
        return False
    if any(git.get(key, "") not in ("", None) for key in ("repo_type", "repo_username", "ssh_key")):
        return False
    return not _contains_sensitive_value(document)


def _prl_change_allowed(before: bytes | None, after: bytes | None, policy: NativeMoveSideEffectPolicy) -> bool:
    if (None if before is None else _sha(before)) != policy.prl_before_sha256:
        return False
    if before == after:
        return True
    if policy.prl_policy != "ALLOW_REVIEWED_KEYS" or after is None:
        return False
    try:
        old = {} if before is None else json.loads(before.decode("utf-8"))
        new = json.loads(after.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    if not _prl_schema(new, policy) or not _prl_schema(old, policy):
        return False
    changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
    if not changed <= set(policy.allowed_root_keys):
        return False
    for root in ("board", "net_inspector_panel", "schematic", "project", "meta", "git"):
        if root not in changed:
            continue
        before_child, after_child = old.get(root, {}), new.get(root, {})
        if not isinstance(before_child, dict) or not isinstance(after_child, dict):
            return False
        if root == "schematic" and any(before_child.get(key) != after_child.get(key) for key in set(before_child) | set(after_child) if key != "selection_filter"):
            return False
        if root == "git" and any(before_child.get(key) != after_child.get(key) for key in set(before_child) | set(after_child) if key != "integration_disabled"):
            return False
    return True


def expected_limited_observation(source: bytes, staged: bytes, native_uuid: str, *, reader_id: str | None = None) -> tuple[str, str]:
    """Compute frozen finite expectation; preserve the V1 tuple digest for old reader IDs."""
    if reader_id == READER_ID:
        before, after = source_projection(source, native_uuid), source_projection(staged, native_uuid)
        fixed = ("projection_schema", "coverage", "native_uuid", "rotation_degrees", "pad_net_fingerprint", "non_target_summary_fingerprint")
        if any(before[key] != after[key] for key in fixed):
            raise ValueError("native finite projection changed outside target x/y")
        return after["pad_net_fingerprint"], after["non_target_summary_fingerprint"]
    def summaries(payload: bytes) -> tuple[str, str]:
        text = payload.decode("utf-8", errors="strict")
        if "\x00" in text:
            raise ValueError("invalid PCB source")
        root = _parse_s_expression(text, PCBParserLimits())
        if _head(root) != "kicad_pcb":
            raise ValueError("invalid PCB root")
        nets = _children(root, "net")
        pads = []
        others = []
        target_count = 0
        for item in root[1:]:
            if not isinstance(item, tuple):
                others.append(item)
                continue
            if _head(item) != "footprint":
                others.append(item)
                continue
            uuids = _children(item, "uuid")
            is_target = len(uuids) == 1 and _atom(uuids[0], 1) == native_uuid
            pads.append((_atom(uuids[0], 1) if len(uuids) == 1 else None, _children(item, "pad")))
            if is_target:
                target_count += 1
                at = _children(item, "at")
                if len(at) != 1:
                    raise ValueError("ambiguous target placement")
                others.append(tuple(child for child in item if child is not at[0]))
            else:
                others.append(item)
        if target_count != 1:
            raise ValueError("ambiguous native target")
        canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return _sha(canonical((nets, pads))), _sha(canonical(others))
    before = summaries(source)
    after = summaries(staged)
    if before != after:
        raise ValueError("non-target or pad/net structure changed")
    return after

def _manifest_allowed(before, after, pcb_path: str, expected_pcb_sha256: str, policy: NativeMoveSideEffectPolicy, root: Path, before_prl: bytes | None) -> bool:
    left = {item.relative_file_reference: item.content_fingerprint for item in before.files}
    right = {item.relative_file_reference: item.content_fingerprint for item in after.files}
    changed = {key for key in left.keys() | right.keys() if left.get(key) != right.get(key)}
    if not changed <= {pcb_path, policy.prl_relative_path} or pcb_path not in changed or right.get(pcb_path) != expected_pcb_sha256:
        return False
    prl_path = root.joinpath(*policy.prl_relative_path.split("/"))
    after_prl = prl_path.read_bytes() if prl_path.is_file() else None
    return _prl_change_allowed(before_prl, after_prl, policy)


def _drc_snapshot_bound(context: dict, snapshot, pcb_path: str, prl_path: str,
                        pcb_bytes: bytes, prior_prl: bytes | None, current_prl: bytes | None,
                        *, read_artifact: Callable[[ArtifactIdentity], bytes], operation_id: str) -> None:
    """Bind re-read collector claims to the complete canonical Workspace inventory."""
    raw = ArtifactIdentity.model_validate(context["raw_report"])
    provenance = ArtifactIdentity.model_validate(context["provenance"])
    scope = (snapshot.tenant_id, snapshot.project_id, snapshot.target_id, snapshot.project_fingerprint)
    for item in (raw, provenance):
        if (item.tenant_id, item.project_id, item.target_id, item.workspace_fingerprint) != scope:
            raise ValueError("DRC artifact differs from canonical Workspace scope")
        if item.build_operation_id != operation_id:
            raise ValueError("DRC artifact operation differs from reviewed capture")
    document = json.loads(read_artifact(provenance).decode("utf-8"))
    if document["prl_relative_path"] != prl_path or document["argv_slots"]["pcb"]["relative_path"] != pcb_path:
        raise ValueError("DRC target or PRL path differs from reviewed operation")
    manifest = ArtifactIdentity.model_validate(document["workspace_manifest"])
    entries = json.loads(read_artifact(manifest).decode("utf-8"))["entries"]
    observed = {item["relative_path"]: (item["sha256"], item["byte_size"]) for item in entries if item["role"] == "FILE"}
    expected = {item.relative_file_reference: (item.content_fingerprint, item.size)
                for item in snapshot.files if item.exists}
    if len(observed) != len(entries) or observed != expected:
        raise ValueError("DRC Workspace FILE inventory differs from canonical snapshot")
    pcb = ArtifactIdentity.model_validate(document["input_pcb"])
    if (pcb.artifact_sha256, pcb.size_bytes) != (_sha(pcb_bytes), len(pcb_bytes)):
        raise ValueError("DRC input PCB differs from current Workspace bytes")
    for role, data in (("prl_before", prior_prl), ("prl_after", current_prl)):
        value = document[role]
        if (value is None) != (data is None):
            raise ValueError("DRC PRL presence differs from actual Workspace")
        if data is not None:
            identity = ArtifactIdentity.model_validate(value)
            if (identity.artifact_sha256, identity.size_bytes) != (_sha(data), len(data)):
                raise ValueError("DRC PRL differs from actual Workspace bytes")


def native_drc_comparison_fingerprint(operation: KiCadNativeFootprintMoveOperation, after_manifest_fingerprint: str) -> str:
    context = operation.production_context
    assert context is not None
    material = {
        "policy": context.drc_policy.model_dump(mode="json"),
        "before": context.workspace_snapshot.project_fingerprint,
        "after": after_manifest_fingerprint,
        "operation_id": operation.operation_id,
    }
    return _sha(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class NativeReadbackPhase:
    """Read-only process cancellation/deadline view; cannot issue a Workspace grant."""

    deadline: datetime
    policy_timeout_seconds: int
    clock: Callable[[], datetime]
    allowed: Callable[[], bool]

    def is_cancelled(self) -> bool:
        try:
            return not self.allowed()
        except Exception:  # noqa: BLE001 - runner cancellation must fail closed
            return True

    def timeout_seconds(self) -> int:
        if self.is_cancelled():
            return 0
        remaining = (self.deadline - self.clock()).total_seconds()
        return max(0, min(self.policy_timeout_seconds, int(remaining)))


class NativeMoveProductionAdapter:
    """Applies one reviewed PCB through Workspace, then invokes separately authorized independent ports."""

    def __init__(self, setup: NativeMoveProductionSetup, *, clock: Callable[[], datetime]) -> None:
        if not isinstance(setup, NativeMoveProductionSetup) or not callable(clock):
            raise TypeError("native move adapter composition is invalid")
        self._setup = setup
        self._clock = clock

    def _allowed(self, phase: str, operation: KiCadNativeFootprintMoveOperation, approval: OperationApprovalGrant, cancellation: object) -> bool:
        try:
            now = self._clock()
            context = operation.production_context
            return bool(
                context is not None
                and context.native_reader_policy == self._setup.reader_policy
                and context.drc_policy == self._setup.drc_policy
                and context.side_effect_policy == self._setup.side_effect_policy
                and self._setup.validate_policies(
                    context.native_reader_policy, context.drc_policy, context.side_effect_policy,
                ) is True
                and approval.status is ApprovalStatus.CONSUMED
                and approval.operation_id == operation.operation_id
                and approval.operation_fingerprint == operation.fingerprint
                and approval.reviewer_id == operation.requester_id
                and now < min(operation.expires_at, approval.expires_at)
                and not cancellation.is_cancelled()
                and self._setup.authorize_phase(phase, operation, approval, now) is True
            )
        except Exception:  # noqa: BLE001 - preserve post-apply facts on any ordinary port or I/O failure
            return False

    def _result(self, operation, *, status: str, summary: str, before_fp: str, after_fp: str | None = None,
                apply_receipt=None, grant_fp: str | None = None, apply_status: str = "NOT_RUN",
                readback=None, drc=None, recovery: RecoveryState = RecoveryState.NONE) -> AdapterResult:
        context = operation.production_context
        assert context is not None
        production = NativeMoveProductionResultV1(
            candidate_id=operation.proposal_id, candidate_fingerprint=operation.proposal_fingerprint,
            mapping_review_id=operation.mapping_review_id, mapping_review_fingerprint=operation.mapping_review_fingerprint,
            import_revision_id=operation.import_revision_id, import_revision_fingerprint=operation.import_revision_fingerprint,
            production_context_fingerprint=context.fingerprint, before_manifest_fingerprint=before_fp,
            after_manifest_fingerprint=after_fp,
            workspace_apply_receipt_fingerprint=None if apply_receipt is None else apply_receipt.fingerprint,
            workspace_approval_fingerprint=grant_fp, workspace_apply_status=apply_status,
            native_read_back=readback, drc_result=drc, recovery_state=recovery,
        )
        return AdapterResult(
            status=status, summary=summary, workspace_apply_receipt=apply_receipt,
            native_move_production=production,
        )

    def _artifact_bytes(self, artifact) -> bytes:
        data = self._setup.artifact_store.read(artifact)
        if not isinstance(data, bytes) or _sha(data) != artifact.artifact_sha256 or len(data) != artifact.size_bytes:
            raise ValueError("native observation artifact mismatch")
        return data

    def execute(self, operation: KiCadNativeFootprintMoveOperation, approval: OperationApprovalGrant, cancellation: object) -> AdapterResult:
        context = operation.production_context
        if context is None or not self._setup.available() or not self._setup.staging_current(context.changeset):
            raise ValueError("native production context unavailable")
        workspace = self._setup.workspace
        before = workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id)
        frozen = context.workspace_snapshot
        if not _same_files(before, frozen):
            return self._result(operation, status="FAILED", summary="native Workspace snapshot changed", before_fp=frozen.project_fingerprint)
        root = workspace._resolve(operation.tenant_id, operation.project_id, operation.target_id).root
        board = workspace._safe_path(root, operation.pcb_relative_path)
        original = board.read_bytes()
        prl_path = workspace._safe_path(root, context.side_effect_policy.prl_relative_path)
        before_prl = prl_path.read_bytes() if prl_path.is_file() else None
        try:
            baseline = json.loads(self._artifact_bytes(context.drc_policy.baseline_report).decode("utf-8"))
            baseline = validate_drc_v2_context(
                baseline, read_artifact=self._setup.artifact_store.read, policy=context.drc_policy,
            )
            baseline_raw = ArtifactIdentity.model_validate(baseline["raw_report"])
            baseline_envelope = context.drc_policy.baseline_report
            if (
                baseline_envelope.tenant_id, baseline_envelope.project_id, baseline_envelope.target_id,
                baseline_envelope.workspace_fingerprint, baseline_envelope.build_operation_id,
                baseline_envelope.build_configuration,
            ) != (
                before.tenant_id, before.project_id, before.target_id,
                before.project_fingerprint, baseline_raw.build_operation_id,
                baseline_raw.build_configuration,
            ):
                raise ValueError("DRC baseline envelope is not bound to frozen Workspace")
            _drc_snapshot_bound(
                baseline, before, operation.pcb_relative_path, context.side_effect_policy.prl_relative_path,
                original, before_prl, before_prl, read_artifact=self._setup.artifact_store.read,
                operation_id=context.drc_policy.baseline_report.build_operation_id,
            )
        except Exception:  # noqa: BLE001 - invalid baseline must never authorize APPLY
            return self._result(
                operation, status="UNAVAILABLE", summary="native DRC baseline Workspace evidence unavailable",
                before_fp=before.project_fingerprint,
            )
        expected = context.changeset.entries[0].after_content
        if expected is None:
            raise ValueError("native staged content unavailable")
        staged = stage_kicad_footprint_xy_bytes(
            original, operation.native_uuid, expected_old=(operation.before_x_mm, operation.before_y_mm),
            new=(operation.after_x_mm, operation.after_y_mm), expected_source_sha256=operation.pcb_sha256.removeprefix("sha256:"),
        )
        if staged.staged_bytes != expected.encode("utf-8") or _sha(staged.staged_bytes) != context.changeset.entries[0].after_fingerprint:
            return self._result(operation, status="FAILED", summary="native staged bytes differ from reviewed change", before_fp=before.project_fingerprint)
        try:
            expected_pad_net, expected_non_target = expected_limited_observation(
                original, staged.staged_bytes, operation.native_uuid,
                reader_id=context.native_reader_policy.reader_id,
            )
            expected_projection = (source_projection(staged.staged_bytes, operation.native_uuid)
                                   if context.native_reader_policy.reader_id == READER_ID else None)
        except (UnicodeError, ValueError, TypeError):
            return self._result(operation, status="UNAVAILABLE", summary="finite PCB expectation unavailable", before_fp=before.project_fingerprint)
        if not self._allowed("APPLY", operation, approval, cancellation):
            return self._result(operation, status="UNAVAILABLE", summary="native Workspace apply phase unauthorized", before_fp=before.project_fingerprint)
        grant = workspace.issue_apply_approval(
            changeset=context.changeset, reviewer_id=approval.reviewer_id,
            nonce="native-" + _sha((operation.operation_id + approval.fingerprint).encode())[7:31],
            expires_at=min(operation.expires_at, approval.expires_at),
        )
        if grant.expires_at > min(operation.expires_at, approval.expires_at):
            raise ValueError("native Workspace grant exceeds operation approval")
        if not self._allowed("APPLY", operation, approval, cancellation):
            return self._result(
                operation, status="UNAVAILABLE", summary="native Workspace apply phase expired before dispatch",
                before_fp=before.project_fingerprint, grant_fp=grant.fingerprint,
            )
        try:
            receipt = workspace.apply(changeset=context.changeset, approval=grant, operation_id=operation.operation_id)
        except Exception:  # noqa: BLE001 - preserve post-apply facts on any ordinary port or I/O failure
            try:
                receipt = workspace.get_receipt(operation.project_id, operation.operation_id)
            except Exception:  # noqa: BLE001 - preserve post-apply facts on any ordinary port or I/O failure
                receipt = None
            try:
                after_fp = workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id).project_fingerprint
            except Exception:  # noqa: BLE001 - preserve post-apply facts on any ordinary port or I/O failure
                after_fp = None
            applied = receipt is not None and receipt.status.value == "APPLIED"
            return self._result(
                operation, status="FAILED", summary="native Workspace apply needs operator decision",
                before_fp=before.project_fingerprint, after_fp=after_fp,
                apply_receipt=receipt, grant_fp=grant.fingerprint,
                apply_status="APPLIED" if applied else ("UNKNOWN" if receipt is None else "FAILED"),
                recovery=RecoveryState.TARGET_STATE_UNKNOWN if receipt is None or after_fp is None else RecoveryState.OPERATOR_DECISION_REQUIRED,
            )
        try:
            try:
                after = workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id)
            except Exception:  # noqa: BLE001 - preserve post-apply facts on any ordinary port or I/O failure
                return self._result(
                    operation, status="FAILED", summary="native Workspace post-apply manifest unavailable",
                    before_fp=before.project_fingerprint, apply_receipt=receipt, grant_fp=grant.fingerprint,
                    apply_status="APPLIED" if receipt.status.value == "APPLIED" else "FAILED",
                    recovery=RecoveryState.TARGET_STATE_UNKNOWN,
                )
            if receipt.status.value != "APPLIED" or _sha(board.read_bytes()) != context.changeset.entries[0].after_fingerprint:
                return self._result(
                    operation, status="FAILED", summary="native Workspace apply hash mismatch",
                    before_fp=before.project_fingerprint, after_fp=after.project_fingerprint,
                    apply_receipt=receipt, grant_fp=grant.fingerprint,
                    apply_status="APPLIED" if receipt.status.value == "APPLIED" else "FAILED",
                    recovery=RecoveryState.TARGET_STATE_UNKNOWN,
                )
            if not _manifest_allowed(before, after, operation.pcb_relative_path, context.changeset.entries[0].after_fingerprint, context.side_effect_policy, root, before_prl):
                return self._result(
                    operation, status="FAILED", summary="native Workspace side effect exceeded reviewed policy",
                    before_fp=before.project_fingerprint, after_fp=after.project_fingerprint,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    recovery=RecoveryState.OPERATOR_DECISION_REQUIRED,
                )
            if not self._allowed("NATIVE_READBACK", operation, approval, cancellation):
                readback = NativeMoveReadBackV1(
                    status="UNAVAILABLE", reader_id=context.native_reader_policy.reader_id,
                    reader_version=context.native_reader_policy.reader_version,
                    executable_sha256=context.native_reader_policy.executable_sha256,
                )
                return self._result(
                    operation, status="UNAVAILABLE", summary="native read-back phase unauthorized",
                    before_fp=before.project_fingerprint, after_fp=after.project_fingerprint,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    readback=readback, recovery=RecoveryState.OPERATOR_DECISION_REQUIRED,
                )
            try:
                if context.native_reader_policy.reader_id == READER_ID:
                    phase = NativeReadbackPhase(
                        deadline=min(operation.expires_at, approval.expires_at),
                        policy_timeout_seconds=context.native_reader_policy.timeout_seconds,
                        clock=self._clock,
                        allowed=lambda: self._allowed("NATIVE_READBACK", operation, approval, cancellation),
                    )
                    if phase.timeout_seconds() < 1:
                        raise TimeoutError("native read-back deadline expired")
                    readback = self._setup.reader.read_back(operation, board, context.native_reader_policy, phase)
                else:
                    readback = self._setup.reader.read_back(operation, board, context.native_reader_policy)
                if not isinstance(readback, NativeMoveReadBackV1):
                    raise TypeError("native reader returned no typed observation")
                if (readback.reader_id, readback.reader_version, readback.executable_sha256) != (
                    context.native_reader_policy.reader_id, context.native_reader_policy.reader_version,
                    context.native_reader_policy.executable_sha256,
                ):
                    raise ValueError("native reader identity changed")
                if readback.status == "PASS":
                    artifact = readback.observation_artifact
                    if artifact is None or (
                        artifact.tenant_id, artifact.project_id, artifact.target_id, artifact.workspace_fingerprint,
                        artifact.build_operation_id, artifact.toolchain_id, artifact.toolchain_version,
                    ) != (
                        operation.tenant_id, operation.project_id, operation.target_id, operation.workspace_fingerprint,
                        operation.operation_id, context.native_reader_policy.reader_id,
                        context.native_reader_policy.reader_version,
                    ):
                        raise ValueError("native read-back artifact identity mismatch")
                    document = json.loads(self._artifact_bytes(artifact).decode("utf-8"))
                    expected_observation = {
                        "pcb_sha256": context.changeset.entries[0].after_fingerprint, "native_uuid": operation.native_uuid,
                        "x_mm": operation.after_x_mm, "y_mm": operation.after_y_mm,
                        "rotation_degrees": operation.rotation_degrees,
                        "pad_net_fingerprint": expected_pad_net,
                        "non_target_summary_fingerprint": expected_non_target,
                    }
                    if expected_projection is not None:
                        expected_observation.update(expected_projection)
                    if not isinstance(document, dict) or any(document.get(key) != value for key, value in expected_observation.items()):
                        raise ValueError("native read-back artifact content mismatch")
                    if (readback.pcb_sha256, readback.native_uuid, readback.x_mm, readback.y_mm, readback.rotation_degrees,
                        readback.pad_net_fingerprint, readback.non_target_summary_fingerprint) != (
                        expected_observation["pcb_sha256"], operation.native_uuid, operation.after_x_mm,
                        operation.after_y_mm, expected_observation["rotation_degrees"], expected_pad_net, expected_non_target,
                    ):
                        raise ValueError("native read-back target mismatch")
                else:
                    raise ValueError("native reader did not independently pass")
                if not self._allowed("NATIVE_READBACK", operation, approval, cancellation):
                    raise TimeoutError("native read-back result expired or unauthorized")
            except Exception:  # noqa: BLE001 - retain typed APPLIED facts on any ordinary reader failure
                readback = NativeMoveReadBackV1(
                    status="UNKNOWN", reader_id=context.native_reader_policy.reader_id,
                    reader_version=context.native_reader_policy.reader_version,
                    executable_sha256=context.native_reader_policy.executable_sha256,
                )
                recovery_state = RecoveryState.TARGET_STATE_UNKNOWN
                try:
                    observed_after = workspace.snapshot(
                        tenant_id=operation.tenant_id, project_id=operation.project_id,
                        target_id=operation.target_id,
                    )
                    observed_fp = observed_after.project_fingerprint
                    if _manifest_allowed(
                        before, observed_after, operation.pcb_relative_path,
                        context.changeset.entries[0].after_fingerprint,
                        context.side_effect_policy, root, before_prl,
                    ):
                        recovery_state = RecoveryState.OPERATOR_DECISION_REQUIRED
                except Exception:  # noqa: BLE001 - preserve APPLIED even when scanning fails
                    observed_fp = None
                return self._result(
                    operation, status="FAILED", summary="native read-back evidence unknown",
                    before_fp=before.project_fingerprint, after_fp=observed_fp,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    readback=readback, recovery=recovery_state,
                )
            after_read = workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id)
            if not _manifest_allowed(before, after_read, operation.pcb_relative_path, context.changeset.entries[0].after_fingerprint, context.side_effect_policy, root, before_prl):
                return self._result(
                    operation, status="FAILED", summary="native reader changed Workspace files",
                    before_fp=before.project_fingerprint, after_fp=after_read.project_fingerprint,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    readback=readback, recovery=RecoveryState.OPERATOR_DECISION_REQUIRED,
                )
            if not self._allowed("DRC", operation, approval, cancellation):
                drc = NativeMoveDrcResultV1(
                    status="NOT_RUN", tool_id=context.drc_policy.tool_id, tool_version=context.drc_policy.tool_version,
                    executable_sha256=context.drc_policy.executable_sha256,
                    comparison_context_fingerprint=native_drc_comparison_fingerprint(operation, after_read.project_fingerprint),
                    baseline_report_sha256=context.drc_policy.baseline_report.artifact_sha256,
                )
                return self._result(
                    operation, status="UNAVAILABLE", summary="native DRC phase unauthorized",
                    before_fp=before.project_fingerprint, after_fp=after_read.project_fingerprint,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    readback=readback, drc=drc, recovery=RecoveryState.OPERATOR_DECISION_REQUIRED,
                )
            after_capture = None
            try:
                from .native_move_drc_collector import NativeMoveDrcCollector
                if type(self._setup.drc) is NativeMoveDrcCollector:
                    phase = NativeReadbackPhase(
                        deadline=min(operation.expires_at, approval.expires_at),
                        policy_timeout_seconds=context.drc_policy.timeout_seconds,
                        clock=self._clock,
                        allowed=lambda: self._allowed("DRC", operation, approval, cancellation),
                    )
                    if phase.timeout_seconds() < 1:
                        raise TimeoutError("native DRC deadline expired")
                    claimed = self._setup.drc.compare(operation, board, context.drc_policy, phase)
                else:
                    claimed = self._setup.drc.compare(operation, board, context.drc_policy)
                if not isinstance(claimed, NativeMoveDrcResultV1) or claimed.report is None:
                    raise TypeError("native DRC returned no typed report")
                if (
                    claimed.tool_id, claimed.tool_version, claimed.executable_sha256,
                    claimed.comparison_context_fingerprint, claimed.baseline_report_sha256,
                ) != (
                    context.drc_policy.tool_id, context.drc_policy.tool_version, context.drc_policy.executable_sha256,
                    native_drc_comparison_fingerprint(operation, after_read.project_fingerprint),
                    context.drc_policy.baseline_report.artifact_sha256,
                ):
                    raise ValueError("native DRC identity changed")
                report = claimed.report
                if (report.tenant_id, report.project_id, report.target_id, report.workspace_fingerprint,
                    report.build_operation_id, report.toolchain_id, report.toolchain_version) != (
                    operation.tenant_id, operation.project_id, operation.target_id, operation.workspace_fingerprint,
                    operation.operation_id, context.drc_policy.tool_id, context.drc_policy.tool_version,
                ):
                    raise ValueError("native DRC report identity mismatch")
                after_capture = workspace.snapshot(
                    tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id,
                )
                if not _same_files(after_read, after_capture):
                    raise ValueError("native DRC changed Workspace before evidence comparison")
                baseline = json.loads(self._artifact_bytes(context.drc_policy.baseline_report).decode("utf-8"))
                observed = json.loads(self._artifact_bytes(report).decode("utf-8"))
                baseline = validate_drc_v2_context(
                    baseline, read_artifact=self._setup.artifact_store.read, policy=context.drc_policy,
                )
                observed = validate_drc_v2_context(
                    observed, read_artifact=self._setup.artifact_store.read, policy=context.drc_policy,
                )
                current_prl = prl_path.read_bytes() if prl_path.is_file() else None
                _drc_snapshot_bound(
                    baseline, before, operation.pcb_relative_path, context.side_effect_policy.prl_relative_path,
                    original, before_prl, before_prl, read_artifact=self._setup.artifact_store.read,
                    operation_id=context.drc_policy.baseline_report.build_operation_id,
                )
                _drc_snapshot_bound(
                    observed, after_capture, operation.pcb_relative_path, context.side_effect_policy.prl_relative_path,
                    board.read_bytes(), before_prl, current_prl, read_artifact=self._setup.artifact_store.read,
                    operation_id=operation.operation_id,
                )
                comparison = compare_drc_v2_contexts(
                    baseline, observed, read_artifact=self._setup.artifact_store.read, policy=context.drc_policy,
                )
                if not self._allowed("DRC", operation, approval, cancellation):
                    raise TimeoutError("native DRC phase expired after artifact reads")
                if comparison["status"] == "FAIL":
                    drc = NativeMoveDrcResultV1(
                        status="NEW_KEYS" if comparison["new_key_count"] else "SEVERITY_INCREASED",
                        report=report, tool_id=context.drc_policy.tool_id,
                        tool_version=context.drc_policy.tool_version,
                        executable_sha256=context.drc_policy.executable_sha256,
                        comparison_context_fingerprint=native_drc_comparison_fingerprint(
                            operation, after_read.project_fingerprint,
                        ),
                        baseline_report_sha256=context.drc_policy.baseline_report.artifact_sha256,
                        error_count=comparison["error_count"], warning_count=comparison["warning_count"],
                        new_key_count=comparison["new_key_count"],
                        severity_increase_count=comparison["severity_increase_count"],
                    )
                    return self._result(
                        operation, status="FAILED", summary="native DRC added or elevated an issue",
                        before_fp=before.project_fingerprint, after_fp=after_capture.project_fingerprint,
                        apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                        readback=readback, drc=drc, recovery=RecoveryState.OPERATOR_DECISION_REQUIRED,
                    )
                if comparison["status"] != "NO_NEW_KEYS":
                    raise ValueError("native DRC evidence is not comparable")
                drc = NativeMoveDrcResultV1(
                    status="NO_NEW_KEYS", report=report, tool_id=context.drc_policy.tool_id,
                    tool_version=context.drc_policy.tool_version,
                    executable_sha256=context.drc_policy.executable_sha256,
                    comparison_context_fingerprint=native_drc_comparison_fingerprint(
                        operation, after_read.project_fingerprint,
                    ),
                    baseline_report_sha256=context.drc_policy.baseline_report.artifact_sha256,
                    error_count=comparison["error_count"], warning_count=comparison["warning_count"],
                    new_key_count=0, severity_increase_count=0,
                )
            except Exception:  # noqa: BLE001 - preserve APPLIED and actual manifest on DRC evidence failures
                drc = NativeMoveDrcResultV1(
                    status="COMPARABILITY_UNKNOWN", tool_id=context.drc_policy.tool_id,
                    tool_version=context.drc_policy.tool_version,
                    executable_sha256=context.drc_policy.executable_sha256,
                    comparison_context_fingerprint=native_drc_comparison_fingerprint(operation, after_read.project_fingerprint),
                    baseline_report_sha256=context.drc_policy.baseline_report.artifact_sha256,
                )
                observed_fp = None if after_capture is None else after_capture.project_fingerprint
                accepted_readback = readback
                if observed_fp is None:
                    historical = readback.model_dump(mode="python", exclude={"fingerprint"})
                    accepted_readback = NativeMoveReadBackV1.model_validate(
                        {**historical, "status": "UNKNOWN"},
                    )
                return self._result(
                    operation, status="FAILED", summary="native DRC comparison unknown",
                    before_fp=before.project_fingerprint, after_fp=observed_fp,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    readback=accepted_readback, drc=drc,
                    recovery=(RecoveryState.TARGET_STATE_UNKNOWN if observed_fp is None
                              else RecoveryState.OPERATOR_DECISION_REQUIRED),
                )
            final = workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id)
            if not _manifest_allowed(before, final, operation.pcb_relative_path, context.changeset.entries[0].after_fingerprint, context.side_effect_policy, root, before_prl):
                return self._result(
                    operation, status="FAILED", summary="native DRC changed Workspace files",
                    before_fp=before.project_fingerprint, after_fp=final.project_fingerprint,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    readback=readback, drc=drc, recovery=RecoveryState.OPERATOR_DECISION_REQUIRED,
                )
            if not self._allowed("DRC", operation, approval, cancellation):
                return self._result(
                    operation, status="FAILED", summary="native DRC phase expired before result adoption",
                    before_fp=before.project_fingerprint, after_fp=final.project_fingerprint,
                    apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                    readback=readback, recovery=RecoveryState.OPERATOR_DECISION_REQUIRED,
                )
            return self._result(
                operation, status="SUCCEEDED", summary="native move applied with independent limited comparison",
                before_fp=before.project_fingerprint, after_fp=final.project_fingerprint,
                apply_receipt=receipt, grant_fp=grant.fingerprint, apply_status="APPLIED",
                readback=readback, drc=drc,
            )
        except Exception:  # noqa: BLE001 - preserve post-apply facts on any ordinary port or I/O failure
            return self._result(
                operation, status="FAILED", summary="native post-apply evidence unavailable",
                before_fp=before.project_fingerprint, apply_receipt=receipt,
                grant_fp=grant.fingerprint,
                apply_status="APPLIED" if getattr(getattr(receipt, "status", None), "value", None) == "APPLIED" else "UNKNOWN",
                recovery=RecoveryState.TARGET_STATE_UNKNOWN,
            )
