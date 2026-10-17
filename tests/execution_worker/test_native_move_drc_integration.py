"""Collector composition/phase boundary checks; all process and DRC data are fake."""
from dataclasses import replace

import pytest

from embedded_copilot.execution_worker.contracts import OperationStatus
from embedded_copilot.execution_worker.native_move import NativeReadbackPhase
from embedded_copilot.execution_worker.native_move_drc_collector import (
    NativeMoveDrcCollector,
)
from tests.execution_worker.test_c2r_native_move_offline import harness, run


@pytest.mark.parametrize("ready", (True, False))
def test_real_collector_composition_requires_readiness_before_apply_and_passes_phase(tmp_path, ready):
    h = harness(tmp_path)
    # Deliberate test seam: exercises composition and Worker phase, not real closure proof.
    collector = object.__new__(NativeMoveDrcCollector)
    collector.workspace = h["workspace"]
    collector.artifact_store = h["artifacts"]
    collector.authorize_phase = h["setup"].authorize_phase
    collector.ready_for = lambda policy: ready
    calls = []
    def compare(operation, board, policy, phase):
        assert type(phase) is NativeReadbackPhase
        assert phase.policy_timeout_seconds == policy.timeout_seconds
        assert phase.deadline <= operation.expires_at
        assert not phase.is_cancelled() and phase.timeout_seconds() >= 1
        calls.append(operation.operation_id)
        return h["drc"].compare(operation, board, policy)
    collector.compare = compare
    h["setup"] = replace(h["setup"], drc=collector)
    original = h["board"].read_bytes()
    status = run(h)
    if ready:
        assert status.status is OperationStatus.SUCCEEDED and len(calls) == 1
        receipt = h["store"].get_receipt(h["operation"].tenant_id, h["operation"].project_id, h["operation"].operation_id)
        assert receipt.native_move_production.workspace_apply_status == "APPLIED"
        assert receipt.native_move_production.drc_result.status == "NO_NEW_KEYS"
        assert receipt.native_move_production.drc_board_pass is False
    else:
        assert status.status is not OperationStatus.SUCCEEDED and calls == []
        assert h["board"].read_bytes() == original and h["reader"].calls == 0


@pytest.mark.parametrize("mismatch", (None, "store", "catalog"))
def test_create_app_binds_real_drc_to_canonical_store_and_catalog(tmp_path, monkeypatch, mismatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from embedded_copilot.api import main
    from embedded_copilot.services.config import Settings

    h = harness(tmp_path)
    collector = object.__new__(NativeMoveDrcCollector)
    collector.workspace = h["workspace"]
    collector.artifact_store = h["artifacts"]
    collector.authorize_phase = h["setup"].authorize_phase
    collector.store = h["store"]
    collector.catalog = h["catalog"]
    collector.ready_for = lambda policy: True
    collector.compare = lambda *args: pytest.fail("composition must not dispatch DRC")
    if mismatch:
        setattr(collector, mismatch, object())
    setup = replace(h["setup"], drc=collector)
    captured = []
    actual_worker = main.ExecutionWorker
    def capture_worker(**kwargs):
        worker = actual_worker(**kwargs)
        captured.append(worker)
        return worker
    monkeypatch.setattr(main, "ExecutionWorker", capture_worker)
    for name in ("discover_kicad_cli", "discover_kicad_footprint_root", "discover_kicad_symbol_root",
                 "discover_kicad_pcb_drc_command", "discover_kicad_pcb_parse_command"):
        monkeypatch.setattr(main, name, lambda *args, **kwargs: None)
    def no_probe(*args, **kwargs):
        pytest.fail("composition must not probe a real tool")
    monkeypatch.setattr(main, "probe_kicad_cli_version", no_probe)
    monkeypatch.setattr(main, "probe_trusted_tool_version", no_probe)
    def no_discovery(*args, **kwargs):
        raise RuntimeError("tool discovery disabled")
    monkeypatch.setattr(main, "TrustedToolDiscovery", no_discovery)
    monkeypatch.setattr(main.chromadb, "PersistentClient", lambda *args, **kwargs: object())
    before = h["board"].read_bytes()
    app = main.create_app(
        settings=Settings(environment="test", desktop_state_database=tmp_path / "app.sqlite3",
                          project_root=tmp_path / "projects", _env_file=None),
        project_catalog_runtime=h["catalog"], execution_operation_port=h["execution"],
        workspace_apply_port=SimpleNamespace(_service=h["workspace"]), native_move_setup=setup,
    )
    if mismatch:
        with pytest.raises(ValueError, match="native DRC canonical"), TestClient(app):
            pytest.fail("non-canonical DRC composition accepted")
        assert captured == []
    else:
        with TestClient(app):
            assert app.state.native_move_production_available is True
            assert len(captured) == 1 and captured[0]._native_move_adapter._setup is setup
    assert h["board"].read_bytes() == before
