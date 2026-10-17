from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.adapters import SafeProcessRunner
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.simulation_runtime.renode import (
    RenodeGPIOConfig,
    RenodeSimulationAdapter,
)
from embedded_copilot.verification_loop import (
    BackendAvailability,
    BackendProbeStatus,
    BackendTrustStatus,
    G9AuthoritySnapshot,
    G9GateStatus,
    G9Scope,
    VirtualBackendDescriptor,
    VirtualBackendPool,
    VirtualNeedScope,
    VirtualSystemVerificationService,
    backend_descriptor_from_capability,
    backend_from_capability,
)
from embedded_copilot.virtual_assembly import (
    CanonicalVirtualAssemblyAdapter,
    VirtualAssemblyService,
)
from tests.production_acceptance.test_real_functional_verification_golden import (
    ELF,
    NOW,
    PLATFORM,
    PROJECT_ID,
    PROJECT_ROOT,
    RENODE_EXE,
    STATE_DB,
    TOOLCHAIN_BIN,
    _run_real_renode,
)

CONVERSATION_ID = "conversation-ba96322b-c712-4485-9bfb-5901fa0d5878"
G8_MANIFEST = PROJECT_ROOT / ".embedded-copilot-state" / "manufacturing-g8-current-b1ecfb88d1991bbfe3c5-r2" / "manifest.json"


def _manifest_state() -> tuple[dict[str, object], bytes, str]:
    raw = G8_MANIFEST.read_bytes()
    manifest = json.loads(raw)
    return manifest, raw, manifest["manifest_fingerprint"]


def _note(manifest: dict[str, object], prefix: str) -> str:
    notes = manifest.get("notes", ())
    value = next((item for item in notes if str(item).startswith(prefix)), None)
    if value is None:
        raise AssertionError(f"G8 manifest note is missing: {prefix}")
    return str(value).split("=", 1)[1]


def _authority(detail, source, manifest: dict[str, object]) -> G9AuthoritySnapshot:
    consistency = detail.firmware_production.consistency_result
    scenario = source.consistency_input.simulation_specification.scenarios[0]
    case = source.consistency_input.verification_specification.cases[0]
    if consistency is None:
        raise AssertionError("canonical firmware consistency result is unavailable")
    if manifest["pcb_hash"] != consistency.pcb_fingerprint:
        raise AssertionError("G8 PCB fingerprint is not the current firmware consistency PCB")
    return G9AuthoritySnapshot.create(
        project_id=PROJECT_ID,
        conversation_id=CONVERSATION_ID,
        hardware_identity=consistency.design_truth_id,
        board_identity=consistency.pcb_artifact_id,
        mcu_identity=source.consistency_input.truth.mcu_exact_part,
        current_pcb_fingerprint=consistency.pcb_fingerprint,
        current_pcb_state_fingerprint=_note(manifest, "pcb_state_fingerprint="),
        firmware_spec_fingerprint=consistency.firmware_specification_fingerprint,
        firmware_implementation_fingerprint=consistency.implementation_fingerprint,
        build_configuration_fingerprint=consistency.build_configuration_fingerprint,
        build_receipt_fingerprint=detail.firmware_production.build_receipt.fingerprint,
        elf_fingerprint=consistency.elf_fingerprint,
        bin_fingerprint=consistency.bin_fingerprint,
        hw_fw_consistency_fingerprint=consistency.fingerprint,
        requirement_refs=tuple(dict.fromkeys((*case.related_requirement_refs, *scenario.acceptance_refs))),
        interface_set=("GPIO", "PA8", "PB0", "SWD"),
        expected_system_behaviors=tuple(
            f"{input_state} -> {output_state}"
            for input_state, output_state in zip(
                scenario.inputs,
                scenario.expected_outputs,
                strict=True,
            )
        ),
        g8_manifest_fingerprint=manifest["manifest_fingerprint"],
    )


def _renode_backends():
    adapter = RenodeSimulationAdapter(
        executable=RENODE_EXE,
        platform_file=PLATFORM,
        gpio=RenodeGPIOConfig(
            target_kind="STM32F103C8T6",
            platform_descriptor="stm32f103",
            led_port="A",
            led_pin=8,
            button_port="B",
            button_pin=0,
        ),
        runner=SafeProcessRunner(),
    )
    capability = adapter.probe()
    current = backend_from_capability(
        capability,
        path_or_runtime="renode-1.16.1-runtime",
        discovery_source="trusted_tool_discovery",
    )
    descriptor = backend_descriptor_from_capability(
        capability,
        backend_id="renode",
        tool_path="renode.exe",
        supported_scopes=(
            VirtualNeedScope.BOOT,
            VirtualNeedScope.CPU_RUNTIME,
            VirtualNeedScope.GPIO,
            VirtualNeedScope.UART,
            VirtualNeedScope.TIMER,
            VirtualNeedScope.INTERRUPT,
            VirtualNeedScope.SYSTEM_BEHAVIOR,
        ),
        supported_capabilities=(
            "elf_loaded",
            "reset_handler_reached",
            "main_reached",
            "cpu_started",
            "gpio_configuration",
            "gpio_transition",
            "state_transition",
        ),
        limitations=("PARTIAL_RCC", "virtual-only"),
        last_probe_at=NOW,
    )
    return current, descriptor


def _registered_pool(renode_descriptor: VirtualBackendDescriptor) -> VirtualBackendPool:
    host_harness = VirtualBackendDescriptor.create(
        backend_id="host-harness",
        backend_type="HOST_HARNESS",
        tool_name="deterministic-host-harness",
        tool_path="host-harness-runtime",
        tool_version=None,
        availability=BackendAvailability.KNOWN_NOT_CONNECTED,
        trust_status=BackendTrustStatus.UNAVAILABLE,
        supported_targets=("STM32F103C8T6",),
        supported_architectures=("ARM Cortex-M3",),
        supported_scopes=(
            VirtualNeedScope.SYSTEM_BEHAVIOR,
            VirtualNeedScope.PROTOCOL,
            VirtualNeedScope.FAULT_RECOVERY,
        ),
        supported_capabilities=("state_transition", "protocol_trace", "fault_recovery"),
        limitations=("runtime is not connected in this canonical acceptance",),
        discovery_source="registered_descriptor",
        probe_status=BackendProbeStatus.NOT_PROBED,
        last_probe_at=NOW,
    )
    ngspice = VirtualBackendDescriptor.create(
        backend_id="ngspice",
        backend_type="SPICE_ANALOG_SIMULATOR",
        tool_name="ngspice",
        tool_path="ngspice-runtime",
        tool_version=None,
        availability=BackendAvailability.NOT_APPLICABLE,
        trust_status=BackendTrustStatus.UNAVAILABLE,
        supported_targets=(),
        supported_architectures=(),
        supported_scopes=(VirtualNeedScope.ANALOG_POWER,),
        supported_capabilities=("trusted_spice_model",),
        limitations=("no trusted SPICE model is part of the current project authority",),
        discovery_source="registered_descriptor",
        probe_status=BackendProbeStatus.NOT_PROBED,
        last_probe_at=NOW,
    )
    future = tuple(
        VirtualBackendDescriptor.create(
            backend_id=backend_id,
            backend_type="KNOWN_FUTURE_BACKEND",
            tool_name=backend_id,
            tool_path=f"{backend_id}-runtime",
            tool_version=None,
            availability=BackendAvailability.KNOWN_NOT_CONNECTED,
            trust_status=BackendTrustStatus.UNAVAILABLE,
            supported_targets=(),
            supported_architectures=(),
            supported_scopes=(),
            supported_capabilities=(),
            limitations=("registered but not connected for this G9",),
            discovery_source="registered_descriptor",
            probe_status=BackendProbeStatus.NOT_PROBED,
            last_probe_at=NOW,
        )
        for backend_id in ("qemu", "wokwi", "proteus", "gazebo", "webots")
    )
    return VirtualBackendPool((renode_descriptor, host_harness, ngspice, *future))


def _runtime() -> ProjectCatalogRuntime:
    return ProjectCatalogRuntime(
        project_root=PROJECT_ROOT,
        state_database=STATE_DB,
        metadata_directory=STATE_DB.parent / ".embedded-copilot" / "projects",
        initialize_async=False,
        location_allow_list=(PROJECT_ROOT,),
        production_mode=True,
    )


def test_canonical_g9_pass_persists_and_survives_two_restart_boundaries(tmp_path: Path) -> None:
    if os.environ.get("EMBRIX_REAL_RENODE_ACCEPTANCE") != "1":
        pytest.skip("canonical G9 acceptance requires explicit external-process authorization")
    if not PROJECT_ROOT.is_dir() or not TOOLCHAIN_BIN.is_dir() or not ELF.is_file():
        pytest.skip("canonical project, toolchain, or ELF is unavailable")
    if not RENODE_EXE.is_file() or not PLATFORM.is_file() or not STATE_DB.is_file() or not G8_MANIFEST.is_file():
        pytest.skip("official Renode, canonical state, or G8 manifest is unavailable")

    manifest_before, manifest_bytes_before, manifest_fingerprint = _manifest_state()
    first_runtime = _runtime()
    detail = first_runtime.conversation(
        PROJECT_ID,
        CONVERSATION_ID,
        tenant_id="desktop-tenant",
        requester="golden-bootstrap-reviewer",
    )
    source = CanonicalVirtualAssemblyAdapter(
        PROJECT_ROOT,
        project_id=PROJECT_ID,
        toolchain_bin=TOOLCHAIN_BIN,
    ).collect()
    assembly = VirtualAssemblyService().assemble(source)
    request, simulation_result, evidence = _run_real_renode(source, assembly, tmp_path)
    authority = _authority(detail, source, manifest_before)
    backend, renode_descriptor = _renode_backends()
    backend_pool = _registered_pool(renode_descriptor)
    service = VirtualSystemVerificationService(clock=lambda: NOW)
    plan, needs, pool_snapshot = service.build_plan_with_backend_pool(
        authority,
        (backend,),
        backend_pool,
    )
    state = service.record_execution(
        plan,
        (backend,),
        scenario_id=source.consistency_input.simulation_specification.scenarios[0].scenario_id,
        scenario_fingerprint=source.consistency_input.simulation_specification.scenarios[0].fingerprint,
        request_fingerprint=request.fingerprint,
        configuration_fingerprint="sha256:" + hashlib.sha256(b"renode-stm32f103-gpio-pa8-pb0").hexdigest(),
        simulation_result=simulation_result,
        evidence=evidence,
        backend_pool=pool_snapshot,
        need_id=next(item.need_id for item in needs if item.scope is VirtualNeedScope.SYSTEM_BEHAVIOR),
    )
    assert state.summary.gate is G9GateStatus.PASS
    assert state.summary.current
    assert state.summary.physical_functionally_validated is False
    assert state.summary.g10_not_entered
    assert state.backend_pool is not None
    assert state.backend_pool.blocking_unknowns == ()
    assert {item.backend_id for item in state.backend_pool.registered} >= {
        "renode",
        "host-harness",
        "ngspice",
        "qemu",
        "wokwi",
        "proteus",
        "gazebo",
        "webots",
    }
    receipt = state.execution_receipts[0]
    assert receipt.backend_id == "RENODE"
    assert receipt.need_id is not None
    assert receipt.receipt_fingerprint == receipt.fingerprint
    assert receipt.exit_status == 0
    assert receipt.observations
    assert {item.scope for item in state.verification_results if item.status.value == "PASS"} >= {
        G9Scope.BOOT.value,
        G9Scope.CPU_RUNTIME.value,
        G9Scope.INTERFACE.value,
        G9Scope.SYSTEM_BEHAVIOR.value,
    }

    persisted = first_runtime.persist_conversation_detail(
        detail.model_copy(update={"virtual_system_verification": state, "fingerprint": "sha256:" + "0" * 64})
    )
    assert persisted.virtual_system_verification is not None
    assert persisted.virtual_system_verification.fingerprint == state.fingerprint

    second_runtime = _runtime()
    restored = second_runtime.conversation(
        PROJECT_ID,
        CONVERSATION_ID,
        tenant_id=detail.tenant_id,
        requester=detail.requester,
    )
    assert restored.virtual_system_verification is not None
    assert restored.virtual_system_verification.fingerprint == state.fingerprint
    assert restored.virtual_system_verification.summary.gate is G9GateStatus.PASS

    third_runtime = _runtime()
    restored_again = third_runtime.conversation(
        PROJECT_ID,
        CONVERSATION_ID,
        tenant_id=detail.tenant_id,
        requester=detail.requester,
    )
    assert restored_again.virtual_system_verification is not None
    assert restored_again.virtual_system_verification.fingerprint == state.fingerprint
    assert restored_again.virtual_system_verification.summary.current

    stale_firmware = service.mark_stale(state, "firmware")
    stale_pcb = service.mark_stale(state, "pcb")
    stale_backend = service.mark_stale(state, "backend")
    stale_scenario = service.mark_stale(state, "scenario")
    assert stale_firmware.summary.gate is G9GateStatus.BLOCKED
    assert stale_pcb.summary.gate is G9GateStatus.BLOCKED
    assert stale_backend.summary.gate is G9GateStatus.BLOCKED
    assert stale_scenario.summary.gate is G9GateStatus.BLOCKED
    assert service.mark_stale(state, "g8_package_only_change").fingerprint == state.fingerprint
    assert service.mark_stale(state, "ui_change").fingerprint == state.fingerprint

    manifest_after = json.loads(G8_MANIFEST.read_text(encoding="utf-8"))
    assert G8_MANIFEST.read_bytes() == manifest_bytes_before
    assert manifest_after["manifest_fingerprint"] == manifest_fingerprint
    assert manifest_after["pcb_hash"] == manifest_before["pcb_hash"]
    assert restored.pcb_ready_for_fab is not None
    assert restored.pcb_engineering_verification_summary is not None
    assert restored.pcb_ready_for_fab.fingerprint == detail.pcb_ready_for_fab.fingerprint
    assert restored.pcb_engineering_verification_summary.fingerprint == detail.pcb_engineering_verification_summary.fingerprint
