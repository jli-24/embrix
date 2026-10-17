from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.engineering_consistency import (
    CanonicalProjectConsistencyAdapter,
    ConsistencyOverallStatus,
    HardwareFirmwareConsistencyService,
    VerificationLevel,
)
from embedded_copilot.execution_worker.adapters import FirmwareBuildToolAdapter, probe_trusted_tool_version
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ExecutionTarget,
    OperationType,
    TargetBindingStatus,
    TargetDiscoveryCandidate,
)
from embedded_copilot.execution_worker.firmware_build_bridge import FirmwareBuildExecutionBridge
from embedded_copilot.execution_worker.registry import ExecutionToolRegistry, TrustedToolDiscovery
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.targets import SQLiteTargetRegistry
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.firmware_engineering import FirmwareBuildRequest, FirmwareSourceArtifact
from embedded_copilot.project_catalog import FirmwareProductionStatus, ProjectCatalogRuntime
from scripts.bootstrap_canonical_golden_project import GOLDEN_NAME, REQUESTER, TENANT_ID, bootstrap
from tests.production_acceptance.kicad_toolchain import discover_test_kicad_cli, discover_test_symbol_root


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _source_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        root / path
        for path in (
            "CMakeLists.txt",
            "cmake/arm-none-eabi.cmake",
            "linker/STM32F103C8Tx_FLASH.ld",
            "generated/main.c",
            "generated/minimal_mcu_control_core.c",
            "generated/minimal_mcu_control_core.h",
            "vendor/st/STM32F1xx/Source/Templates/gcc/startup_stm32f103xb.s",
            "vendor/st/STM32F1xx/Source/Templates/system_stm32f1xx.c",
        )
    )


class _CanonicalWorkspace:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve(self, **_kwargs) -> Path:
        return self._root


@pytest.mark.real_toolchain
def test_canonical_g6_projects_build_receipt_consistency_and_stale_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("EMBRIX_RUN_REAL_TOOLCHAIN") != "1":
        pytest.skip("real toolchain acceptance is opt-in")
    kicad_cli = discover_test_kicad_cli()
    symbol_dir = None if kicad_cli is None else discover_test_symbol_root(kicad_cli)
    toolchain_root = Path(os.environ.get("EMBRIX_TOOLCHAIN_ROOT", r"D:\EmbeddedToolchains\ArmGNU\15.3.rel1"))
    toolchain_bin = toolchain_root / "bin"
    cmake = Path(os.environ.get("EMBRIX_CMAKE_EXECUTABLE", r"D:\EmbeddedToolchains\Auxiliary\cmake-4.3.5-windows-x86_64\bin\cmake.exe"))
    ninja = Path(os.environ.get("EMBRIX_NINJA_EXECUTABLE", r"D:\EmbeddedToolchains\Auxiliary\ninja-1.13.2\ninja.exe"))
    compiler = toolchain_bin / "arm-none-eabi-gcc.exe"
    if kicad_cli is None or symbol_dir is None or not compiler.is_file() or not cmake.is_file() or not ninja.is_file():
        pytest.skip("canonical KiCad and real GNU Arm/CMake/Ninja toolchain are required")

    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(symbol_dir))
    monkeypatch.setenv("EMBRIX_CANONICAL_TOOLCHAIN_VERSION", probe_trusted_tool_version(compiler))
    monkeypatch.setenv("EMBRIX_CMAKE_EXECUTABLE", str(cmake))
    monkeypatch.setenv("EMBRIX_NINJA_EXECUTABLE", str(ninja))
    project_root = tmp_path / "projects"
    state_database = tmp_path / "catalog.sqlite3"
    bootstrap(project_root=project_root, state_database=state_database, executable=str(kicad_cli))
    canonical_source = Path(os.environ.get("EMBRIX_CANONICAL_PROJECT", r"D:\EmbeddedProjects\Minimal MCU Control Core"))
    if not canonical_source.is_dir():
        pytest.skip("canonical production scaffold is required")
    # The ProjectCatalog is the isolated state owner for this acceptance test;
    # the trusted canonical production workspace remains the real build target.
    golden_root = canonical_source

    catalog = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=state_database,
        initialize_async=False,
        production_mode=True,
    )
    project = catalog.projects(TENANT_ID)[0]
    detail = catalog.conversation(project.project_id, next(iter(catalog._repository.conversations(project.project_id, TENANT_ID, REQUESTER))).conversation_id, tenant_id=TENANT_ID, requester=REQUESTER)
    assert detail.firmware_spec is not None
    assert detail.design_truth is not None
    assert detail.implementation_spec is not None
    assert detail.build_configuration is not None

    source_artifacts = tuple(
        FirmwareSourceArtifact(
            relative_path=path.relative_to(golden_root).as_posix(),
            content_fingerprint=_sha256(path),
            size_bytes=path.stat().st_size,
        )
        for path in _source_paths(golden_root)
    )
    source_revision = "sha256:" + hashlib.sha256(
        "|".join(item.content_fingerprint for item in source_artifacts).encode()
    ).hexdigest()
    request = FirmwareBuildRequest.create(
        project_id=project.project_id,
        conversation_id=detail.conversation_id,
        firmware_reference=detail.firmware_spec.firmware_spec_id,
        build_profile=detail.build_configuration.profile,
        approval_reference=None,
        firmware_spec_id=detail.firmware_spec.firmware_spec_id,
        firmware_spec_fingerprint=detail.firmware_spec.fingerprint,
        implementation_id=detail.implementation_spec.implementation_id,
        implementation_fingerprint=detail.implementation_spec.fingerprint,
        target_kind=detail.build_configuration.target_kind,
        build_system=detail.build_configuration.build_system,
        toolchain_id=detail.build_configuration.toolchain_id,
        toolchain_version=detail.build_configuration.toolchain_version,
        configuration=detail.build_configuration,
        source_artifacts=source_artifacts,
        expected_source_revision=source_revision,
        actual_source_revision=source_revision,
    )

    execution_store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    targets = SQLiteTargetRegistry(execution_store)
    now = datetime.now(UTC)
    candidate = TargetDiscoveryCandidate(
        candidate_id="candidate-g6-canonical",
        tenant_id=TENANT_ID,
        project_id=project.project_id,
        target_kind="STM32F103C8T6",
        model="STM32F103C8T6",
        observed_identity_hash="observed-g6-canonical",
        discovered_at=now,
    )
    execution_store.save_candidate(candidate)
    target = ExecutionTarget(
        target_id="target-g6-canonical",
        tenant_id=TENANT_ID,
        project_id=project.project_id,
        target_kind="STM32F103C8T6",
        board_family="MINIMAL-MCU-CONTROL-CORE",
        mcu_family="STM32F1",
        stable_identity_hash="stable-g6-canonical",
        candidate_id=candidate.candidate_id,
        binding_status=TargetBindingStatus.BOUND,
        bound_by=REQUESTER,
        bound_at=now,
    )
    targets.bind(target)
    execution = ExecutionOperationService(store=execution_store)
    operation_request = FirmwareBuildExecutionBridge(execution).approve_and_enqueue(
        request,
        tenant_id=TENANT_ID,
        requester_id=REQUESTER,
        target_id=target.target_id,
        target_fingerprint=target.fingerprint,
        workspace_snapshot_id="snapshot-g6-canonical",
        workspace_fingerprint="sha256:" + "6" * 64,
        proposal_id="proposal-g6-canonical",
        proposal_fingerprint="sha256:" + "7" * 64,
        reviewer_id=REQUESTER,
        approval_nonce="approval-g6-canonical",
        expires_at=now + timedelta(minutes=10),
    )
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    registry = ExecutionToolRegistry(
        TrustedToolDiscovery(
            trusted_roots=(toolchain_root,),
            trusted_versions={"ARM_NONE_EABI_GCC": probe_trusted_tool_version(compiler)},
            explicit_paths={"ARM_NONE_EABI_GCC": compiler},
            version_probe=probe_trusted_tool_version,
        )
    )
    worker = ExecutionWorker(
        worker_id="worker-g6-canonical",
        store=execution_store,
        registry=registry,
        workspace_resolver=_CanonicalWorkspace(golden_root),
        targets=targets,
        artifact_store=artifact_store,
        build=FirmwareBuildToolAdapter(),
    )
    status = worker.run_once()
    assert status is not None and status.status.value == "SUCCEEDED", status.safe_detail if status else "missing status"
    receipt = execution.get_receipt(
        tenant_id=TENANT_ID,
        project_id=project.project_id,
        operation_id=operation_request.operation.operation_id,
    )
    assert receipt is not None
    recorded = catalog.record_firmware_build(operation=operation_request.operation, receipt=receipt)
    assert recorded.firmware_production is not None
    assert recorded.firmware_production.status is FirmwareProductionStatus.BUILD_PASS
    assert recorded.firmware_production.build_receipt.raw_evidence_artifact_id is not None

    consistency_input = CanonicalProjectConsistencyAdapter(
        golden_root,
        project_id=project.project_id,
        conversation_id=detail.conversation_id,
        toolchain_bin=toolchain_bin,
        firmware_specification=detail.firmware_spec,
        simulation_specification=detail.simulation_spec,
        verification_specification=detail.verification_spec,
        implementation_specification=detail.implementation_spec,
        build_configuration=detail.build_configuration,
        design_truth_id=detail.design_truth.truth_id,
        design_truth_fingerprint=detail.design_truth.fingerprint,
    ).collect()
    consistency = HardwareFirmwareConsistencyService().evaluate(consistency_input)
    assert consistency.overall_status is ConsistencyOverallStatus.CONSISTENT
    assert consistency.verification_level is VerificationLevel.L3_ENGINEERING_VERIFIED
    gated = catalog.record_firmware_consistency(result=consistency)
    assert gated.firmware_production is not None
    assert gated.firmware_production.status is FirmwareProductionStatus.FIRMWARE_GATE_PASS

    restarted = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=state_database,
        initialize_async=False,
        production_mode=True,
    )
    restored = restarted.conversation(project.project_id, detail.conversation_id, tenant_id=TENANT_ID, requester=REQUESTER)
    assert restored.firmware_production is not None
    assert restored.firmware_production.status is FirmwareProductionStatus.FIRMWARE_GATE_PASS
    stale = restarted.mark_firmware_production_stale(
        project.project_id,
        detail.conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
        reason="controlled upstream change",
    )
    assert stale.firmware_production is not None
    assert stale.firmware_production.status is FirmwareProductionStatus.STALE
    assert stale.firmware_production.build_receipt.receipt_id == restored.firmware_production.build_receipt.receipt_id
