"""Offline post-APPLY DRC collector checks; no native process is started."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.execution_worker.adapters import SafeProcessRunner
from embedded_copilot.execution_worker.contracts import (
    ApprovalStatus,
    KiCadNativeFootprintMoveOperation,
    NativeMoveDrcPolicy,
    NativeMoveProductionContextV1,
    OperationStatus,
    PcbDrcBaselineCollectorPolicy,
    native_move_operation_id,
    native_move_operation_nonce,
)
from embedded_copilot.execution_worker.drc_baseline import (
    BaselineClosureFile,
    PcbDrcBaselineCollector,
    _encoded,
    _sha,
)
from embedded_copilot.execution_worker.native_drc_provenance import (
    compare_drc_v2_contexts,
    validate_drc_v2_context,
)
from embedded_copilot.execution_worker.native_move import (
    NativeReadbackPhase,
    _drc_snapshot_bound,
)
from embedded_copilot.execution_worker.native_move_drc_collector import (
    NativeMoveDrcCollector,
)
from tests.execution_worker.test_c2r_native_move_offline import Clock, harness


def _setup(tmp_path):
    h = harness(tmp_path)
    tool = tmp_path / "kicad-cli.exe"
    rule = tmp_path / "rule"
    dependency = tmp_path / "dependency"
    for path, content in ((tool, b"fake-drc"), (rule, b"rules"), (dependency, b"library")):
        path.write_bytes(content)
    def closure(role, relative, path):
        content = path.read_bytes()
        return BaselineClosureFile(role, relative, path, _sha(content), len(content))
    bundle = (closure("EXECUTABLE", "bin/kicad-cli.exe", tool),)
    rules = (closure("RULE", "project/rules", rule),)
    dependencies = (closure("DEPENDENCY", "lib/footprints", dependency),)
    environment = (("PATH", "offline-fixed"),)
    baseline_policy = PcbDrcBaselineCollectorPolicy(
        collector_id="KICAD_DRC_COLLECTOR", collector_version="1",
        tool_id="kicad-cli", tool_version="10.0.5", executable_sha256=_sha(tool.read_bytes()),
        tool_bundle_manifest_fingerprint=_sha(PcbDrcBaselineCollector._manifest(bundle)),
        rules_manifest_fingerprint=_sha(PcbDrcBaselineCollector._manifest(rules)),
        dependency_manifest_fingerprint=_sha(PcbDrcBaselineCollector._manifest(dependencies)),
        environment_content_fingerprint=_sha(_encoded({"schema_version": 1, "entries": [
            {"name": "PATH", "value": "offline-fixed"}]})),
        timeout_seconds=30,
    )
    snapshot = h["workspace"].snapshot(tenant_id="tenant-1", project_id=h["project"].project_id,
                                        target_id="project-root")
    baseline_op = SimpleNamespace(
        operation_id="baseline-real-closure", tenant_id="tenant-1", project_id=h["project"].project_id,
        target_id="project-root", workspace_fingerprint=snapshot.project_fingerprint,
        target_fingerprint=snapshot.target_fingerprint, pcb_relative_path="native/layout.kicad_pcb",
        prl_relative_path="native/layout.kicad_prl", pcb_sha256=_sha(h["source_board"].read_bytes()),
        prl_before_sha256=None, collector_policy=baseline_policy,
    )
    raw = {"$schema": "https://schemas.kicad.org/drc.v1.json", "coordinate_units": "mm",
           "date": "2026-10-13T00:00:00", "ignored_checks": [],
           "included_severities": ["error", "warning"], "kicad_version": "10.0.5",
           "schematic_parity": [], "source": "layout.kicad_pcb", "unconnected_items": [],
           "violations": []}
    class BaselineRunner:
        def run_drc_baseline(self, argv, **_):
            Path(argv[6]).write_bytes(_encoded(raw))
            return 0, b"", b""
    baseline = PcbDrcBaselineCollector(
        workspace=h["workspace"], policy=baseline_policy, tool_bundle=bundle,
        rules=rules, dependencies=dependencies, environment=environment,
        report_root=tmp_path / "baseline-reports", runner=BaselineRunner(), clock=Clock().now,
    ).execute(baseline_op, deadline=h["operation"].expires_at,
              cancellation=SimpleNamespace(is_cancelled=lambda: False), revalidate=lambda: True)
    assert baseline.status == "SUCCEEDED"
    for identity, content in (*baseline.additional_artifacts, (baseline.artifact, baseline.artifact_content)):
        h["artifacts"].put(identity, content)
    policy = NativeMoveDrcPolicy(
        tool_id="kicad-cli", tool_version="10.0.5", executable_sha256=_sha(tool.read_bytes()),
        timeout_seconds=30, baseline_report=baseline.artifact, ignored_checks=(),
    )
    old = h["operation"]
    context_data = old.production_context.model_dump(mode="python", exclude={"fingerprint"})
    context_data["drc_policy"] = policy
    context = NativeMoveProductionContextV1.model_validate(context_data)
    operation_data = old.model_dump(mode="python", exclude={"fingerprint"})
    operation_data["production_context"] = context
    operation_data["operation_id"] = native_move_operation_id(
        old.tenant_id, old.project_id, old.proposal_id, old.proposal_fingerprint,
        old.mapping_review_id, old.mapping_review_fingerprint, context.fingerprint,
    )
    operation_data["nonce"] = native_move_operation_nonce(
        old.mapping_review_id, old.mapping_review_fingerprint, context.fingerprint,
    )
    operation = KiCadNativeFootprintMoveOperation.model_validate(operation_data)
    h["board"].write_text(context.changeset.entries[0].after_content, encoding="utf-8")
    approval = SimpleNamespace(status=ApprovalStatus.CONSUMED, operation_id=operation.operation_id,
                               operation_fingerprint=operation.fingerprint, reviewer_id=operation.requester_id,
                               expires_at=operation.expires_at)
    status = SimpleNamespace(status=OperationStatus.RUNNING, cancellation_requested=False, approval_id="approval-1")
    class Store:
        def get_operation(self, *_): return operation
        def get_status(self, *_): return status
        def get_approval(self, *_): return approval
    class Catalog:
        def _native_move_readback_binding(self, *_): return "canonical-binding"
        def revalidate_native_move_readback(self, *_): return True
    runner = SafeProcessRunner()
    collector = NativeMoveDrcCollector(
        workspace=h["workspace"], artifact_store=h["artifacts"], store=Store(), catalog=Catalog(),
        authorize_phase=lambda phase, *_: phase == "DRC", tool_bundle=bundle, rules=rules,
        dependencies=dependencies, environment=environment,
        report_root=tmp_path / "after-reports", runner=runner, clock=Clock().now,
    )
    phase = NativeReadbackPhase(deadline=operation.expires_at, policy_timeout_seconds=30,
                                clock=Clock().now, allowed=lambda: True)
    return SimpleNamespace(h=h, collector=collector, operation=operation, policy=policy,
                           phase=phase, runner=runner, raw=raw, tool=tool, rules=rules)


def test_post_apply_single_capture_binds_actual_raw_and_workspace(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    calls = []
    def fake(argv, **kwargs):
        calls.append((argv, kwargs))
        assert not kwargs["cancellation"].is_cancelled()
        Path(argv[6]).write_bytes(_encoded(case.raw))
        return 0, b"", b""
    monkeypatch.setattr(case.runner, "run_drc_baseline", fake)
    assert case.collector.ready_for(case.policy)
    result = case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)
    assert len(calls) == 1 and calls[0][0][1:6] == ("pcb", "drc", "--format", "json", "--output")
    assert result.report is not None and result.report.workspace_fingerprint == case.operation.workspace_fingerprint
    context = json.loads(case.h["artifacts"].read(result.report))
    assert validate_drc_v2_context(context, read_artifact=case.h["artifacts"].read, policy=case.policy) == context
    after = case.h["workspace"].snapshot(tenant_id=case.operation.tenant_id,
                                          project_id=case.operation.project_id,
                                          target_id=case.operation.target_id)
    _drc_snapshot_bound(context, after, case.operation.pcb_relative_path,
                        case.operation.production_context.side_effect_policy.prl_relative_path,
                        case.h["board"].read_bytes(), None, None,
                        read_artifact=case.h["artifacts"].read, operation_id=case.operation.operation_id)
    baseline = json.loads(case.h["artifacts"].read(case.policy.baseline_report))
    assert compare_drc_v2_contexts(baseline, context,
                                   read_artifact=case.h["artifacts"].read,
                                   policy=case.policy)["status"] == "NO_NEW_KEYS"
    raw_ref = context["raw_report"]
    assert case.h["artifacts"].read(type(result.report).model_validate(raw_ref)) == _encoded(case.raw)
    with pytest.raises(ValueError, match="no replay"):
        case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)


def test_closure_drift_blocks_readiness_and_dispatch(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    calls = []
    monkeypatch.setattr(case.runner, "run_drc_baseline", lambda *a, **k: calls.append(True))
    case.tool.write_bytes(b"changed")
    assert not case.collector.ready_for(case.policy)
    with pytest.raises(ValueError, match="closure"):
        case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)
    assert calls == []


def test_revoked_phase_blocks_dispatch(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    calls = []
    monkeypatch.setattr(case.runner, "run_drc_baseline", lambda *a, **k: calls.append(True))
    revoked = NativeReadbackPhase(deadline=case.phase.deadline, policy_timeout_seconds=30,
                                  clock=Clock().now, allowed=lambda: False)
    with pytest.raises(PermissionError):
        case.collector.compare(case.operation, case.h["board"], case.policy, revoked)
    assert calls == []


def test_nonzero_exit_preserves_raw_and_after_manifest(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    def failing(argv, **_):
        Path(argv[6]).write_bytes(b"partial raw")
        return 1, b"", b""
    monkeypatch.setattr(case.runner, "run_drc_baseline", failing)
    with pytest.raises(ValueError, match="failed"):
        case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)
    prefix = "drc-after-" + hashlib.sha256(case.operation.operation_id.encode()).hexdigest()[:32] + "-"
    assert (case.h["artifacts"]._root / (prefix + "raw-report")).read_bytes() == b"partial raw"
    assert (case.h["artifacts"]._root / (prefix + "workspace-after")).is_file()


def test_actual_fixed_runner_rechecks_phase_before_popen(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    spawned = []
    monkeypatch.setattr("embedded_copilot.execution_worker.adapters.subprocess.Popen",
                        lambda *args, **kwargs: spawned.append(True))
    monkeypatch.setattr(case.collector, "ready_for", lambda policy: False)
    with pytest.raises(InterruptedError):
        case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)
    assert spawned == []


def test_slow_closure_read_cannot_outlive_phase_authority(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    spawned = []
    revoked = [False]
    original_ready = case.collector.ready_for
    def slow_ready(policy):
        available = original_ready(policy)
        revoked[0] = True
        return available
    monkeypatch.setattr(case.collector, "ready_for", slow_ready)
    monkeypatch.setattr("embedded_copilot.execution_worker.adapters.subprocess.Popen",
                        lambda *args, **kwargs: spawned.append(True))
    phase = NativeReadbackPhase(deadline=case.phase.deadline, policy_timeout_seconds=30,
                                clock=Clock().now, allowed=lambda: not revoked[0])
    with pytest.raises(InterruptedError):
        case.collector.compare(case.operation, case.h["board"], case.policy, phase)
    assert spawned == []


def test_workspace_report_root_is_unavailable_before_dispatch(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    case.collector.report_root = case.h["workspace_root"] / "reports"
    calls = []
    monkeypatch.setattr(case.runner, "run_drc_baseline", lambda *a, **k: calls.append(True))
    assert not case.collector.ready_for(case.policy)
    with pytest.raises(ValueError, match="report root"):
        case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)
    assert calls == []


@pytest.mark.parametrize("changed", ("notes.txt", "native/layout.kicad_prl"))
def test_cli_workspace_side_effect_preserves_raw_and_rejects_result(tmp_path, monkeypatch, changed):
    case = _setup(tmp_path)
    def mutating(argv, **_):
        Path(argv[6]).write_bytes(_encoded(case.raw))
        path = case.h["workspace_root"] / changed
        path.write_bytes(b"unexpected DRC side effect")
        return 0, b"", b""
    monkeypatch.setattr(case.runner, "run_drc_baseline", mutating)
    with pytest.raises(ValueError, match="failed"):
        case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)
    prefix = "drc-after-" + hashlib.sha256(case.operation.operation_id.encode()).hexdigest()[:32] + "-"
    assert (case.h["artifacts"]._root / (prefix + "raw-report")).read_bytes() == _encoded(case.raw)
    assert (case.h["artifacts"]._root / (prefix + "workspace-after")).is_file()


def test_malformed_raw_never_becomes_v2_context(tmp_path, monkeypatch):
    case = _setup(tmp_path)
    def malformed(argv, **_):
        Path(argv[6]).write_bytes(b"not json")
        return 0, b"", b""
    monkeypatch.setattr(case.runner, "run_drc_baseline", malformed)
    with pytest.raises((TypeError, ValueError)):
        case.collector.compare(case.operation, case.h["board"], case.policy, case.phase)
    prefix = "drc-after-" + hashlib.sha256(case.operation.operation_id.encode()).hexdigest()[:32] + "-"
    assert (case.h["artifacts"]._root / (prefix + "raw-report")).read_bytes() == b"not json"
    assert not (case.h["artifacts"]._root / (prefix + "context")).exists()
