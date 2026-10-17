"""Offline construction and existing FastAPI consumer checks for the native move seam."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from embedded_copilot.api import main
from embedded_copilot.core.config import Settings
from embedded_copilot.execution_worker.adapters import SafeProcessRunner
from embedded_copilot.execution_worker.contracts import NativeMoveReaderPolicy
from embedded_copilot.execution_worker.native_move import NativeMoveProductionAdapter
from embedded_copilot.execution_worker.native_move_composition import (
    build_native_move_production_setup,
)
from embedded_copilot.execution_worker.native_move_projection import READER_ID
from tests.execution_worker.test_c2r_native_move_offline import (
    Clock,
    fake_native_bundle,
)
from tests.execution_worker.test_native_move_drc_collector import _setup


def _inputs(tmp_path: Path):
    case = _setup(tmp_path)
    h = case.h
    bundle = fake_native_bundle(tmp_path)
    reader_policy = NativeMoveReaderPolicy(
        reader_id=READER_ID,
        reader_version=bundle.reader_version,
        executable_sha256=next(
            item.sha256 for item in bundle.files if item.role == "INTERPRETER"
        ),
        timeout_seconds=30,
    )
    side_effect_policy = h["setup"].side_effect_policy

    def validate_policies(reader, drc, side_effect):
        return (
            reader == reader_policy
            and drc == case.policy
            and side_effect == side_effect_policy
        )

    def authorize_phase(phase, operation, approval, now):
        return (
            phase in {"APPLY", "NATIVE_READBACK", "DRC"}
            and operation == case.operation
            and approval is not None
            and now < operation.expires_at
        )

    values = {
        "reader_policy": reader_policy,
        "drc_policy": case.policy,
        "side_effect_policy": side_effect_policy,
        "readback_bundle": bundle,
        "tool_bundle": case.collector.tool_bundle,
        "rules": case.collector.rules,
        "dependencies": case.collector.dependencies,
        "environment": case.collector.environment,
        "report_root": case.collector.report_root,
        "store": h["store"],
        "catalog": h["catalog"],
        "workspace": h["workspace"],
        "artifact_store": h["artifacts"],
        "runner": SafeProcessRunner(),
        "clock": Clock().now,
        "validate_policies": validate_policies,
        "authorize_phase": authorize_phase,
    }
    return case, values


def test_factory_binds_canonical_dependencies_without_running_native_tools(
    tmp_path, monkeypatch
):
    case, values = _inputs(tmp_path)
    monkeypatch.setattr(
        values["runner"],
        "run_native_readback",
        lambda *a, **k: pytest.fail("reader launched"),
    )
    monkeypatch.setattr(
        values["runner"],
        "run_drc_baseline",
        lambda *a, **k: pytest.fail("DRC launched"),
    )

    setup = build_native_move_production_setup(**values)

    assert setup.available()
    assert setup.reader.ready_for(values["reader_policy"])
    assert setup.drc.ready_for(values["drc_policy"])
    for port in (setup.reader, setup.drc):
        assert port.store is values["store"]
        assert port.catalog is values["catalog"]
        assert port.workspace is values["workspace"]
        assert port.artifact_store is values["artifact_store"]
        assert port.runner is values["runner"]
        assert port.authorize_phase is setup.authorize_phase
    assert setup.reader.bundle is values["readback_bundle"]
    assert setup.drc.tool_bundle is values["tool_bundle"]
    assert setup.drc.environment is values["environment"]
    assert not setup.authorize_phase("UNKNOWN", case.operation, object(), Clock().now())


@pytest.mark.parametrize("missing", ["validate_policies", "authorize_phase"])
def test_factory_rejects_missing_authority(tmp_path, missing):
    _, values = _inputs(tmp_path)
    values[missing] = None
    with pytest.raises(ValueError, match="inputs are unavailable"):
        build_native_move_production_setup(**values)


def test_factory_rejects_policy_validator_denial(tmp_path):
    _, values = _inputs(tmp_path)

    def deny_policies(reader, drc, side_effect):
        return False

    values["validate_policies"] = deny_policies
    with pytest.raises(ValueError, match="composition is unavailable"):
        build_native_move_production_setup(**values)


def test_factory_rejects_baseline_or_closure_mismatch(tmp_path):
    case, values = _inputs(tmp_path)
    old_baseline = case.h["setup"].drc_policy.baseline_report
    values["drc_policy"] = case.policy.model_copy(
        update={"baseline_report": old_baseline}
    )

    def validate_changed_policy(reader, drc, side_effect):
        return (
            reader == values["reader_policy"]
            and drc == values["drc_policy"]
            and side_effect == values["side_effect_policy"]
        )

    values["validate_policies"] = validate_changed_policy
    with pytest.raises(ValueError, match="composition is unavailable"):
        build_native_move_production_setup(**values)

    values["drc_policy"] = case.policy
    case.collector.tool_bundle[0].absolute_path.write_bytes(b"drifted executable")
    with pytest.raises(ValueError, match="composition is unavailable"):
        build_native_move_production_setup(**values)


@pytest.mark.parametrize(
    "mismatch",
    [
        None,
        "reader_store",
        "reader_catalog",
        "drc_store",
        "drc_catalog",
        "workspace",
        "artifact_store",
    ],
)
def test_factory_setup_obeys_existing_app_canonical_identity_boundary(
    tmp_path, monkeypatch, mismatch
):
    case, values = _inputs(tmp_path)
    setup = build_native_move_production_setup(**values)
    h = case.h
    if mismatch in {"workspace", "artifact_store"}:
        setup = replace(setup, **{mismatch: object()})
    elif mismatch is not None:
        port_name, field_name = mismatch.split("_", 1)
        setattr(getattr(setup, port_name), field_name, object())
    for name in (
        "discover_kicad_cli",
        "discover_kicad_footprint_root",
        "discover_kicad_symbol_root",
        "discover_kicad_pcb_drc_command",
        "discover_kicad_pcb_parse_command",
    ):
        monkeypatch.setattr(main, name, lambda *a, **k: None)
    monkeypatch.setattr(
        main, "probe_kicad_cli_version", lambda *a, **k: pytest.fail("tool probe")
    )
    monkeypatch.setattr(
        main, "probe_trusted_tool_version", lambda *a, **k: pytest.fail("tool probe")
    )

    def unavailable_tool_discovery(*args, **kwargs):
        raise RuntimeError("tool discovery disabled")

    monkeypatch.setattr(main, "TrustedToolDiscovery", unavailable_tool_discovery)
    monkeypatch.setattr(main.chromadb, "PersistentClient", lambda *a, **k: object())
    captured = []
    actual_worker = main.ExecutionWorker

    def capture_worker(**kwargs):
        worker = actual_worker(**kwargs)
        captured.append(worker)
        return worker

    monkeypatch.setattr(main, "ExecutionWorker", capture_worker)
    app = main.create_app(
        settings=Settings(
            environment="test",
            desktop_state_database=tmp_path / "app.sqlite3",
            project_root=tmp_path / "projects",
            _env_file=None,
        ),
        project_catalog_runtime=h["catalog"],
        execution_operation_port=h["execution"],
        workspace_apply_port=SimpleNamespace(_service=h["workspace"]),
        native_move_setup=setup,
    )
    if mismatch is not None:
        expected_error = (
            "native reader canonical composition is unavailable"
            if mismatch in {"reader_store", "reader_catalog"}
            else "native move production composition is unavailable"
        )
        with pytest.raises(ValueError, match=expected_error), TestClient(app):
            pytest.fail("mismatched canonical composition accepted")
        assert captured == []
    else:
        with TestClient(app):
            assert app.state.native_move_production_available is True
            assert h["catalog"]._native_move_setup is setup
            assert len(captured) == 1
            assert isinstance(
                captured[0]._native_move_adapter, NativeMoveProductionAdapter
            )
            assert captured[0]._native_move_adapter._setup is setup
