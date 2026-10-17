from __future__ import annotations

import hashlib
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

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
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration, FirmwareBuildRequest, FirmwareSourceArtifact


PROJECT_ID = "project-cd1617ef-0029-4628-882b-a2cc4bf518dc"
CONVERSATION_ID = "conversation-ba96322b-c712-4485-9bfb-5901fa0d5878"
TENANT_ID = "tenant-embedded-copilot"
FP = "sha256:" + "4" * 64


class _CanonicalWorkspace:
    def __init__(self, root: Path) -> None:
        self._root = root

    def resolve(self, **_kwargs) -> Path:
        return self._root


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_source_paths(root: Path) -> tuple[Path, ...]:
    return (
        root / "CMakeLists.txt",
        root / "cmake" / "arm-none-eabi.cmake",
        root / "linker" / "STM32F103C8Tx_FLASH.ld",
        root / "generated" / "main.c",
        root / "generated" / "minimal_mcu_control_core.c",
        root / "generated" / "minimal_mcu_control_core.h",
        root / "vendor" / "st" / "STM32F1xx" / "Source" / "Templates" / "gcc" / "startup_stm32f103xb.s",
        root / "vendor" / "st" / "STM32F1xx" / "Source" / "Templates" / "system_stm32f1xx.c",
    )


def _canonical_design_input_paths(root: Path) -> tuple[Path, ...]:
    return (
        root / "docs" / "canonical-golden-facts.json",
        root / "hardware" / "main.kicad_pcb",
        root / "hardware" / "main.kicad_sch",
        root / "hardware" / "Minimal_MCU_Control_Core_BOM.csv",
    )


@pytest.mark.real_toolchain
def test_canonical_stm32_build_uses_real_toolchain_through_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("EMBRIX_RUN_REAL_TOOLCHAIN") != "1":
        pytest.skip("real toolchain acceptance is opt-in")
    project_root = Path(os.environ.get("EMBRIX_CANONICAL_PROJECT", r"D:\EmbeddedProjects\Minimal MCU Control Core"))
    toolchain_root = Path(os.environ.get("EMBRIX_TOOLCHAIN_ROOT", r"D:\EmbeddedToolchains\ArmGNU\15.3.rel1"))
    cmake = Path(os.environ.get("EMBRIX_CMAKE_EXECUTABLE", r"D:\EmbeddedToolchains\Auxiliary\cmake-4.3.5-windows-x86_64\bin\cmake.exe"))
    ninja = Path(os.environ.get("EMBRIX_NINJA_EXECUTABLE", r"D:\EmbeddedToolchains\Auxiliary\ninja-1.13.2\ninja.exe"))
    compiler = toolchain_root / "bin" / "arm-none-eabi-gcc.exe"
    if not project_root.is_dir() or not compiler.is_file() or not cmake.is_file() or not ninja.is_file():
        pytest.skip("canonical project and real GNU Arm/CMake/Ninja toolchain are required")

    monkeypatch.setenv("EMBRIX_CMAKE_EXECUTABLE", str(cmake))
    monkeypatch.setenv("EMBRIX_NINJA_EXECUTABLE", str(ninja))
    monkeypatch.setenv("PATH", os.pathsep.join((str(toolchain_root / "bin"), str(cmake.parent), str(ninja.parent), os.environ.get("PATH", ""))))

    source_paths = _canonical_source_paths(project_root)
    design_input_paths = _canonical_design_input_paths(project_root)
    before_hashes = {
        path.relative_to(project_root).as_posix(): _sha256(path)
        for path in (*source_paths, *design_input_paths)
    }
    source_artifacts = tuple(
        FirmwareSourceArtifact(
            relative_path=path.relative_to(project_root).as_posix(),
            content_fingerprint=_sha256(path),
            size_bytes=path.stat().st_size,
        )
        for path in source_paths
    )
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        profile="Release",
        build_directory="build",
        expected_artifact_types=("ELF", "BIN", "MAP"),
        output_stem="minimal_mcu_control_core",
    )
    request = FirmwareBuildRequest.create(
        project_id=PROJECT_ID,
        conversation_id=CONVERSATION_ID,
        firmware_reference="firmware-canonical-stm32f103c8t6",
        build_profile="Release",
        approval_reference=None,
        firmware_spec_id="firmware-spec-canonical-stm32f103c8t6",
        firmware_spec_fingerprint=FP,
        implementation_id="implementation-canonical-stm32f103c8t6",
        implementation_fingerprint=FP,
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        configuration=configuration,
        source_artifacts=source_artifacts,
    )

    discovery = TrustedToolDiscovery(
        trusted_roots=(toolchain_root,),
        trusted_versions={"ARM_NONE_EABI_GCC": "15.3.1"},
        explicit_paths={"ARM_NONE_EABI_GCC": compiler},
        version_probe=probe_trusted_tool_version,
    )
    registry = ExecutionToolRegistry(discovery)
    resolved = registry.resolve("ARM_NONE_EABI_GCC", target_kind="STM32F103C8T6", operation=OperationType.BUILD)
    assert resolved.status == "AVAILABLE"
    assert resolved.version_verified is True

    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    target_registry = SQLiteTargetRegistry(store)
    now = datetime.now(UTC)
    candidate = TargetDiscoveryCandidate(
        candidate_id="candidate-canonical-stm32f103c8t6",
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        target_kind="STM32F103C8T6",
        model="STM32F103C8T6",
        observed_identity_hash="observed-stm32f103c8t6",
        discovered_at=now,
    )
    store.save_candidate(candidate)
    target = ExecutionTarget(
        target_id="target-canonical-stm32f103c8t6",
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        target_kind="STM32F103C8T6",
        board_family="MINIMAL-MCU-CONTROL-CORE",
        mcu_family="STM32F1",
        stable_identity_hash="stable-stm32f103c8t6",
        candidate_id=candidate.candidate_id,
        binding_status=TargetBindingStatus.BOUND,
        bound_by="reviewer-canonical-build",
        bound_at=now,
    )
    target_registry.bind(target)

    execution = ExecutionOperationService(store=store)
    bridge = FirmwareBuildExecutionBridge(execution)
    operation_request = bridge.approve_and_enqueue(
        request,
        tenant_id=TENANT_ID,
        requester_id="requester-canonical-build",
        target_id=target.target_id,
        target_fingerprint=target.fingerprint,
        workspace_snapshot_id="snapshot-canonical-project",
        workspace_fingerprint=FP,
        proposal_id="proposal-canonical-build",
        proposal_fingerprint=FP,
        reviewer_id="reviewer-canonical-build",
        approval_nonce="approval-nonce-canonical-build",
        expires_at=now + timedelta(minutes=10),
    )

    artifact_store = ArtifactStore(tmp_path / "artifacts")
    worker = ExecutionWorker(
        worker_id="worker-canonical-real-build",
        store=store,
        registry=registry,
        workspace_resolver=_CanonicalWorkspace(project_root),
        targets=target_registry,
        artifact_store=artifact_store,
        build=FirmwareBuildToolAdapter(),
    )
    status = worker.run_once()
    assert status is not None
    assert status.status.value == "SUCCEEDED", status.safe_detail
    receipt = execution.get_receipt(tenant_id=TENANT_ID, project_id=PROJECT_ID, operation_id=operation_request.operation.operation_id)
    assert receipt is not None
    assert receipt.tool_execution_status == "SUCCEEDED"
    assert receipt.build_result is not None
    assert receipt.build_result.result_status.value == "BUILD_PASS"
    assert operation_request.operation.firmware_build_request is not None
    assert operation_request.operation.firmware_build_request.conversation_id == CONVERSATION_ID
    assert receipt.build_evidence is not None
    assert receipt.build_evidence.tool_observed is True
    assert receipt.build_evidence.build_system == "CMAKE"
    assert receipt.build_evidence.compiler_exit_code == 0
    assert receipt.build_evidence.assembler_exit_code == 0
    assert receipt.build_evidence.linker_exit_code == 0
    assert receipt.build_evidence.elf_verified is True
    assert receipt.build_evidence.bin_verified is True
    assert receipt.build_evidence.raw_evidence_artifact_id == receipt.build_evidence.log_artifact_id
    assert receipt.build_evidence.raw_evidence_artifact_id in receipt.build_evidence.output_artifact_ids
    raw_log_identity = store.get_artifact(TENANT_ID, PROJECT_ID, receipt.build_evidence.raw_evidence_artifact_id)
    assert raw_log_identity is not None
    raw_log = artifact_store.read(raw_log_identity).decode("utf-8")
    assert "stage=cmake_build" in raw_log
    assert "<PROJECT>" in raw_log
    assert {item.artifact_type for item in receipt.build_result.outputs} == {"ELF", "BIN", "MAP"}
    assert receipt.artifact is not None
    assert receipt.artifact.artifact_type == "ELF"
    for output in receipt.build_result.outputs:
        identity = store.get_artifact(TENANT_ID, PROJECT_ID, output.reference)
        assert identity is not None
        content = artifact_store.read(identity)
        assert "sha256:" + hashlib.sha256(content).hexdigest() == identity.artifact_sha256
        assert len(content) == identity.size_bytes
    assert store.get_artifact(TENANT_ID, PROJECT_ID, receipt.artifact.artifact_id) is not None
    assert before_hashes == {
        path.relative_to(project_root).as_posix(): _sha256(path)
        for path in (*source_paths, *design_input_paths)
    }


@pytest.mark.real_toolchain
def test_canonical_stm32_compile_failure_is_failed_closed_with_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get("EMBRIX_RUN_REAL_TOOLCHAIN") != "1":
        pytest.skip("real toolchain acceptance is opt-in")
    canonical_root = Path(os.environ.get("EMBRIX_CANONICAL_PROJECT", r"D:\EmbeddedProjects\Minimal MCU Control Core"))
    toolchain_root = Path(os.environ.get("EMBRIX_TOOLCHAIN_ROOT", r"D:\EmbeddedToolchains\ArmGNU\15.3.rel1"))
    cmake = Path(os.environ.get("EMBRIX_CMAKE_EXECUTABLE", r"D:\EmbeddedToolchains\Auxiliary\cmake-4.3.5-windows-x86_64\bin\cmake.exe"))
    ninja = Path(os.environ.get("EMBRIX_NINJA_EXECUTABLE", r"D:\EmbeddedToolchains\Auxiliary\ninja-1.13.2\ninja.exe"))
    compiler = toolchain_root / "bin" / "arm-none-eabi-gcc.exe"
    if not canonical_root.is_dir() or not compiler.is_file() or not cmake.is_file() or not ninja.is_file():
        pytest.skip("canonical project and real GNU Arm/CMake/Ninja toolchain are required")
    failure_root = tmp_path / "canonical-failure-project"
    shutil.copytree(
        canonical_root,
        failure_root,
        ignore=lambda _directory, names: [name for name in names if name in {"build", ".embedded-copilot-state"}],
    )
    (failure_root / "generated" / "main.c").write_text(
        "#include <stdint.h>\n\nint main(void) {\n    this_is_an_intentional_compiler_failure();\n    return 0;\n}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EMBRIX_CMAKE_EXECUTABLE", str(cmake))
    monkeypatch.setenv("EMBRIX_NINJA_EXECUTABLE", str(ninja))
    monkeypatch.setenv("PATH", os.pathsep.join((str(toolchain_root / "bin"), str(cmake.parent), str(ninja.parent), os.environ.get("PATH", ""))))

    source_paths = _canonical_source_paths(failure_root)
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        profile="Release",
        build_directory="build",
        expected_artifact_types=("ELF", "BIN", "MAP"),
        output_stem="minimal_mcu_control_core",
    )
    request = FirmwareBuildRequest.create(
        project_id=PROJECT_ID,
        conversation_id=CONVERSATION_ID,
        firmware_reference="firmware-canonical-stm32f103c8t6-failure",
        build_profile="Release",
        approval_reference=None,
        firmware_spec_id="firmware-spec-canonical-stm32f103c8t6-failure",
        firmware_spec_fingerprint=FP,
        implementation_id="implementation-canonical-stm32f103c8t6-failure",
        implementation_fingerprint=FP,
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        configuration=configuration,
        source_artifacts=tuple(
            FirmwareSourceArtifact(
                relative_path=path.relative_to(failure_root).as_posix(),
                content_fingerprint=_sha256(path),
                size_bytes=path.stat().st_size,
            )
            for path in source_paths
        ),
    )
    registry = ExecutionToolRegistry(
        TrustedToolDiscovery(
            trusted_roots=(toolchain_root,),
            trusted_versions={"ARM_NONE_EABI_GCC": "15.3.1"},
            explicit_paths={"ARM_NONE_EABI_GCC": compiler},
            version_probe=probe_trusted_tool_version,
        )
    )
    store = SQLiteExecutionStore(tmp_path / "failure-execution.sqlite3")
    target_registry = SQLiteTargetRegistry(store)
    now = datetime.now(UTC)
    candidate = TargetDiscoveryCandidate(
        candidate_id="candidate-canonical-stm32f103c8t6-failure",
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        target_kind="STM32F103C8T6",
        model="STM32F103C8T6",
        observed_identity_hash="observed-stm32f103c8t6-failure",
        discovered_at=now,
    )
    store.save_candidate(candidate)
    target = ExecutionTarget(
        target_id="target-canonical-stm32f103c8t6-failure",
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        target_kind="STM32F103C8T6",
        board_family="MINIMAL-MCU-CONTROL-CORE",
        mcu_family="STM32F1",
        stable_identity_hash="stable-stm32f103c8t6-failure",
        candidate_id=candidate.candidate_id,
        binding_status=TargetBindingStatus.BOUND,
        bound_by="reviewer-canonical-build",
        bound_at=now,
    )
    target_registry.bind(target)
    execution = ExecutionOperationService(store=store)
    operation_request = FirmwareBuildExecutionBridge(execution).approve_and_enqueue(
        request,
        tenant_id=TENANT_ID,
        requester_id="requester-canonical-build",
        target_id=target.target_id,
        target_fingerprint=target.fingerprint,
        workspace_snapshot_id="snapshot-canonical-failure-project",
        workspace_fingerprint=FP,
        proposal_id="proposal-canonical-build-failure",
        proposal_fingerprint=FP,
        reviewer_id="reviewer-canonical-build",
        approval_nonce="approval-nonce-canonical-build-failure",
        expires_at=now + timedelta(minutes=10),
    )
    status = ExecutionWorker(
        worker_id="worker-canonical-real-build-failure",
        store=store,
        registry=registry,
        workspace_resolver=_CanonicalWorkspace(failure_root),
        targets=target_registry,
        artifact_store=ArtifactStore(tmp_path / "failure-artifacts"),
        build=FirmwareBuildToolAdapter(),
    ).run_once()
    assert status is not None
    assert status.status.value == "FAILED"
    receipt = execution.get_receipt(tenant_id=TENANT_ID, project_id=PROJECT_ID, operation_id=operation_request.operation.operation_id)
    assert receipt is not None
    assert receipt.tool_execution_status == "FAILED"
    assert receipt.build_result is not None
    assert receipt.build_result.result_status.value == "BUILD_FAIL"
    assert receipt.build_evidence is not None
    assert receipt.build_evidence.tool_observed is True
    assert receipt.build_evidence.cmake_configure_exit_code == 0
    assert receipt.build_evidence.compiler_exit_code is not None
    assert receipt.build_evidence.raw_evidence_artifact_id == receipt.build_evidence.log_artifact_id
    assert receipt.build_evidence.raw_evidence_artifact_id in receipt.build_evidence.output_artifact_ids
    assert receipt.artifact is None
    assert receipt.build_result.log_artifact_reference is not None
    raw_log_identity = store.get_artifact(TENANT_ID, PROJECT_ID, receipt.build_result.log_artifact_reference)
    assert raw_log_identity is not None
    raw_log = ArtifactStore(tmp_path / "failure-artifacts").read(raw_log_identity).decode("utf-8")
    assert "stage=cmake_build" in raw_log
    assert "intentional_compiler_failure" in raw_log


def test_real_build_acceptance_and_adapter_have_no_shell_true_or_test_process_boundary() -> None:
    acceptance_source = Path(__file__).read_text(encoding="utf-8")
    adapter_source = (Path(__file__).parents[2] / "src" / "embedded_copilot" / "execution_worker" / "adapters.py").read_text(encoding="utf-8")
    process_token = "sub" + "process"
    shell_token = "shell" + "=True"
    assert process_token not in acceptance_source
    assert shell_token not in acceptance_source
    assert shell_token not in adapter_source
