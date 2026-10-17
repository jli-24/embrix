from __future__ import annotations

import ast
from pathlib import Path

import pytest

from embedded_copilot.simulation_runtime.adapters import (
    FakeSimulationAdapter,
    QemuSimulationAdapter,
    RenodeSimulationAdapter,
    WokwiSimulationAdapter,
)
from embedded_copilot.simulation_runtime.contracts import (
    SimulationBackend,
    SimulationCapabilityStatus,
    SimulationExecutionMode,
)
from embedded_copilot.simulation_runtime.exceptions import SimulationUnavailable
from embedded_copilot.simulation_runtime.ports import SimulationAdapterPort

PACKAGE = Path("src/embedded_copilot/simulation_runtime")


@pytest.mark.parametrize(
    ("adapter", "backend", "mode", "reason"),
    (
        (
            RenodeSimulationAdapter(),
            SimulationBackend.RENODE,
            SimulationExecutionMode.LOCAL_PROCESS,
            "RENODE_NOT_INSTALLED",
        ),
        (
            QemuSimulationAdapter(),
            SimulationBackend.QEMU,
            SimulationExecutionMode.LOCAL_PROCESS,
            "QEMU_NOT_AVAILABLE",
        ),
        (
            WokwiSimulationAdapter(),
            SimulationBackend.WOKWI,
            SimulationExecutionMode.EXTERNAL_CLOUD,
            "WOKWI_NOT_CONFIGURED",
        ),
    ),
)
def test_disabled_backend_conformance(adapter, backend, mode, reason) -> None:
    assert isinstance(adapter, SimulationAdapterPort)

    capability = adapter.probe()

    assert capability.backend is backend
    assert capability.status is SimulationCapabilityStatus.UNAVAILABLE
    assert capability.execution_mode is mode
    assert capability.version is None
    assert capability.supported_targets == ()
    assert capability.reason_code == reason
    with pytest.raises(SimulationUnavailable, match=reason):
        adapter.prepare(object())


def test_fake_backend_is_verified_only_for_test_execution() -> None:
    capability = FakeSimulationAdapter().probe()

    assert capability.backend is SimulationBackend.FAKE
    assert capability.status is SimulationCapabilityStatus.VERIFIED
    assert capability.execution_mode is SimulationExecutionMode.TEST_ONLY
    assert capability.reason_code == "FAKE_TEST_EXECUTION_VERIFIED"


def test_simulation_runtime_has_no_process_network_or_database_bypass() -> None:
    forbidden_imports = {
        "subprocess",
        "socket",
        "sqlite3",
        "requests",
        "httpx",
        "urllib",
    }
    forbidden_calls = {"eval", "exec", "open", "compile", "__import__"}
    for path in PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".", 1)[0] not in forbidden_imports
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in forbidden_imports
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls


def test_no_simulation_agent_is_added_and_operation_is_first_class() -> None:
    forbidden_classes = {
        "SimulationAgent",
        "VirtualHardwareAgent",
        "LabAgent",
        "RenodeAgent",
        "QEMUAgent",
        "WokwiAgent",
    }
    classes: set[str] = set()
    for path in Path("src/embedded_copilot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes.update(
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        )
    assert not (classes & forbidden_classes)

    contracts = Path("src/embedded_copilot/execution_worker/contracts.py").read_text(
        encoding="utf-8"
    )
    assert 'SIMULATION = "SIMULATION"' in contracts
