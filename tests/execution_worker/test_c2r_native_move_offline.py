"""C2-R fake-port verification over isolated TEMP Workspace; no EDA process or discovery."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from embedded_copilot.engineering_workspace_apply import (
    FixedWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker.adapters import SafeProcessRunner
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    NativeMoveDrcPolicy,
    NativeMoveDrcResultV1,
    NativeMoveReadBackV1,
    NativeMoveReaderPolicy,
    NativeMoveSideEffectPolicy,
    OperationStatus,
    OperationType,
    RecoveryState,
)
from embedded_copilot.execution_worker.native_drc_provenance import (
    NORMALIZER_ID,
    NORMALIZER_VERSION,
    build_drc_v2_context,
)
from embedded_copilot.execution_worker.native_move import (
    NativeMoveProductionAdapter,
    NativeMoveProductionSetup,
    _drc_snapshot_bound,
    _prl_change_allowed,
    native_drc_comparison_fingerprint,
)
from embedded_copilot.execution_worker.native_move_pcbnew_reader import (
    NativePcbnewReader,
    NativeReadbackBundleV1,
)
from embedded_copilot.execution_worker.native_move_projection import (
    READER_ID,
    load_native_projection,
    native_projection,
    source_projection,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.pcb.parser.kicad import (
    _atom,
    _children,
    _head,
    _parse_s_expression,
)
from embedded_copilot.pcb.parser.models import PCBParserLimits
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)

NOW = datetime(2026, 9, 30, 8, tzinfo=UTC)
UUID = "01234567-89ab-4cde-8f01-23456789abcd"
BOARD = (
    b'(kicad_pcb (version 20240108) (net 1 "GND") '
    b'(footprint "Library:Part" (uuid 01234567-89ab-4cde-8f01-23456789abcd) '
    b'(at 10 20 90) (property "Reference" "U1") '
    b'(pad "1" smd rect (at 0 0) (net 1 "GND"))))'
)


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Clock:
    def now(self):
        return NOW + timedelta(minutes=1)


def identity(blob: bytes, *, artifact_id: str, project_id: str, workspace_fp: str,
             operation_id: str, tool_id: str, tool_version: str, kind: str = "LOG") -> ArtifactIdentity:
    return ArtifactIdentity(
        artifact_id=artifact_id, tenant_id="tenant-1", project_id=project_id,
        target_id="project-root", workspace_fingerprint=workspace_fp,
        build_operation_id=operation_id, toolchain_id=tool_id, toolchain_version=tool_version,
        build_configuration="offline", artifact_type=kind, artifact_sha256=sha(blob),
        size_bytes=len(blob), created_at=NOW,
    )


def independent_summaries(payload: bytes, native_uuid: str) -> tuple[str, str]:
    """Fake native-port output is recomputed from the applied path, independently of adapter helper."""
    root = _parse_s_expression(payload.decode("utf-8"), PCBParserLimits())
    pads = []
    others = []
    for node in root[1:]:
        if not isinstance(node, tuple) or _head(node) != "footprint":
            others.append(node)
            continue
        ids = _children(node, "uuid")
        key = _atom(ids[0], 1) if len(ids) == 1 else None
        pads.append((key, _children(node, "pad")))
        if key == native_uuid:
            at = _children(node, "at")[0]
            others.append(tuple(child for child in node if child is not at))
        else:
            others.append(node)
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha(canonical((_children(root, "net"), pads))), sha(canonical(others))


def report(policy: NativeMoveDrcPolicy, prl_sha: str | None = None, *,
           violations: list[dict] | None = None, **updates) -> bytes:
    value = {
        "schema_version": 1, "tool_id": policy.tool_id, "tool_version": policy.tool_version,
        "executable_sha256": policy.executable_sha256,
        "rules_fingerprint": sha(b"rules"), "dependency_fingerprint": sha(b"libraries"),
        "zone_fill_policy": "NO_REFILL", "schematic_parity": "NOT_RUN",
        "included_severities": ["error", "warning"], "ignored_checks": [],
        "prl_sha256": prl_sha,
        "violations": [{"key": "existing", "severity": "error"}] if violations is None else violations,
    }
    value.update(updates)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def capture_drc_v2(artifacts, policy, snapshot, operation_id, board: bytes, *, updates=None):
    """Synthetic ArtifactStore evidence only; no KiCad collector is claimed."""
    updates = updates or {}
    workspace_fp = snapshot.project_fingerprint
    project_id = snapshot.project_id
    def put(blob, role, kind="LOG"):
        base = identity(
            blob, artifact_id=operation_id + "-" + role, project_id=project_id,
            workspace_fp=workspace_fp, operation_id=operation_id,
            tool_id=policy.tool_id, tool_version=policy.tool_version, kind=kind,
        )
        item = base
        artifacts.put(item, blob)
        return item
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    entry = lambda role, path, data: {
        "role": role, "relative_path": path, "sha256": sha(data),
        "byte_size": len(data), "source": "offline-canonical-snapshot",
    }
    manifest = lambda entries: encode({"schema_version": 1, "entries": sorted(entries, key=lambda item: (item["role"], item["relative_path"]))})
    bundle = put(manifest([entry("EXECUTABLE", "bin/kicad-cli", b"fake-drc")]), "bundle", "MANIFEST")
    rules = put(manifest([entry("RULE", "project/rules", b"changed" if "rules_fingerprint" in updates else b"rules")]), "rules", "MANIFEST")
    deps = put(manifest([entry("DEPENDENCY", "lib/footprints", b"changed" if "dependency_fingerprint" in updates else b"library")]), "deps", "MANIFEST")
    env = put(encode({"schema_version": 1, "entries": [{"name": "PATH", "value": "offline-fixed"}]}), "env")
    pcb = put(board, "pcb")
    files = []
    for file in snapshot.files:
        if file.exists:
            files.append({"role": "FILE", "relative_path": file.relative_file_reference,
                          "sha256": file.content_fingerprint, "byte_size": file.size,
                          "source": "offline-canonical-snapshot"})
    workspace = put(manifest(files), "workspace", "MANIFEST")
    raw = {
        "$schema": "https://schemas.kicad.org/drc.v1.json", "coordinate_units": "mm",
        "date": "2026-10-07T00:00:00", "ignored_checks": [],
        "included_severities": ["error", "warning"], "kicad_version": "10.0.5",
        "schematic_parity": [], "source": "layout.kicad_pcb", "unconnected_items": [],
        "violations": [{"description": "existing", "type": "clearance", "severity": "error",
                        "items": [{"description": "U1", "pos": {"x": 1.0, "y": 2.0}, "uuid": UUID}]}],
    }
    if "unexpected" in updates:
        raw["unexpected"] = updates["unexpected"]
    if "violations" in updates:
        values = updates["violations"]
        raw["violations"] = [
            {"description": item["key"], "type": "clearance", "severity": item["severity"],
             "items": [{"description": "U1", "pos": {"x": 1.0, "y": 2.0}, "uuid": UUID}]}
            for item in values
        ]
    raw_ref = put(encode(raw), "raw")
    provenance = {
        "schema_version": 2, "collector_id": "OFFLINE_TEST_COLLECTOR", "collector_version": "1",
        "tool_id": policy.tool_id, "tool_version": policy.tool_version,
        "executable_sha256": policy.executable_sha256,
        "normalizer_id": NORMALIZER_ID, "normalizer_version": NORMALIZER_VERSION,
        "tool_bundle": bundle.model_dump(mode="json"),
        "argv_template": ["pcb", "drc", "--format", "json", "--output", "{report}", "{pcb}"],
        "argv_slots": {
            "pcb": {"relative_path": "native/layout.kicad_pcb", "artifact": pcb.model_dump(mode="json")},
            "report": {"role": "DRC_REPORT", "relative_path": "evidence/" + operation_id + ".json",
                       "artifact": raw_ref.model_dump(mode="json")},
        },
        "environment": env.model_dump(mode="json"), "input_pcb": pcb.model_dump(mode="json"),
        "workspace_manifest": workspace.model_dump(mode="json"),
        "rules_manifest": rules.model_dump(mode="json"),
        "dependency_manifest": deps.model_dump(mode="json"),
        "prl_relative_path": "native/layout.kicad_prl", "prl_before": None, "prl_after": None,
        "raw_sha256": raw_ref.artifact_sha256, "scope": "BASELINE_COMPARISON_ONLY",
        "policy": {"zone_fill_policy": "NO_REFILL", "schematic_parity": "NOT_RUN",
                   "included_severities": ["error", "warning"], "ignored_checks": []},
    }
    provenance_ref = put(encode(provenance), "provenance")
    context = build_drc_v2_context(
        raw_ref, provenance_ref, read_artifact=artifacts.read, policy=policy,
    )
    return put(encode(context), "context")


class FakeReader:
    def __init__(self, artifacts: ArtifactStore, project_id: str):
        self.artifacts, self.project_id = artifacts, project_id
        self.calls = 0
        self.bad_hash = False
        self.mutate_board = False
        self.wrong_artifact_tool = False

    def read_back(self, operation, board: Path, policy):
        self.calls += 1
        payload = board.read_bytes()
        if self.mutate_board:
            board.write_bytes(payload + b" ")
            payload = board.read_bytes()
        pad, other = independent_summaries(payload, operation.native_uuid)
        if self.bad_hash:
            pad = sha(b"false-pad-net")
        document = {
            "pcb_sha256": sha(payload), "native_uuid": operation.native_uuid,
            "x_mm": operation.after_x_mm, "y_mm": operation.after_y_mm,
            "rotation_degrees": operation.rotation_degrees,
            "pad_net_fingerprint": pad, "non_target_summary_fingerprint": other,
        }
        blob = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        artifact = identity(
            blob, artifact_id="readback-" + operation.operation_id,
            project_id=self.project_id, workspace_fp=operation.workspace_fingerprint,
            operation_id=operation.operation_id,
            tool_id="wrong-reader" if self.wrong_artifact_tool else policy.reader_id,
            tool_version=policy.reader_version,
        )
        self.artifacts.put(artifact, blob)
        self.last_artifact = artifact
        return NativeMoveReadBackV1(
            status="PASS", reader_id=policy.reader_id, reader_version=policy.reader_version,
            executable_sha256=policy.executable_sha256, pcb_sha256=sha(payload),
            native_uuid=operation.native_uuid, x_mm=operation.after_x_mm,
            y_mm=operation.after_y_mm, rotation_degrees=operation.rotation_degrees,
            pad_net_fingerprint=pad, non_target_summary_fingerprint=other,
            observation_artifact=artifact,
        )


class FakeDrc:
    def __init__(self, artifacts: ArtifactStore, project_id: str):
        self.artifacts, self.project_id = artifacts, project_id
        self.calls = 0
        self.updates = {}
        self.mutate_board = False
        self.claimed_error_count = 1
        self.omit_notes = False

    def compare(self, operation, board: Path, policy):
        self.calls += 1
        # The fake has no EDA capability; it saves typed raw evidence for adapter checks.
        current = self.workspace.snapshot(
            tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id,
        )
        claimed_snapshot = (current.model_copy(update={
            "files": tuple(item for item in current.files if item.relative_file_reference != "notes.txt"),
        }) if self.omit_notes else current)
        captured = capture_drc_v2(
            self.artifacts, policy, claimed_snapshot, operation.operation_id, board.read_bytes(),
            updates=self.updates,
        )
        envelope = self.artifacts.read(captured)
        artifact = identity(
            envelope, artifact_id=operation.operation_id + "-report", project_id=self.project_id,
            workspace_fp=operation.workspace_fingerprint, operation_id=operation.operation_id,
            tool_id=policy.tool_id, tool_version=policy.tool_version,
        )
        self.artifacts.put(artifact, envelope)
        self.last_artifact = artifact
        if self.mutate_board:
            board.write_bytes(board.read_bytes() + b" ")
        return NativeMoveDrcResultV1(
            status="NO_NEW_KEYS", report=artifact, tool_id=policy.tool_id,
            tool_version=policy.tool_version, executable_sha256=policy.executable_sha256,
            comparison_context_fingerprint=native_drc_comparison_fingerprint(operation, current.project_fingerprint),
            baseline_report_sha256=policy.baseline_report.artifact_sha256,
            error_count=self.claimed_error_count, warning_count=0,
            new_key_count=0, severity_increase_count=0,
        )


def fake_native_bundle(tmp_path: Path) -> NativeReadbackBundleV1:
    root = tmp_path / "closed-native-bundle"
    (root / "Lib" / "site-packages").mkdir(parents=True)
    for name, blob in {
        "python.exe": b"offline fake interpreter",
        "python311.dll": b"offline fake startup DLL",
        "python311.zip": b"offline fake stdlib zip",
        "python311._pth": b"python311.zip\nLib\nLib/site-packages\n.\n",
        "Lib/os.py": b"offline fake stdlib file",
        "pcbnew.py": b"offline fake pcbnew module",
        "_pcbnew.pyd": b"offline fake native extension",
    }.items():
        (root / name).write_bytes(blob)
    reviewed = Path(__file__).parents[2] / "src" / "embedded_copilot" / "execution_worker"
    for name in ("native_move_wrapper.py", "native_move_native_core.py"):
        (root / name).write_bytes((reviewed / name).read_bytes())
    return NativeReadbackBundleV1.freeze(root, {"PATH": str(root)})


def harness(tmp_path: Path, *, board_bytes=BOARD, reader_factory=FakeReader, reader_id="fake-reader", reader_ready=True, separate_workspace=False, legacy_baseline=False, baseline_omit_notes=False):
    root = tmp_path / "projects"
    root.mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite3",
        clock=Clock(), initialize_async=False,
    )
    preview = catalog.preview(
        ProjectCreationRequest(name="Board", relative_path="board", project_type=ProjectType.STM32),
        tenant_id="tenant-1", requester="owner-1",
    )
    project = catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=Clock().now(),
        tenant_id="tenant-1", requester="owner-1",
    )
    project_root = root / "board"
    native = project_root / "native"
    native.mkdir()
    board = native / "layout.kicad_pcb"
    board.write_bytes(board_bytes)
    (project_root / "notes.txt").write_text("outside C1 source set", encoding="utf-8")
    source_board = board
    if separate_workspace:
        workspace_root = tmp_path / "workspace-board"
        (workspace_root / "native").mkdir(parents=True)
        board = workspace_root / "native" / "layout.kicad_pcb"
        board.write_bytes(board_bytes)
        (workspace_root / "notes.txt").write_text("outside C1 source set", encoding="utf-8")
    else:
        workspace_root = project_root
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(workspace_root, tenant_id="tenant-1", project_id=project.project_id),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state", clock=Clock(),
    )
    catalog.set_workspace_snapshot_source(workspace)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    snapshot = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    dummy = identity(
        b"{}", artifact_id="baseline-" + project.project_id, project_id=project.project_id,
        workspace_fp=snapshot.project_fingerprint, operation_id="baseline-1",
        tool_id="kicad-cli", tool_version="10.0.5",
    )
    policy = NativeMoveDrcPolicy(
        tool_id="kicad-cli", tool_version="10.0.5", executable_sha256=sha(b"fake-drc"),
        timeout_seconds=30, baseline_report=dummy, ignored_checks=(),
    )
    if legacy_baseline:
        baseline_blob = report(policy)
        baseline_identity = identity(
            baseline_blob, artifact_id="baseline-" + project.project_id,
            project_id=project.project_id, workspace_fp=snapshot.project_fingerprint,
            operation_id="baseline-1", tool_id=policy.tool_id, tool_version=policy.tool_version,
        )
        artifacts.put(baseline_identity, baseline_blob)
    else:
        baseline_snapshot = (snapshot.model_copy(update={
            "files": tuple(item for item in snapshot.files if item.relative_file_reference != "notes.txt"),
        }) if baseline_omit_notes else snapshot)
        baseline_identity = capture_drc_v2(
            artifacts, policy, baseline_snapshot, "baseline-1", board.read_bytes(),
        )
    policy = policy.model_copy(update={"baseline_report": baseline_identity})
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    drc = FakeDrc(artifacts, project.project_id)
    drc.workspace = workspace
    phases = {"APPLY", "NATIVE_READBACK", "DRC"}
    phase_authorizer = lambda phase, *_: phase in phases
    if reader_id == READER_ID:
        bundle = fake_native_bundle(tmp_path)
        reader = NativePcbnewReader(
            bundle=bundle, runner=SafeProcessRunner(), store=store, catalog=catalog,
            workspace=workspace, artifact_store=artifacts,
            authorize_phase=phase_authorizer, clock=Clock().now,
        )
        reader.api = FakeNativeApi(FakeNativeBoard())
        reader.calls = 0
        reader.runner.calls = []
        original_read = reader.read_back
        def counted_read(*args):
            reader.calls += 1
            return original_read(*args)
        reader.read_back = counted_read
        if not reader_ready:
            (bundle.root / "python311.zip").write_bytes(b"drifted fake stdlib")
        reader_policy = NativeMoveReaderPolicy(
            reader_id=READER_ID, reader_version=bundle.reader_version,
            executable_sha256=next(item.sha256 for item in bundle.files if item.role == "INTERPRETER"),
            timeout_seconds=30,
        )
    else:
        reader = reader_factory(artifacts, project.project_id)
        reader_policy = NativeMoveReaderPolicy(
            reader_id=reader_id, reader_version="v1", executable_sha256=sha(b"fake-reader"),
            timeout_seconds=30,
        )
    setup = NativeMoveProductionSetup(
        workspace=workspace, reader_policy=reader_policy,
        drc_policy=policy,
        side_effect_policy=NativeMoveSideEffectPolicy(
            prl_relative_path="native/layout.kicad_prl", prl_before_sha256=None,
            prl_policy="REJECT",
        ),
        artifact_store=artifacts, validate_policies=lambda *_: True,
        authorize_phase=phase_authorizer,
        reader=reader, drc=drc,
    )
    catalog.set_native_move_production_setup(setup)
    c1 = catalog.create_eda_import_preview(
        project.project_id, tenant_id="tenant-1", requester="owner-1", backend="KICAD",
        native_entry_relative_path="native/layout.kicad_pcb", selected_native_entries=(),
        expected_head_revision_id=None, expected_head_revision_fingerprint=None,
    )
    revision = catalog.commit_eda_import_preview(
        project.project_id, c1["preview_id"], c1["preview_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    candidate = catalog.create_native_move_candidate(
        project.project_id, tenant_id="tenant-1", requester="owner-1",
        import_revision_id=revision["import_revision_id"],
        import_revision_fingerprint=revision["import_revision_fingerprint"],
        workspace_target_id="project-root", target_native_uuid=UUID,
        after_x_mm=11.0, after_y_mm=20.0,
    )
    review = catalog.review_native_move_candidate(
        project.project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        tenant_id="tenant-1", requester="owner-1", decision="ACCEPT_MAPPING",
        design_intent="Move U1 east by one millimetre.",
    )
    execution = ExecutionOperationService(
        store=store, clock=Clock().now, artifact_store=artifacts,
        native_move_authorizer=catalog.revalidate_native_move_operation,
    )
    status = catalog.create_native_move_operation(
        project.project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        review["mapping_review_id"], review["mapping_review_fingerprint"],
        tenant_id="tenant-1", requester="owner-1", execution=execution,
    )
    operation = execution.get_operation(
        tenant_id="tenant-1", project_id=project.project_id, operation_id=status.operation_id,
    )
    assert operation.production_context is not None
    return locals()


def fake_native_popen(h, operation):
    reader = h["reader"]
    def fake_popen(argv, **kwargs):
        assert argv == [
            str(reader.bundle.interpreter), "-I", "-S", str(reader.bundle.wrapper),
            str(h["board"]), operation.native_uuid,
        ]
        assert kwargs["shell"] is False and kwargs["env"] == {"PATH": str(reader.bundle.root)}
        reader.runner.calls.append(tuple(argv))
        projection = load_native_projection(reader.api, argv[4], argv[5])
        data = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
        class FakeProcess:
            stdout = io.BytesIO(data)
            stderr = io.BytesIO()
            returncode = 0
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0
            def terminate(self):
                self.returncode = -1
            def kill(self):
                self.returncode = -9
        return FakeProcess()
    return fake_popen


def run(h, *, phases=None, adapter_clock=None):
    operation = h["operation"]
    execution = h["execution"]
    if phases is not None:
        h["phases"].clear()
        h["phases"].update(phases)
    approval = execution.issue_approval_for_id(
        tenant_id=operation.tenant_id, project_id=operation.project_id,
        operation_id=operation.operation_id, reviewer_id="owner-1",
        nonce="c2r-approval", expires_at=NOW + timedelta(minutes=4),
    )
    execution.enqueue_by_id(
        tenant_id=operation.tenant_id, project_id=operation.project_id,
        operation_id=operation.operation_id, approval_id=approval.grant_id,
    )
    worker = ExecutionWorker(
        worker_id="fake-c2r-worker", store=h["store"], registry=None,
        workspace_resolver=None, targets=None, artifact_store=h["artifacts"],
        native_move_adapter=NativeMoveProductionAdapter(h["setup"], clock=adapter_clock or Clock().now),
        native_move_revalidator=h["catalog"].revalidate_native_move_operation,
        recovery_workspace=h["workspace"],
        allowed_operation_types=(OperationType.KICAD_NATIVE_FOOTPRINT_MOVE,),
        clock=Clock().now,
    )
    reader = h["reader"]
    if isinstance(reader, NativePcbnewReader):
        with patch("embedded_copilot.execution_worker.adapters.subprocess.Popen", h.get("fake_popen", fake_native_popen(h, operation))):
            return worker.run_once()
    return worker.run_once()


def test_production_candidate_review_retry_and_fake_three_phase_success(tmp_path: Path):
    h = harness(tmp_path)
    candidate, operation = h["candidate"], h["operation"]
    assert candidate["binding"]["preservation_status"] == "BYTE_SPAN_PROVEN"
    assert candidate["binding"]["can_request_operation_approval"] is False
    projected = h["catalog"].native_move_candidate(
        operation.project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        tenant_id="tenant-1", requester="owner-1",
    )
    assert projected["can_request_operation_approval"] is True
    class LaterClock:
        def now(self):
            return NOW + timedelta(minutes=2)
    h["catalog"]._clock = LaterClock()
    retry = h["catalog"].create_native_move_operation(
        operation.project_id, candidate["candidate_id"], candidate["candidate_fingerprint"],
        h["review"]["mapping_review_id"], h["review"]["mapping_review_fingerprint"],
        tenant_id="tenant-1", requester="owner-1", execution=h["execution"],
    )
    assert retry.operation_id == operation.operation_id
    assert h["workspace"].get_changeset(
        operation.tenant_id, operation.project_id, operation.production_context.changeset.changeset_id,
    ) == operation.production_context.changeset
    status = run(h)
    assert status.status is OperationStatus.SUCCEEDED
    assert h["board"].read_bytes() == operation.production_context.changeset.entries[0].after_content.encode()
    receipt = h["store"].get_receipt(operation.tenant_id, operation.project_id, operation.operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "PASS"
    assert receipt.native_move_production.drc_result.status == "NO_NEW_KEYS"
    assert receipt.native_move_production.drc_board_pass is False
    assert (h["reader"].calls, h["drc"].calls) == (1, 1)


@pytest.mark.parametrize("phases,expected_calls", [
    (set(), (0, 0)),
    ({"APPLY"}, (0, 0)),
    ({"APPLY", "NATIVE_READBACK"}, (1, 0)),
])
def test_each_phase_requires_separate_authority(tmp_path: Path, phases, expected_calls):
    h = harness(tmp_path)
    status = run(h, phases=phases)
    assert status.status in {OperationStatus.FAILED, OperationStatus.RECOVERY_REQUIRED}
    assert (h["reader"].calls, h["drc"].calls) == expected_calls
    if "APPLY" in phases:
        receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
        assert receipt.native_move_production.workspace_apply_status == "APPLIED"


def test_readback_self_echo_cannot_pass(tmp_path: Path):
    h = harness(tmp_path)
    h["reader"].bad_hash = True
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert h["drc"].calls == 0


@pytest.mark.parametrize("updates", [
    {"rules_fingerprint": sha(b"changed")},
    {"dependency_fingerprint": sha(b"changed")},
    {"unexpected": "field"},
    {"violations": [{"key": "existing", "severity": "warning"}]},
])
def test_drc_comparability_rejects_changed_raw_context(tmp_path: Path, updates):
    h = harness(tmp_path)
    h["drc"].updates = updates
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.drc_result.status == "COMPARABILITY_UNKNOWN"


@pytest.mark.parametrize("old,new", [
    ({}, {"board": {"active_layer": 1, "new_key": True}}),
    ({}, {"project": {"files": ["C:/secret"]}}),
    ({}, {"open_jobsets": ["C:/secret"]}),
    ({"board": {"unknown": True}}, {"board": {"unknown": True, "active_layer": 1}}),
    ({}, {"board": {"auto_track_width": "true"}}),
])
def test_prl_policy_rejects_unknown_or_sensitive_values(old, new):
    policy = NativeMoveSideEffectPolicy(
        prl_relative_path="native/layout.kicad_prl", prl_before_sha256=sha(json.dumps(old).encode()),
        prl_policy="ALLOW_REVIEWED_KEYS", allowed_root_keys=("board", "project", "open_jobsets"),
    )
    assert not _prl_change_allowed(json.dumps(old).encode(), json.dumps(new).encode(), policy)


def test_workspace_grant_is_bounded_by_short_operation_approval(tmp_path: Path):
    h = harness(tmp_path)
    operation = h["operation"]
    execution = h["execution"]
    short = NOW + timedelta(minutes=2)
    approval = execution.issue_approval_for_id(
        tenant_id=operation.tenant_id, project_id=operation.project_id,
        operation_id=operation.operation_id, reviewer_id="owner-1",
        nonce="short-c2r-approval", expires_at=short,
    )
    execution.enqueue_by_id(
        tenant_id=operation.tenant_id, project_id=operation.project_id,
        operation_id=operation.operation_id, approval_id=approval.grant_id,
    )
    worker = ExecutionWorker(
        worker_id="short-c2r", store=h["store"], registry=None,
        workspace_resolver=None, targets=None, artifact_store=h["artifacts"],
        native_move_adapter=NativeMoveProductionAdapter(h["setup"], clock=Clock().now),
        native_move_revalidator=h["catalog"].revalidate_native_move_operation,
        allowed_operation_types=(OperationType.KICAD_NATIVE_FOOTPRINT_MOVE,),
        clock=Clock().now,
    )
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    with h["workspace"]._store._connect() as connection:
        rows = connection.execute("SELECT body FROM v65_approvals").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0][0])["expires_at"] == short.isoformat().replace("+00:00", "Z")


def test_apply_rechecks_authority_after_grant_issuance(tmp_path: Path, monkeypatch):
    h = harness(tmp_path)
    original = h["workspace"].issue_apply_approval
    def issue_then_deny(**kwargs):
        grant = original(**kwargs)
        h["phases"].remove("APPLY")
        return grant
    monkeypatch.setattr(h["workspace"], "issue_apply_approval", issue_then_deny)
    status = run(h)
    assert status.status is OperationStatus.FAILED
    assert h["board"].read_bytes() == BOARD
    assert h["workspace"].get_receipt(h["project"].project_id, h["operation"].operation_id) is None


@pytest.mark.parametrize("drift", ["none", "extra", "prl", "missing"])
def test_recovery_in_fresh_process_without_native_reader_or_drc(tmp_path: Path, drift: str, native_v2: bool = False):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID) if native_v2 else harness(tmp_path)
    operation, execution, store, workspace = (
        h["operation"], h["execution"], h["store"], h["workspace"],
    )
    approval = execution.issue_approval_for_id(
        tenant_id=operation.tenant_id, project_id=operation.project_id,
        operation_id=operation.operation_id, reviewer_id="owner-1",
        nonce="crash-before-result", expires_at=NOW + timedelta(minutes=5),
    )
    execution.enqueue_by_id(
        tenant_id=operation.tenant_id, project_id=operation.project_id,
        operation_id=operation.operation_id, approval_id=approval.grant_id,
    )
    assert store.claim_next(
        worker_id="old-process", lease_seconds=60, now=Clock().now(),
        operation_types=(OperationType.KICAD_NATIVE_FOOTPRINT_MOVE,),
    ) == (operation.tenant_id, operation.project_id, operation.operation_id)
    running = store.start_operation_transition(
        operation, approval_id=approval.grant_id, worker_id="old-process", now=Clock().now(),
    )
    assert running is not None and running.status is OperationStatus.RUNNING
    consumed = store.get_approval(operation.tenant_id, operation.project_id, approval.grant_id)
    assert consumed.status.value == "CONSUMED"
    grant = workspace.issue_apply_approval(
        changeset=operation.production_context.changeset,
        reviewer_id=approval.reviewer_id,
        nonce="crash-grant",
        expires_at=min(operation.expires_at, consumed.expires_at),
    )
    applied = workspace.apply(
        changeset=operation.production_context.changeset,
        approval=grant, operation_id=operation.operation_id,
    )
    assert applied.status.value == "APPLIED"
    if drift == "extra":
        (tmp_path / "projects" / "board" / "unexpected.txt").write_text("unexpected")
    elif drift == "prl":
        (tmp_path / "projects" / "board" / "native" / "layout.kicad_prl").write_text("{}")
    elif drift == "missing":
        other = next(
            item.relative_file_reference for item in operation.production_context.workspace_snapshot.files
            if item.relative_file_reference != operation.pcb_relative_path
        )
        workspace._safe_path(tmp_path / "projects" / "board", other).unlink()
    child = r"""
import json, sys, subprocess
class NoToolPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        raise AssertionError("recovery must never launch a tool")
subprocess.Popen = NoToolPopen
from datetime import UTC, datetime, timedelta
from pathlib import Path
from embedded_copilot.engineering_workspace_apply import FixedWorkspaceTargetResolver, SQLiteWorkspaceApplyStore, WorkspaceApplyService
from embedded_copilot.execution_worker.contracts import OperationType
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
base, project_id, operation_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
class Clock:
    def now(self):
        return datetime(2026, 9, 30, 10, tzinfo=UTC)
workspace = WorkspaceApplyService(
    resolver=FixedWorkspaceTargetResolver(base / "projects" / "board", tenant_id="tenant-1", project_id=project_id),
    store=SQLiteWorkspaceApplyStore(base / "workspace.sqlite3"),
    state_root=base / "workspace-state", clock=Clock(),
)
store = SQLiteExecutionStore(base / "execution.sqlite3")
worker = ExecutionWorker(
    worker_id="restarted-no-native-ports", store=store, registry=None,
    workspace_resolver=None, targets=None, native_move_adapter=None,
    recovery_workspace=workspace, allowed_operation_types=(OperationType.BUILD,), clock=Clock().now,
)
statuses = worker.recover_after_restart()
assert store.get_operation("tenant-1", project_id, operation_id).production_context.native_reader_policy.reader_version == sys.argv[4]
status = store.get_status("tenant-1", project_id, operation_id)
assert store.get_approval("tenant-1", project_id, status.approval_id).fingerprint == sys.argv[5]
print(json.dumps({
    "count": len(statuses), "status": status.status.value,
    "recovery": status.recovery_state.value,
    "receipt": store.get_receipt("tenant-1", project_id, operation_id) is not None,
    "approval": store.get_approval("tenant-1", project_id, status.approval_id).status.value,
}))
"""
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(repo / "src"))
    completed = subprocess.run(
        [sys.executable, "-B", "-c", child, str(tmp_path), operation.project_id, operation.operation_id,
         operation.production_context.native_reader_policy.reader_version, consumed.fingerprint],
        cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result == {
        "count": 1, "status": "RECOVERY_REQUIRED",
        "recovery": "OPERATOR_DECISION_REQUIRED" if drift == "none" else "TARGET_STATE_UNKNOWN",
        "receipt": False, "approval": "CONSUMED",
    }
    assert h["board"].read_bytes() == operation.production_context.changeset.entries[0].after_content.encode()


@pytest.mark.parametrize("v2", [False, True])
def test_fake_testclient_wait_default_503_and_typed_receipt(tmp_path: Path, v2: bool):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from embedded_copilot.api.copilot_execution_routes import router as execution_router
    from embedded_copilot.api.project_catalog_routes import router as catalog_router

    if v2:
        h = harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader, reader_id=READER_ID)
    else:
        h = harness(tmp_path)
    operation, candidate, review = h["operation"], h["candidate"], h["review"]
    app = FastAPI()
    app.state.project_catalog_runtime = h["catalog"]
    app.state.execution_operation_port = h["execution"]
    app.state.native_move_production_available = False
    app.include_router(catalog_router)
    app.include_router(execution_router)
    headers = {"X-Copilot-Requester": "owner-1", "X-Environment-Tenant": "tenant-1"}
    operation_path = (
        f"/api/v1/copilot/projects/{operation.project_id}/eda-native/"
        f"footprint-move-candidates/{candidate['candidate_id']}/operations"
    )
    approval_path = f"/api/v1/copilot/operations/{operation.operation_id}/approval?project_id={operation.project_id}"
    approval_body = {
        "reviewer_id": "owner-1", "nonce": "api-fake-approval",
        "expires_at": (NOW + timedelta(minutes=4)).isoformat(),
    }
    with TestClient(app) as client:
        waiting = client.post(operation_path, headers=headers, json={
            "candidate_fingerprint": candidate["candidate_fingerprint"],
            "mapping_review_id": review["mapping_review_id"],
            "mapping_review_fingerprint": review["mapping_review_fingerprint"],
        })
        denied = client.post(approval_path, headers=headers, json=approval_body)
        assert waiting.status_code == 201 and waiting.json()["status"] == "WAIT_APPROVAL"
        assert denied.status_code == 503 and denied.json()["error"] == "NATIVE_MOVE_UNAVAILABLE"
        assert h["store"].get_status("tenant-1", operation.project_id, operation.operation_id).status is OperationStatus.WAIT_APPROVAL
        app.state.native_move_production_available = True
        approved = client.post(approval_path, headers=headers, json=approval_body)
        assert approved.status_code == 200
        worker = ExecutionWorker(
            worker_id="api-fake-c2r", store=h["store"], registry=None,
            workspace_resolver=None, targets=None, artifact_store=h["artifacts"],
            native_move_adapter=NativeMoveProductionAdapter(h["setup"], clock=Clock().now),
            native_move_revalidator=h["catalog"].revalidate_native_move_operation,
            allowed_operation_types=(OperationType.KICAD_NATIVE_FOOTPRINT_MOVE,),
            clock=Clock().now,
        )
        if v2:
            with patch("embedded_copilot.execution_worker.adapters.subprocess.Popen", h.get("fake_popen", fake_native_popen(h, operation))):
                result = worker.run_once()
        else:
            result = worker.run_once()
        assert result.status is OperationStatus.SUCCEEDED
        receipt = client.get(
            f"/api/v1/copilot/operations/{operation.operation_id}/receipt?project_id={operation.project_id}",
            headers=headers,
        )
        assert receipt.status_code == 200
        result = receipt.json()["native_move_production"]
        assert result["workspace_apply_status"] == "APPLIED"
        assert result["native_read_back"]["status"] == "PASS"
        assert result["drc_result"]["status"] == "NO_NEW_KEYS"
        assert result["drc_board_pass"] is False


def test_active_c1_board_is_reserved_from_generic_pcb_write(tmp_path: Path):
    from embedded_copilot.execution_worker.contracts import PCBWriteOperation
    from embedded_copilot.execution_worker.service import ExecutionRejected

    h = harness(tmp_path)
    repository = h["catalog"]._repository
    project_id = h["project"].project_id
    assert repository.has_imported_pcb_target(
        project_id, "native/layout.kicad_pcb", tenant_id="tenant-1", requester="owner-1",
    )
    assert not repository.has_imported_pcb_target(
        project_id, "hardware/generated.kicad_pcb", tenant_id="tenant-1", requester="owner-1",
    )
    h["execution"].set_pcb_write_authorizer(h["catalog"].generic_pcb_write_target_allowed)
    reserved = PCBWriteOperation.model_construct(
        project_id=project_id, tenant_id="tenant-1", requester_id="owner-1",
        target_relative_path="native/layout.kicad_pcb",
    )
    with pytest.raises(ExecutionRejected, match="reserved by C1"):
        h["execution"]._require_pcb_write_target_allowed(reserved)
    generated = PCBWriteOperation.model_construct(
        project_id=project_id, tenant_id="tenant-1", requester_id="owner-1",
        target_relative_path="hardware/generated.kicad_pcb",
    )
    h["execution"]._require_pcb_write_target_allowed(generated)


@pytest.mark.parametrize("phase", ["NATIVE_READBACK", "DRC"])
def test_phase_authority_exception_retains_applied_facts(tmp_path: Path, phase: str):
    from dataclasses import replace
    h = harness(tmp_path)
    def authorization(name, *_):
        if name == phase:
            raise RuntimeError("fake phase authority unavailable")
        return True
    h["setup"] = replace(h["setup"], authorize_phase=authorization)
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert (h["reader"].calls, h["drc"].calls) == ((0, 0) if phase == "NATIVE_READBACK" else (1, 0))


@pytest.mark.parametrize("post_apply_scan", [1, 2, 3])
def test_post_apply_snapshot_exception_retains_applied_receipt(tmp_path: Path, monkeypatch, post_apply_scan: int):
    h = harness(tmp_path)
    workspace, operation = h["workspace"], h["operation"]
    original = workspace.snapshot
    count = 0
    def snapshot(**kwargs):
        nonlocal count
        if workspace.get_receipt(operation.project_id, operation.operation_id) is not None:
            count += 1
            if count == post_apply_scan:
                raise OSError("fake snapshot read unavailable")
        return original(**kwargs)
    monkeypatch.setattr(workspace, "snapshot", snapshot)
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", operation.project_id, operation.operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.recovery_state is not RecoveryState.NONE


@pytest.mark.parametrize("inject_trusted_setup", [False, True])
def test_create_app_lifespan_native_composition_is_optional_and_canonical(
    tmp_path: Path, monkeypatch, inject_trusted_setup: bool, native_v2: bool = False, mismatch: str | None = None,
):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from embedded_copilot.api import main
    from embedded_copilot.services.config import Settings

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID) if native_v2 else harness(tmp_path)
    if mismatch is not None:
        setattr(h["reader"], mismatch, object())
    if not inject_trusted_setup:
        h["catalog"].set_native_move_production_setup(None)
        h["execution"]._native_move_authorizer = None
    captured = []
    actual_worker = main.ExecutionWorker
    def capture_worker(**kwargs):
        worker = actual_worker(**kwargs)
        captured.append(worker)
        return worker
    monkeypatch.setattr(main, "ExecutionWorker", capture_worker)
    monkeypatch.setattr(main, "discover_kicad_cli", lambda **kwargs: None)
    monkeypatch.setattr(main, "discover_kicad_footprint_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "discover_kicad_symbol_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "discover_kicad_pcb_drc_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "discover_kicad_pcb_parse_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "probe_kicad_cli_version", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no tool probe")))
    monkeypatch.setattr(main, "probe_trusted_tool_version", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no tool probe")))
    monkeypatch.setattr(main, "TrustedToolDiscovery", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("tool discovery disabled")))
    monkeypatch.setattr(main.chromadb, "PersistentClient", lambda *args, **kwargs: object())
    app = main.create_app(
        settings=Settings(
            environment="test", desktop_state_database=tmp_path / "app.sqlite3",
            project_root=tmp_path / "projects", _env_file=None,
        ),
        project_catalog_runtime=h["catalog"],
        execution_operation_port=h["execution"],
        workspace_apply_port=SimpleNamespace(_service=h["workspace"]),
        native_move_setup=h["setup"] if inject_trusted_setup else None,
    )
    if mismatch is not None:
        with pytest.raises(ValueError, match="native"), TestClient(app):
            pytest.fail("mismatched canonical native composition accepted")
        assert captured == []
        return
    with TestClient(app):
        assert app.state.native_move_production_available is inject_trusted_setup
        assert len(captured) == 1
        worker = captured[0]
        assert worker._store is h["store"]
        assert worker._recovery_workspace is h["workspace"]
        if inject_trusted_setup:
            assert h["catalog"]._native_move_setup is h["setup"]
            assert isinstance(worker._native_move_adapter, NativeMoveProductionAdapter)
            assert worker._native_move_adapter._setup is h["setup"]
            assert OperationType.KICAD_NATIVE_FOOTPRINT_MOVE in worker._allowed_operation_types
            assert callable(h["execution"]._native_move_authorizer)
        else:
            assert worker._native_move_adapter is None
            assert OperationType.KICAD_NATIVE_FOOTPRINT_MOVE not in worker._allowed_operation_types
            assert h["execution"]._native_move_authorizer is None


@pytest.mark.parametrize("phase", ["reader", "drc"])
def test_native_ports_cannot_change_approved_pcb_bytes(tmp_path: Path, phase: str):
    h = harness(tmp_path)
    if phase == "reader":
        h["reader"].mutate_board = True
    else:
        h["drc"].mutate_board = True
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.recovery_state is not RecoveryState.NONE
    assert h["board"].read_bytes() != h["operation"].production_context.changeset.entries[0].after_content.encode()
    if phase == "reader":
        assert h["drc"].calls == 0
    else:
        actual = h["workspace"].snapshot(
            tenant_id="tenant-1", project_id=h["project"].project_id, target_id="project-root",
        )
        assert receipt.native_move_production.after_manifest_fingerprint == actual.project_fingerprint


def test_reader_artifact_wrong_tool_identity_cannot_pass(tmp_path: Path):
    h = harness(tmp_path)
    h["reader"].wrong_artifact_tool = True
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert h["drc"].calls == 0

# The V2 fixture exercises independent pcbnew-shaped object getters through a fake runner.
BOARD_V2 = (
    b'(kicad_pcb (version 20240108) (net 1 "GND") '
    b'(footprint "Library:Part" (layer "F.Cu") (uuid 01234567-89ab-4cde-8f01-23456789abcd) '
    b'(at 10 20 90) (property "Reference" "U1") '
    b'(pad "1" smd rect (at 0 0) (size 1 2) (net 1 "GND") '
    b'(uuid 11111111-1111-4111-8111-111111111111))) '
    b'(footprint "Library:Other" (layer "F.Cu") (uuid 22222222-2222-4222-8222-222222222222) '
    b'(at 30 40 0) (property "Reference" "U2")))'
)


class FakeNativeUuid:
    def __init__(self, value):
        self.value = value

    def AsString(self):
        return self.value


class FakeNativePoint:
    def __init__(self, x, y):
        self.x, self.y = x, y


class FakeNativeAngle:
    def __init__(self, degrees):
        self.degrees = degrees

    def AsDegrees(self):
        return self.degrees


class FakeNativePad:
    def __init__(self, *, number="1", net="GND", rotation=-90, size_x=1_000_000):
        self.m_Uuid = FakeNativeUuid("11111111-1111-4111-8111-111111111111")
        self.number, self.net, self.rotation, self.size_x = number, net, rotation, size_x

    def GetAttribute(self):
        return "SMD"

    def GetShape(self):
        return "RECT"

    def GetFPRelativePosition(self):
        return FakeNativePoint(0, 0)

    def GetFPRelativeOrientation(self):
        return FakeNativeAngle(self.rotation)

    def GetSize(self):
        return FakeNativePoint(self.size_x, 2_000_000)

    def GetNumber(self):
        return self.number

    def GetNetCode(self):
        return 1

    def GetNetname(self):
        return self.net


class FakeNativeFootprint:
    def __init__(self, uuid, ref, x, y, rotation, pads):
        self.m_Uuid = FakeNativeUuid(uuid)
        self.ref, self.x, self.y, self.rotation, self.pads = ref, x, y, rotation, pads

    def GetPosition(self):
        return FakeNativePoint(self.x, self.y)

    def GetReference(self):
        return self.ref

    def GetLayerName(self):
        return "F.Cu"

    def GetOrientationDegrees(self):
        return self.rotation

    def Pads(self):
        return self.pads


class FakeNativeBoard:
    def __init__(self, *, bad_uuid=False, bad_net=False, bad_other=False, missing_pads=False, bad_pad_size=False, bad_pad_rotation=False):
        target_uuid = "33333333-3333-4333-8333-333333333333" if bad_uuid else UUID
        self.footprints = [
            FakeNativeFootprint(target_uuid, "U1", 11_000_000, 20_000_000, 90,
                                [] if missing_pads else [FakeNativePad(
                                    net="WRONG" if bad_net else "GND",
                                    rotation=45 if bad_pad_rotation else -90,
                                    size_x=2_000_000 if bad_pad_size else 1_000_000,
                                )]),
            FakeNativeFootprint("22222222-2222-4222-8222-222222222222", "U2",
                                31_000_000 if bad_other else 30_000_000, 40_000_000, 0, []),
        ]

    def GetFootprints(self):
        return self.footprints


class FakeNativeApi:
    PAD_ATTRIB_SMD = "SMD"
    PAD_SHAPE_RECT = "RECT"

    def __init__(self, board):
        self.board = board
        self.loaded = []

    def LoadBoard(self, path):
        self.loaded.append(path)
        return self.board


class FakeNativeRunner:
    def __init__(self, api):
        self.api = api
        self.calls = []

    def run(self, argv, *, cwd, timeout_seconds, environment, cancellation):

        assert argv[0] == "fake-native-reader"
        assert not cancellation.is_cancelled()
        assert 0 < timeout_seconds <= 30
        self.calls.append(argv)
        projected = load_native_projection(self.api, argv[1], argv[2])
        return 0, json.dumps(projected, sort_keys=True, separators=(",", ":")).encode(), b""


class FakeV2Reader(FakeReader):
    def __init__(self, artifacts, project_id):
        super().__init__(artifacts, project_id)
        self.api = FakeNativeApi(FakeNativeBoard())
        self.runner = FakeNativeRunner(self.api)
        self.ready = True

    def native_dispatch_ready(self):
        return self.ready

    def read_back(self, operation, board, policy, phase):
        self.calls += 1
        code, stdout, _ = self.runner.run(
            ("fake-native-reader", str(board), operation.native_uuid),
            cwd=board.parent, timeout_seconds=phase.timeout_seconds(), environment={}, cancellation=phase,
        )
        assert code == 0
        projection = json.loads(stdout)
        payload = board.read_bytes()
        document = {"pcb_sha256": sha(payload), **projection}
        blob = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        artifact = identity(
            blob, artifact_id="readback-" + operation.operation_id,
            project_id=self.project_id, workspace_fp=operation.workspace_fingerprint,
            operation_id=operation.operation_id, tool_id=policy.reader_id, tool_version=policy.reader_version,
        )
        self.artifacts.put(artifact, blob)
        return NativeMoveReadBackV1(
            status="PASS", reader_id=policy.reader_id, reader_version=policy.reader_version,
            executable_sha256=policy.executable_sha256, pcb_sha256=sha(payload),
            native_uuid=projection["native_uuid"], x_mm=projection["x_mm"], y_mm=projection["y_mm"],
            rotation_degrees=projection["rotation_degrees"],
            pad_net_fingerprint=projection["pad_net_fingerprint"],
            non_target_summary_fingerprint=projection["non_target_summary_fingerprint"],
            observation_artifact=artifact,
        )


@pytest.mark.parametrize("fault", ["bad_uuid", "bad_net", "bad_other", "missing_pads", "bad_pad_size", "bad_pad_rotation"])
def test_v2_native_getter_projection_detects_independent_drift(fault):

    expected = source_projection(BOARD_V2.replace(b"(at 10 20 90)", b"(at 11 20 90)"), UUID)
    actual = native_projection(FakeNativeBoard(), UUID, FakeNativeApi(None))
    assert actual == expected
    board = FakeNativeBoard(**{fault: True})
    if fault == "bad_uuid":
        with pytest.raises(ValueError, match="target coverage"):
            native_projection(board, UUID, FakeNativeApi(None))
    else:
        observed = native_projection(board, UUID, FakeNativeApi(None))
        assert observed != expected


def test_v2_source_missing_required_coverage_fails_before_apply():

    with pytest.raises(ValueError, match="coverage unavailable"):
        source_projection(BOARD, UUID)
    with pytest.raises(ValueError, match="pad type"):
        source_projection(BOARD_V2.replace(b'"1" smd rect', b'"1" thru_hole circle'), UUID)


def test_v2_fake_runner_full_phase_success_and_default_gate(tmp_path: Path):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader, reader_id=READER_ID)
    assert h["setup"].available() is True
    assert run(h).status is OperationStatus.SUCCEEDED
    assert len(h["reader"].runner.calls) == 1
    assert h["reader"].api.loaded == [str(h["board"])]
    (h["reader"].bundle.root / "python311.zip").write_bytes(b"drifted fake stdlib")
    assert h["setup"].available() is False


@pytest.mark.parametrize("fault", ["bad_uuid", "bad_net", "bad_other", "missing_pads", "bad_pad_size", "bad_pad_rotation"])
def test_v2_fake_runner_drift_preserves_apply_but_never_calls_drc(tmp_path: Path, fault: str):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader, reader_id=READER_ID)
    h["reader"].api.board = FakeNativeBoard(**{fault: True})
    status = run(h)
    assert status.status is not OperationStatus.SUCCEEDED
    receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert h["drc"].calls == 0


def test_v2_short_phase_deadline_keeps_fake_runner_idle(tmp_path: Path):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader, reader_id=READER_ID)
    status = run(h, adapter_clock=lambda: NOW + timedelta(minutes=3, seconds=59, milliseconds=500))
    assert status.status is not OperationStatus.SUCCEEDED
    assert h["reader"].calls == 0
    assert h["reader"].runner.calls == []
    assert h["drc"].calls == 0


def test_v2_post_result_phase_gate_discards_late_observation(tmp_path: Path):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader, reader_id=READER_ID)
    now = [NOW + timedelta(minutes=1)]
    original = h["reader"].read_back

    def late(*args):
        result = original(*args)
        now[0] = NOW + timedelta(minutes=5)
        return result

    h["reader"].read_back = late
    status = run(h, adapter_clock=lambda: now[0])
    assert status.status is not OperationStatus.SUCCEEDED
    assert h["reader"].runner.calls
    assert h["drc"].calls == 0
    receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"



@pytest.mark.parametrize("mutation", [
    (b'(at 0 0)', b'(at 0 0 45)'),
    (b'(size 1 2)', b'(size 2 2)'),
])
def test_v2_pad_local_geometry_changes_cannot_echo_as_same_projection(mutation):

    original = source_projection(BOARD_V2, UUID)
    changed = source_projection(BOARD_V2.replace(*mutation), UUID)
    assert changed["pad_net_fingerprint"] != original["pad_net_fingerprint"]



def test_v2_missing_real_dispatch_cannot_create_approvable_candidate(tmp_path: Path):
    from embedded_copilot.project_catalog.runtime import ProjectCatalogUnavailable

    with pytest.raises(ProjectCatalogUnavailable, match="NATIVE_MOVE_UNAVAILABLE"):
        harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader,
                reader_id=READER_ID, reader_ready=False)


def test_v2_candidate_preflight_rejects_missing_finite_coverage(tmp_path: Path):
    from embedded_copilot.project_catalog.runtime import ProjectCatalogRejected

    with pytest.raises(ProjectCatalogRejected, match="PRESERVATION_UNPROVEN"):
        harness(tmp_path, board_bytes=BOARD, reader_factory=FakeV2Reader, reader_id=READER_ID)

def test_historical_schema1_drc_baseline_cannot_enable_v2_seam(tmp_path):
    with pytest.raises(Exception, match="NATIVE_MOVE_UNAVAILABLE"):
        harness(tmp_path, legacy_baseline=True)


def test_v2_drc_manifest_must_cover_every_canonical_workspace_file(tmp_path):
    h = harness(tmp_path)
    snapshot = h["workspace"].snapshot(
        tenant_id="tenant-1", project_id=h["project"].project_id, target_id="project-root",
    )
    omitted = snapshot.model_copy(update={
        "files": tuple(item for item in snapshot.files if item.relative_file_reference != "notes.txt"),
    })
    assert len(omitted.files) + 1 == len(snapshot.files)
    artifact = capture_drc_v2(
        h["artifacts"], h["setup"].drc_policy, omitted, "incomplete-baseline", h["board"].read_bytes(),
    )
    context = json.loads(h["artifacts"].read(artifact))
    with pytest.raises(ValueError, match="FILE inventory"):
        _drc_snapshot_bound(
            context, snapshot, "native/layout.kicad_pcb", "native/layout.kicad_prl",
            h["board"].read_bytes(), None, None,
            read_artifact=h["artifacts"].read, operation_id="incomplete-baseline",
        )


def test_v2_drc_ignores_fake_collector_counts_and_recomputes_from_raw(tmp_path):
    h = harness(tmp_path)
    h["drc"].claimed_error_count = 99
    status = run(h)
    assert status.status is OperationStatus.SUCCEEDED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.drc_result.error_count == 1
    assert receipt.native_move_production.drc_result.status == "NO_NEW_KEYS"


def test_v2_drc_new_key_overrides_fake_collector_no_new_keys_claim(tmp_path):
    h = harness(tmp_path)
    h["drc"].updates = {"violations": [
        {"key": "existing", "severity": "error"}, {"key": "new", "severity": "error"},
    ]}
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.drc_result.status == "NEW_KEYS"
    assert receipt.native_move_production.drc_result.new_key_count == 1


def test_v2_drc_phase_revoked_during_artifact_read_retains_applied_evidence(tmp_path):
    h = harness(tmp_path)
    original_read = h["artifacts"].read
    target = h["operation"].operation_id + "-report"
    def revoking_read(identity):
        data = original_read(identity)
        if identity.artifact_id == target:
            h["phases"].discard("DRC")
        return data
    h["artifacts"].read = revoking_read
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.after_manifest_fingerprint is not None
    assert receipt.native_move_production.drc_result.status == "COMPARABILITY_UNKNOWN"
    assert h["drc"].calls == 1


def test_v2_incomplete_baseline_workspace_is_rejected_before_apply(tmp_path):
    h = harness(tmp_path, baseline_omit_notes=True)
    assert h["setup"].available() is True
    status = run(h)
    assert status.status is OperationStatus.FAILED
    assert h["drc"].calls == 0
    assert h["board"].read_bytes() == BOARD
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "NOT_RUN"


def test_v2_baseline_outer_envelope_cannot_claim_another_tenant(tmp_path):
    h = harness(tmp_path)
    policy = h["setup"].drc_policy
    original = policy.baseline_report
    document = original.model_dump(mode="json", exclude={"fingerprint"})
    document["tenant_id"] = "other-tenant"
    forged = ArtifactIdentity.model_validate(document)
    h["artifacts"].put(forged, h["artifacts"].read(original))
    other = replace(h["setup"], drc_policy=policy.model_copy(update={"baseline_report": forged}))
    assert other.available() is False


def test_v2_post_apply_collector_omits_file_and_preserves_applied_manifest(tmp_path):
    h = harness(tmp_path)
    h["drc"].omit_notes = True
    status = run(h)
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.after_manifest_fingerprint is not None
    assert receipt.native_move_production.drc_result.status == "COMPARABILITY_UNKNOWN"
    assert h["drc"].calls == 1


def test_v2_drc_post_collector_snapshot_failure_retains_unknown_manifest(tmp_path, monkeypatch):
    h = harness(tmp_path)
    original_snapshot = h["workspace"].snapshot
    failed = False
    def failing_after_collector(*args, **kwargs):
        nonlocal failed
        if getattr(h["drc"], "last_artifact", None) is not None and not failed:
            failed = True
            raise OSError("post-collector scan unavailable")
        return original_snapshot(*args, **kwargs)
    monkeypatch.setattr(h["workspace"], "snapshot", failing_after_collector)
    status = run(h)
    assert failed is True
    assert status.status is OperationStatus.RECOVERY_REQUIRED
    receipt = h["store"].get_receipt("tenant-1", h["project"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.after_manifest_fingerprint is None
    assert receipt.native_move_production.recovery_state is RecoveryState.TARGET_STATE_UNKNOWN
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert receipt.native_move_production.native_read_back.observation_artifact == h["reader"].last_artifact
    assert receipt.native_move_production.drc_result.status == "COMPARABILITY_UNKNOWN"


@pytest.mark.parametrize("fp_angle,pad_angle,expected_fp,expected_relative", [
    ("90", "90", 90, 0),
    ("180", "180", 180, 0),
    ("-180", "180", 180, 0),
    ("270", "0", -90, 90),
    ("-90", "180", -90, 270),
    ("-179", "359", -179, 538),
    ("90", "10", 90, -80),
    ("450", "-90", 90, 180),
    ("-540", None, 180, -180),
    ("0", "720", 0, 0),
    ("0", "-360", 0, 0),
    ("0", "-90", 0, 270),
])
def test_v2_source_angles_match_pinned_kicad_getter_domains(
    fp_angle, pad_angle, expected_fp, expected_relative,
):
    # Constants come from KiCad 10.0.5 parser/PAD/PADSTACK/EDA_ANGLE source,
    # not from the source projection under test or a fake source parser.
    payload = BOARD_V2.replace(b"(at 10 20 90)", f"(at 11 20 {fp_angle})".encode())
    if pad_angle is not None:
        payload = payload.replace(b"(at 0 0)", f"(at 0 0 {pad_angle})".encode())
    board = FakeNativeBoard()
    board.footprints[0].rotation = expected_fp
    board.footprints[0].pads[0].rotation = expected_relative
    assert source_projection(payload, UUID) == native_projection(board, UUID, FakeNativeApi(None))
    board.footprints[0].pads[0].rotation += 1
    assert source_projection(payload, UUID) != native_projection(board, UUID, FakeNativeApi(None))


def test_v2_source_rejects_placement_after_pads_before_apply():
    # KiCad parses sequentially and rotates already-added pads when at is read.
    # The finite mapper deliberately covers only placement-before-pad order.
    payload = BOARD_V2.replace(b"(at 10 20 90) ", b"")
    needle = b'(uuid 11111111-1111-4111-8111-111111111111)))'
    payload = payload.replace(needle, b'(uuid 11111111-1111-4111-8111-111111111111)) (at 10 20 90))')
    with pytest.raises(ValueError, match="placement order"):
        source_projection(payload, UUID)


@pytest.mark.parametrize("raw_angle,native_angle,relative_pad", [
    (270, -90, 90), (-180, 180, -180), (450, 90, -90),
])
def test_v2_full_phase_compares_typed_readback_in_native_angle_domain(
    tmp_path, raw_angle, native_angle, relative_pad,
):
    payload = BOARD_V2.replace(b"(at 10 20 90)", f"(at 10 20 {raw_angle})".encode())
    h = harness(tmp_path, board_bytes=payload, reader_factory=FakeV2Reader, reader_id=READER_ID)
    h["reader"].api.board.footprints[0].rotation = native_angle
    h["reader"].api.board.footprints[0].pads[0].rotation = relative_pad
    assert h["operation"].rotation_degrees == raw_angle
    assert run(h).status is OperationStatus.SUCCEEDED
    receipt = h["store"].get_receipt(
        h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id,
    )
    assert receipt.native_move_production.native_read_back.rotation_degrees == native_angle
    assert h["board"].read_bytes() == payload.replace(
        f"(at 10 20 {raw_angle})".encode(), f"(at 11.0 20.0 {raw_angle})".encode(),
    )
