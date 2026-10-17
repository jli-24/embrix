"""Offline C2-R fixed native read-back runner contract; no real process or pcbnew import."""
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.contracts import OperationStatus
from tests.execution_worker.test_c2r_native_move_offline import (
    BOARD_V2,
    NOW,
    READER_ID,
    FakeV2Reader,
    fake_native_popen,
    harness,
    run,
)


def test_post_apply_catalog_currentness_accepts_only_receipted_after_bytes(tmp_path: Path):
    h = harness(tmp_path)
    assert run(h).status.value == "SUCCEEDED"
    status = h["store"].get_status(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    approval = h["store"].get_approval(h["operation"].tenant_id, h["operation"].project_id, status.approval_id)
    assert h["catalog"].revalidate_native_move_readback(h["operation"], approval) is True
    h["board"].write_bytes(h["board"].read_bytes() + b" ")
    assert h["catalog"].revalidate_native_move_readback(h["operation"], approval) is False



def test_independent_c1_source_change_after_apply_cannot_authorize_readback(tmp_path: Path):
    h = harness(tmp_path, separate_workspace=True)
    assert run(h).status.value == "SUCCEEDED"
    operation = h["operation"]
    status = h["store"].get_status(operation.tenant_id, operation.project_id, operation.operation_id)
    approval = h["store"].get_approval(operation.tenant_id, operation.project_id, status.approval_id)
    assert h["catalog"].revalidate_native_move_readback(operation, approval) is True
    h["source_board"].write_bytes(h["source_board"].read_bytes() + b" ")
    assert h["catalog"].revalidate_native_move_readback(operation, approval) is False


def test_dedicated_runner_rejects_untrusted_invocation():
    from embedded_copilot.execution_worker.adapters import SafeProcessRunner

    try:
        SafeProcessRunner().run_native_readback(object(), object())
    except (TypeError, ValueError, PermissionError):
        pass
    else:
        raise AssertionError("untrusted native invocation was dispatched")


@pytest.mark.parametrize("fault", [
    "duplicate_key", "extra_key", "nan", "bool_coverage", "string_coordinate",
    "overflow", "drain_error", "nonzero_exit",
])
def test_dedicated_reader_rejects_untrusted_process_output_after_apply(tmp_path: Path, fault: str):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader, reader_id=READER_ID)
    original = fake_native_popen(h, h["operation"])

    def popen(argv, **kwargs):
        process = original(argv, **kwargs)
        document = json.loads(process.stdout.getvalue())
        if fault == "duplicate_key":
            payload = process.stdout.getvalue().replace(b'{"coverage":', b'{"coverage":{},"coverage":', 1)
        elif fault == "extra_key":
            document["unsupported"] = "fake"
            payload = json.dumps(document).encode()
        elif fault == "nan":
            document["x_mm"] = float("nan")
            payload = json.dumps(document).encode()
        elif fault == "bool_coverage":
            document["coverage"]["footprints"] = True
            payload = json.dumps(document).encode()
        elif fault == "string_coordinate":
            document["x_mm"] = "11"
            payload = json.dumps(document).encode()
        elif fault == "overflow":
            payload = b" " * (64 * 1024 + 1)
        else:
            payload = process.stdout.getvalue()
        process.stdout = io.BytesIO(payload)
        if fault == "drain_error":
            class BrokenStream:
                def read(self, _size):
                    raise OSError("fake pipe failure")
                def close(self):
                    pass
            process.stdout = BrokenStream()
        if fault == "nonzero_exit":
            process.returncode = 2
        return process

    h["fake_popen"] = popen
    status = run(h)
    assert status.status is not OperationStatus.SUCCEEDED
    receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert h["drc"].calls == 0
    assert len(h["reader"].runner.calls) == 1


@pytest.mark.parametrize("fault", ["phase_revoked", "bundle_drift", "deadline_expired", "workspace_extra"])
def test_dedicated_reader_poll_rechecks_canonical_phase_and_files(tmp_path: Path, fault: str):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_factory=FakeV2Reader, reader_id=READER_ID)
    original = fake_native_popen(h, h["operation"])
    now = [NOW + timedelta(minutes=1)]

    def popen(argv, **kwargs):
        process = original(argv, **kwargs)
        if fault == "phase_revoked":
            h["phases"].discard("NATIVE_READBACK")
        elif fault == "bundle_drift":
            (h["reader"].bundle.root / "python311.zip").write_bytes(b"drift")
        elif fault == "deadline_expired":
            now[0] = NOW + timedelta(minutes=5)
        else:
            (h["workspace_root"] / "unexpected.txt").write_text("fake side effect", encoding="utf-8")
        return process

    h["fake_popen"] = popen
    status = run(h, adapter_clock=lambda: now[0])
    assert status.status is not OperationStatus.SUCCEEDED
    receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert h["drc"].calls == 0
    assert len(h["reader"].runner.calls) == 1


@pytest.mark.parametrize("field", [
    "approval_id", "approval_fingerprint", "operation_fingerprint", "production_context_fingerprint",
    "workspace_root", "board_path", "pcb_relative_path", "pcb_sha256", "native_uuid", "deadline", "phase",
    "phase_view", "policy", "operation", "extra", "strict_type",
])
def test_fixed_invocation_rejects_binding_drift_before_dispatch(tmp_path, monkeypatch, field):
    from dataclasses import FrozenInstanceError, replace

    from embedded_copilot.execution_worker.adapters import SafeProcessRunner
    from embedded_copilot.execution_worker.native_move import NativeReadbackPhase
    from embedded_copilot.execution_worker.native_move_pcbnew_reader import (
        NativePcbnewReadbackInvocation,
    )

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    original = SafeProcessRunner.run_native_readback
    inspected = []

    def inspect(runner, invocation, phase):
        assert invocation.authorize(phase)
        assert invocation.phase == "NATIVE_READBACK" and invocation.phase_view is phase
        assert invocation.operation_id == h["operation"].operation_id
        assert invocation.production_context_fingerprint == h["operation"].production_context.fingerprint
        with pytest.raises(FrozenInstanceError):
            invocation.approval_id = "changed"
        if field == "extra":
            with pytest.raises(TypeError):
                NativePcbnewReadbackInvocation(**{
                    name: getattr(invocation, name) for name in invocation.__dataclass_fields__
                }, argv=("python.exe", "-c", "forbidden"))
        else:
            bad_value = {
                "approval_id": "other-approval", "approval_fingerprint": "sha256:" + "0" * 64,
                "operation_fingerprint": "sha256:" + "0" * 64,
                "production_context_fingerprint": "sha256:" + "0" * 64,
                "workspace_root": tmp_path / "other-project", "board_path": tmp_path / "outside.kicad_pcb",
                "pcb_relative_path": "../other.kicad_pcb", "pcb_sha256": "sha256:" + "0" * 64,
                "native_uuid": "33333333-3333-4333-8333-333333333333",
                "deadline": phase.deadline + timedelta(seconds=1), "phase": "DRC",
                "phase_view": NativeReadbackPhase(phase.deadline, phase.policy_timeout_seconds, phase.clock, phase.allowed),
                "policy": invocation.policy.model_copy(update={"reader_version": "old-candidate"}),
                "operation": invocation.operation.model_copy(update={"project_id": "other-project"}),
                "strict_type": 7,
            }[field]
            changed_field = "approval_id" if field == "strict_type" else field
            try:
                bad = replace(invocation, **{changed_field: bad_value})
            except (ValueError, TypeError):
                assert field in {"phase", "strict_type"}
            else:
                assert not bad.authorize(phase)
                with pytest.raises((ValueError, TypeError, PermissionError, InterruptedError)):
                    original(runner, bad, phase)
        assert runner.calls == []
        inspected.append(True)
        return original(runner, invocation, phase)

    monkeypatch.setattr(SafeProcessRunner, "run_native_readback", inspect)
    assert run(h).status is OperationStatus.SUCCEEDED
    assert inspected == [True]
    assert len(h["reader"].runner.calls) == 1


@pytest.mark.parametrize("fault", ["not_running", "old_approval", "old_revision", "old_review", "apply_permission_only"])
def test_canonical_drift_after_apply_cannot_dispatch_native(tmp_path, monkeypatch, fault):
    from embedded_copilot.execution_worker.adapters import SafeProcessRunner
    from embedded_copilot.execution_worker.contracts import ApprovalStatus

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    original = SafeProcessRunner.run_native_readback

    def drift(runner, invocation, phase):
        assert invocation.authorize(phase)
        with monkeypatch.context() as scoped:
            if fault == "not_running":
                get_status = h["store"].get_status
                scoped.setattr(h["store"], "get_status", lambda *args: get_status(*args).model_copy(update={"status": OperationStatus.SUCCEEDED}))
            elif fault == "old_approval":
                get_approval = h["store"].get_approval
                scoped.setattr(h["store"], "get_approval", lambda *args: get_approval(*args).model_copy(update={"status": ApprovalStatus.ISSUED}))
            elif fault == "old_revision":
                read_revision = h["catalog"]._repository.eda_import_revision
                scoped.setattr(h["catalog"]._repository, "eda_import_revision", lambda *args, **kwargs: {**read_revision(*args, **kwargs), "active": False})
            elif fault == "old_review":
                read_review = h["catalog"]._repository.eda_native_mapping_review
                scoped.setattr(h["catalog"]._repository, "eda_native_mapping_review", lambda *args, **kwargs: {**read_review(*args, **kwargs), "decision": "REJECT_MAPPING"})
            else:
                h["phases"].intersection_update({"APPLY"})
            assert not invocation.authorize(phase)
            return original(runner, invocation, phase)
    monkeypatch.setattr(SafeProcessRunner, "run_native_readback", drift)
    assert run(h).status is not OperationStatus.SUCCEEDED
    assert h["reader"].runner.calls == [] and h["drc"].calls == 0
    receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"


@pytest.mark.parametrize("when", ["before_spawn", "after_spawn", "after_result"])
def test_native_cancellation_never_replays_or_discards_applied_board(tmp_path, monkeypatch, when):
    from embedded_copilot.execution_worker.adapters import SafeProcessRunner
    from embedded_copilot.execution_worker.contracts import CancellationRequest

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    op = h["operation"]
    def cancel():
        h["execution"].request_cancellation(CancellationRequest(
            request_id="fake-native-cancel", tenant_id=op.tenant_id, project_id=op.project_id,
            operation_id=op.operation_id, requester_id=op.requester_id,
            reason="isolated cancellation test", requested_at=NOW + timedelta(minutes=1),
        ))
    if when == "before_spawn":
        read = h["reader"].read_back
        def cancelled_read(*args):
            cancel()
            return read(*args)
        h["reader"].read_back = cancelled_read
    elif when == "after_spawn":
        popen = fake_native_popen(h, op)
        def cancelled_popen(*args, **kwargs):
            process = popen(*args, **kwargs)
            cancel()
            return process
        h["fake_popen"] = cancelled_popen
    else:
        runner_call = SafeProcessRunner.run_native_readback
        def late_cancel(runner, invocation, phase):
            result = runner_call(runner, invocation, phase)
            cancel()
            return result
        monkeypatch.setattr(SafeProcessRunner, "run_native_readback", late_cancel)
    status = run(h)
    assert status.status is not OperationStatus.SUCCEEDED
    assert len(h["reader"].runner.calls) == (0 if when == "before_spawn" else 1)
    assert h["drc"].calls == 0
    receipt = h["store"].get_receipt(op.tenant_id, op.project_id, op.operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert h["board"].read_bytes() == op.production_context.changeset.entries[0].after_content.encode()


@pytest.mark.parametrize("fault", ["timeout", "late_drain", "missing_pipe", "hung_drain", "stderr_overflow", "extra_stdout"])
def test_native_control_loop_failure_keeps_single_applied_fact(tmp_path, monkeypatch, fault):
    from types import SimpleNamespace

    from embedded_copilot.execution_worker import adapters

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    original = fake_native_popen(h, h["operation"])
    processes = []
    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        if fault == "timeout":
            process.returncode = None
            process.poll = lambda: process.returncode
            process.wait = lambda timeout=None: process.returncode
        elif fault == "missing_pipe":
            process.stdout = None
        elif fault == "stderr_overflow":
            process.stderr = io.BytesIO(b"x" * (64 * 1024 + 1))
        elif fault == "extra_stdout":
            process.stdout = io.BytesIO(process.stdout.getvalue() + b"\nextra output")
        return process
    h["fake_popen"] = popen
    if fault in {"timeout", "late_drain"}:
        ticks = iter([0.0, 100.0] if fault == "timeout" else [0.0, 0.0, 100.0])
        monkeypatch.setattr(adapters, "time", SimpleNamespace(monotonic=lambda: next(ticks, 100.0), sleep=lambda _: None))
    if fault == "hung_drain":
        class HungThread:
            def __init__(self, **kwargs):
                pass
            def start(self):
                pass
            def join(self, **kwargs):
                pass
            def is_alive(self):
                return True
        monkeypatch.setattr(adapters.threading, "Thread", HungThread)
    status = run(h)
    assert status.status is not OperationStatus.SUCCEEDED
    assert len(h["reader"].runner.calls) == 1 and h["drc"].calls == 0
    assert processes[0].returncode is not None
    receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"


@pytest.mark.parametrize("changed", ["pcb", "prl", "other_file"])
def test_native_file_changes_preserve_scene_and_actual_manifest(tmp_path, changed):
    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    original = fake_native_popen(h, h["operation"])
    path = {"pcb": h["board"], "prl": h["board"].with_suffix(".kicad_prl"), "other_file": h["workspace_root"] / "notes.txt"}[changed]
    changed_bytes = b"isolated unexpected native side effect"
    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        path.write_bytes(changed_bytes)
        return process
    h["fake_popen"] = popen
    assert run(h).status is not OperationStatus.SUCCEEDED
    assert path.read_bytes() == changed_bytes
    assert h["drc"].calls == 0 and len(h["reader"].runner.calls) == 1
    current = h["workspace"].snapshot(tenant_id=h["operation"].tenant_id, project_id=h["operation"].project_id)
    receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.after_manifest_fingerprint == current.project_fingerprint
    assert receipt.native_move_production.recovery_state.value == "TARGET_STATE_UNKNOWN"


@pytest.mark.parametrize("drift", ["none", "extra", "prl", "missing"])
def test_v2_bundle_record_fresh_process_recovery_has_zero_tool_replay(tmp_path, drift):
    from tests.execution_worker.test_c2r_native_move_offline import (
        test_recovery_in_fresh_process_without_native_reader_or_drc as recovery_case,
    )
    recovery_case(tmp_path, drift, native_v2=True)


@pytest.mark.parametrize("inject_setup", [False, True])
def test_real_app_concrete_v2_reader_composition_remains_optional(tmp_path, monkeypatch, inject_setup):
    from tests.execution_worker.test_c2r_native_move_offline import (
        test_create_app_lifespan_native_composition_is_optional_and_canonical as composition_case,
    )
    composition_case(tmp_path, monkeypatch, inject_setup, native_v2=True)


@pytest.mark.parametrize("mismatch", ["store", "catalog", "workspace", "artifact_store", "authorize_phase"])
def test_real_app_rejects_wrong_concrete_reader_canonical_service(tmp_path, monkeypatch, mismatch):
    from tests.execution_worker.test_c2r_native_move_offline import (
        test_create_app_lifespan_native_composition_is_optional_and_canonical as composition_case,
    )
    composition_case(tmp_path, monkeypatch, True, native_v2=True, mismatch=mismatch)


@pytest.mark.parametrize("fault", [
    "deadline_expired", "phase_revoked", "cancelled",
    "status_changed", "approval_changed", "operation_changed",
])
def test_native_final_preflight_rejects_authority_change_during_catalog_io(tmp_path, monkeypatch, fault):
    from embedded_copilot.execution_worker.adapters import SafeProcessRunner
    from embedded_copilot.execution_worker.contracts import ApprovalStatus, CancellationRequest

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    operation = h["operation"]
    now = [NOW + timedelta(minutes=1)]
    h["reader"].clock = lambda: now[0]
    original_loop = SafeProcessRunner._execute
    original_revalidate = h["catalog"].revalidate_native_move_readback
    armed = [False]
    changed = []

    def preflight_loop(*args, **kwargs):
        with monkeypatch.context() as scoped:
            armed[0] = kwargs["preflight"]

            def revalidate_then_change(*values):
                current = original_revalidate(*values)
                if current and armed[0] and not changed:
                    changed.append(fault)
                    if fault == "deadline_expired":
                        now[0] = NOW + timedelta(minutes=5)
                    elif fault == "phase_revoked":
                        h["phases"].discard("NATIVE_READBACK")
                    elif fault == "cancelled":
                        h["execution"].request_cancellation(CancellationRequest(
                            request_id="fake-cancel-during-native-preflight",
                            tenant_id=operation.tenant_id, project_id=operation.project_id,
                            operation_id=operation.operation_id, requester_id=operation.requester_id,
                            reason="isolated preflight cancellation", requested_at=now[0],
                        ))
                    elif fault == "status_changed":
                        get_status = h["store"].get_status
                        scoped.setattr(h["store"], "get_status", lambda *keys: get_status(*keys).model_copy(
                            update={"status": OperationStatus.SUCCEEDED},
                        ))
                    elif fault == "approval_changed":
                        get_approval = h["store"].get_approval
                        scoped.setattr(h["store"], "get_approval", lambda *keys: get_approval(*keys).model_copy(
                            update={"status": ApprovalStatus.ISSUED},
                        ))
                    else:
                        get_operation = h["store"].get_operation
                        scoped.setattr(h["store"], "get_operation", lambda *keys: get_operation(*keys).model_copy(
                            update={"requester_id": "other-owner"},
                        ))
                return current

            scoped.setattr(h["catalog"], "revalidate_native_move_readback", revalidate_then_change)
            try:
                return original_loop(*args, **kwargs)
            finally:
                armed[0] = False

    monkeypatch.setattr(SafeProcessRunner, "_execute", staticmethod(preflight_loop))
    assert run(h, adapter_clock=lambda: now[0]).status is not OperationStatus.SUCCEEDED
    assert changed == [fault]
    assert h["reader"].runner.calls == []
    assert h["drc"].calls == 0
    receipt = h["store"].get_receipt(operation.tenant_id, operation.project_id, operation.operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert h["board"].read_bytes() == operation.production_context.changeset.entries[0].after_content.encode()


def test_native_thread_start_failure_reaps_process_and_keeps_applied_receipt(tmp_path, monkeypatch):
    from tests.execution_worker.test_safe_process_runner_compatibility import (
        FakePopen,
        install_thread_setup_failure,
    )

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    original = fake_native_popen(h, h["operation"])
    processes = []

    def popen(*args, **kwargs):
        canonical = original(*args, **kwargs)
        process = FakePopen(stdout=canonical.stdout.getvalue(), running=True)
        processes.append(process)
        return process

    h["fake_popen"] = popen
    install_thread_setup_failure(monkeypatch, "start_2")
    assert run(h).status is not OperationStatus.SUCCEEDED
    assert len(h["reader"].runner.calls) == len(processes) == 1
    assert processes[0].killed == 1 and processes[0].waits == [2]
    assert processes[0].stdout.closed and processes[0].stderr.closed
    assert h["drc"].calls == 0
    operation = h["operation"]
    receipt = h["store"].get_receipt(operation.tenant_id, operation.project_id, operation.operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
    assert h["board"].read_bytes() == operation.production_context.changeset.entries[0].after_content.encode()

@pytest.mark.parametrize("stage", ["catalog_scan", "after_catalog", "final_phase"])
@pytest.mark.parametrize("fault", ["old_revision", "old_review", "owner_changed"])
def test_native_preflight_rechecks_canonical_binding_after_late_io(tmp_path, monkeypatch, stage, fault):
    from embedded_copilot.execution_worker.adapters import SafeProcessRunner

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    operation = h["operation"]
    now = NOW + timedelta(minutes=1)
    h["reader"].clock = lambda: now
    original_loop = SafeProcessRunner._execute
    original_revalidate = h["catalog"].revalidate_native_move_readback
    original_scan = h["catalog"]._eda_import_source
    original_phase = h["reader"].authorize_phase
    changed = []
    armed = [False]
    catalog_done = [False]

    def preflight_loop(*args, **kwargs):
        with monkeypatch.context() as scoped:
            armed[0] = kwargs["preflight"]

            def change_binding():
                if changed:
                    return
                changed.append(fault)
                repository = h["catalog"]._repository
                if fault == "old_revision":
                    read = repository.eda_import_revision
                    scoped.setattr(repository, "eda_import_revision", lambda *a, **k: {**read(*a, **k), "active": False})
                elif fault == "old_review":
                    read = repository.eda_native_mapping_review
                    scoped.setattr(repository, "eda_native_mapping_review", lambda *a, **k: {**read(*a, **k), "decision": "REJECT_MAPPING"})
                else:
                    scoped.setattr(repository, "project_owner", lambda *a, **k: "other-owner")

            def scan_then_change(*args, **kwargs):
                result = original_scan(*args, **kwargs)
                if armed[0] and stage == "catalog_scan":
                    change_binding()
                return result

            def catalog_then_change(*args, **kwargs):
                result = original_revalidate(*args, **kwargs)
                if armed[0] and result:
                    catalog_done[0] = True
                    if stage == "after_catalog":
                        change_binding()
                return result

            def phase_then_change(*args, **kwargs):
                result = original_phase(*args, **kwargs)
                if armed[0] and catalog_done[0] and stage == "final_phase" and result:
                    change_binding()
                return result

            if stage == "catalog_scan":
                scoped.setattr(h["catalog"], "_eda_import_source", scan_then_change)
            else:
                scoped.setattr(h["catalog"], "revalidate_native_move_readback", catalog_then_change)
                if stage == "final_phase":
                    scoped.setattr(h["reader"], "authorize_phase", phase_then_change)
                    object.__setattr__(h["catalog"]._native_move_setup, "authorize_phase", phase_then_change)
            try:
                return original_loop(*args, **kwargs)
            finally:
                if stage == "final_phase":
                    object.__setattr__(h["catalog"]._native_move_setup, "authorize_phase", original_phase)
                armed[0] = False

    monkeypatch.setattr(SafeProcessRunner, "_execute", staticmethod(preflight_loop))
    assert run(h, adapter_clock=lambda: now).status is not OperationStatus.SUCCEEDED
    assert changed == [fault]
    assert h["reader"].runner.calls == [] and h["drc"].calls == 0
    receipt = h["store"].get_receipt(operation.tenant_id, operation.project_id, operation.operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"



def test_native_final_binding_read_phase_revoke_never_spawns(tmp_path, monkeypatch):
    from embedded_copilot.execution_worker.adapters import SafeProcessRunner

    h = harness(tmp_path, board_bytes=BOARD_V2, reader_id=READER_ID)
    operation = h["operation"]
    now = NOW + timedelta(minutes=1)
    h["reader"].clock = lambda: now
    original_loop = SafeProcessRunner._execute
    original_binding = h["catalog"]._native_move_readback_binding
    binding_reads = []
    revoked = []

    def preflight_loop(*args, **kwargs):
        with monkeypatch.context() as scoped:
            def binding_then_revoke(*values):
                current = original_binding(*values)
                if kwargs["preflight"] and current is not None:
                    binding_reads.append(current)
                    if len(binding_reads) == 4:
                        h["phases"].discard("NATIVE_READBACK")
                        revoked.append(True)
                return current

            scoped.setattr(h["catalog"], "_native_move_readback_binding", binding_then_revoke)
            return original_loop(*args, **kwargs)

    monkeypatch.setattr(SafeProcessRunner, "_execute", staticmethod(preflight_loop))
    assert run(h, adapter_clock=lambda: now).status is not OperationStatus.SUCCEEDED
    assert revoked == [True]
    assert h["reader"].runner.calls == [] and h["drc"].calls == 0
    receipt = h["store"].get_receipt(operation.tenant_id, operation.project_id, operation.operation_id)
    assert receipt.native_move_production.workspace_apply_status == "APPLIED"
    assert receipt.native_move_production.native_read_back.status == "UNKNOWN"
