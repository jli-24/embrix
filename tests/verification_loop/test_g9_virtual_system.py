from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.simulation_runtime.contracts import (
    SimulationAssertionKind,
    SimulationAssertionResult,
    SimulationBackend,
    SimulationEvidence,
    SimulationFailureClass,
    SimulationResult,
    SimulationStatus,
)
from embedded_copilot.verification_loop import (
    BackendTrustStatus,
    G9AuthoritySnapshot,
    G9GateStatus,
    G9Scope,
    VerificationStatus,
    VirtualSystemVerificationService,
    VirtualVerificationBackend,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
FP = "sha256:" + "1" * 64


def authority() -> G9AuthoritySnapshot:
    return G9AuthoritySnapshot.create(
        project_id="project-g9",
        conversation_id="conversation-g9",
        hardware_identity="hardware-stm32f103",
        board_identity="board-minimal-control",
        mcu_identity="STM32F103C8T6",
        current_pcb_fingerprint=FP,
        current_pcb_state_fingerprint="sha256:" + "2" * 64,
        firmware_spec_fingerprint="sha256:" + "3" * 64,
        firmware_implementation_fingerprint="sha256:" + "4" * 64,
        build_configuration_fingerprint="sha256:" + "5" * 64,
        build_receipt_fingerprint="sha256:" + "a" * 64,
        elf_fingerprint="sha256:" + "6" * 64,
        bin_fingerprint="sha256:" + "7" * 64,
        hw_fw_consistency_fingerprint="sha256:" + "8" * 64,
        requirement_refs=("requirement-button-led",),
        interface_set=("GPIO",),
        expected_system_behaviors=("button_released->led_off", "button_pressed->led_on"),
        g8_manifest_fingerprint="sha256:" + "9" * 64,
    )


def backend() -> VirtualVerificationBackend:
    return VirtualVerificationBackend.create(
        backend_name="RENODE",
        backend_type="MCU_EXECUTION_EMULATOR",
        path_or_runtime="trusted-installation:Renode",
        version="1.16.1",
        discovery_source="trusted_tool_discovery",
        supported_scope=(G9Scope.BOOT, G9Scope.CPU_RUNTIME, G9Scope.INTERFACE, G9Scope.SYSTEM_BEHAVIOR),
        current_target_supported=True,
        trust_status=BackendTrustStatus.TRUSTED,
    )


def real_result_and_evidence() -> tuple[SimulationResult, SimulationEvidence]:
    result = SimulationResult.create(
        simulation_id="simulation-g9",
        operation_id="operation-g9",
        tenant_id="tenant-g9",
        project_id="project-g9",
        backend=SimulationBackend.RENODE,
        status=SimulationStatus.PASS,
        failure_class=SimulationFailureClass.NONE,
        reason_code="SIMULATION_ASSERTIONS_PASSED",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        assertion_results=(
            SimulationAssertionResult.create(
                assertion_id="assertion-g9",
                kind=SimulationAssertionKind.EXIT_STATUS,
                passed=True,
                observed_summary="renode_exit_code=0",
                evidence_reference="evidence-g9",
            ),
        ),
        artifact_references=("artifact:elf-g9",),
        telemetry_references=("telemetry:simulation-g9:gpio",),
        diagnostic_summary="real backend passed",
        request_fingerprint=FP,
    )
    evidence = SimulationEvidence.create(
        evidence_id="simulation-evidence-g9",
        operation_id="operation-g9",
        simulation_id="simulation-g9",
        backend=SimulationBackend.RENODE,
        backend_version="1.16.1",
        model_fidelity="PARTIAL_RCC",
        artifact_id="artifact-elf-g9",
        artifact_sha256=authority().elf_fingerprint,
        request_fingerprint=FP,
        elf_loaded=True,
        cpu_started=True,
        reset_handler_reached=True,
        main_reached=True,
        observations=(
            "embrix_stage_initial:led_raw=0x00000000,button_raw=0x00000001",
            "embrix_stage_pressed:led_raw=0x00000100,button_raw=0x00000000",
            "embrix_stage_released:led_raw=0x00000000,button_raw=0x00000001",
        ),
        stimuli=("initial:button_released", "stimulus:button_pressed", "stimulus:button_released"),
        configuration_observations=("gpio_configuration:a_crh=0x00000002,b_crl=0x00000004",),
        evidence_summary="real Renode evidence",
        receipt_reference="operation:operation-g9",
        created_at=NOW,
    )
    return result, evidence


def failed_result_and_evidence(
    status: SimulationStatus,
    failure_class: SimulationFailureClass,
    reason_code: str,
) -> tuple[SimulationResult, SimulationEvidence]:
    result = SimulationResult.create(
        simulation_id="simulation-g9-failure",
        operation_id="operation-g9-failure",
        tenant_id="tenant-g9",
        project_id="project-g9",
        backend=SimulationBackend.RENODE,
        status=status,
        failure_class=failure_class,
        reason_code=reason_code,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        assertion_results=(
            SimulationAssertionResult.create(
                assertion_id="assertion-g9-failure",
                kind=SimulationAssertionKind.EXIT_STATUS,
                passed=False,
                observed_summary="renode execution did not satisfy the scenario",
                evidence_reference="evidence-g9-failure",
            ),
        ),
        artifact_references=("artifact:elf-g9",),
        telemetry_references=("telemetry:simulation-g9:failure",),
        diagnostic_summary="sanitized failure evidence",
        request_fingerprint=FP,
    )
    evidence = SimulationEvidence.create(
        evidence_id="simulation-evidence-g9-failure",
        operation_id="operation-g9-failure",
        simulation_id="simulation-g9-failure",
        backend=SimulationBackend.RENODE,
        backend_version="1.16.1",
        model_fidelity="PARTIAL_RCC",
        artifact_id="artifact-elf-g9",
        artifact_sha256=authority().elf_fingerprint,
        request_fingerprint=FP,
        elf_loaded=False,
        cpu_started=False,
        reset_handler_reached=False,
        main_reached=False,
        observations=(),
        stimuli=("initial:button_released",),
        configuration_observations=(),
        evidence_summary="real backend did not produce startup or GPIO observations",
        receipt_reference="operation:operation-g9-failure",
        created_at=NOW,
    )
    return result, evidence


def test_g9_plan_marks_only_current_interfaces_applicable() -> None:
    service = VirtualSystemVerificationService(clock=lambda: NOW)
    plan = service.build_plan(authority(), (backend(),))

    values = {item.item_id: item for item in plan.items}
    assert values["g9-boot"].applicability.value == "READY"
    assert values["g9-gpio"].applicability.value == "READY"
    assert values["g9-uart"].applicability.value == "NOT_APPLICABLE"
    assert values["g9-i2c"].applicability.value == "NOT_APPLICABLE"
    assert values["g9-analog-power"].applicability.value == "NOT_APPLICABLE"
    assert plan.elf_fingerprint == authority().elf_fingerprint


def test_g9_real_execution_produces_unified_results_and_pass_gate() -> None:
    service = VirtualSystemVerificationService(clock=lambda: NOW)
    plan = service.build_plan(authority(), (backend(),))
    result, evidence = real_result_and_evidence()
    state = service.record_execution(
        plan,
        (backend(),),
        scenario_id="scenario-button-led",
        scenario_fingerprint=FP,
        request_fingerprint=FP,
        configuration_fingerprint=FP,
        simulation_result=result,
        evidence=evidence,
    )

    assert state.summary.gate is G9GateStatus.PASS
    assert state.summary.blocking_issue_count == 0
    assert state.summary.blocking_unknown_count == 0
    assert state.summary.boot_status is VerificationStatus.PASS
    assert state.summary.behavior_status is VerificationStatus.PASS
    assert all(item.verifier == "virtual-system-verification" for item in state.verification_results)
    assert state.summary.physical_functionally_validated is False
    assert state.summary.g10_not_entered is True


def test_g9_rejects_fake_backend_evidence() -> None:
    service = VirtualSystemVerificationService(clock=lambda: NOW)
    plan = service.build_plan(authority(), (backend(),))
    result, evidence = real_result_and_evidence()
    fake_evidence = evidence.model_copy(update={"backend": SimulationBackend.FAKE})

    with pytest.raises(ValueError, match="Fake simulation"):
        service.record_execution(
            plan,
            (backend(),),
            scenario_id="scenario-button-led",
            scenario_fingerprint=FP,
            request_fingerprint=FP,
            configuration_fingerprint=FP,
            simulation_result=result,
            evidence=fake_evidence,
        )


def test_g9_engineering_failure_sets_required_results_fail_and_blocks_gate() -> None:
    service = VirtualSystemVerificationService(clock=lambda: NOW)
    plan = service.build_plan(authority(), (backend(),))
    result, evidence = failed_result_and_evidence(
        SimulationStatus.FAIL,
        SimulationFailureClass.ENGINEERING,
        "SIMULATION_ASSERTION_FAILED",
    )

    state = service.record_execution(
        plan,
        (backend(),),
        scenario_id="scenario-button-led",
        scenario_fingerprint=FP,
        request_fingerprint=FP,
        configuration_fingerprint=FP,
        simulation_result=result,
        evidence=evidence,
    )

    assert state.summary.gate is G9GateStatus.BLOCKED
    assert state.summary.boot_status is VerificationStatus.FAIL
    assert state.summary.interface_status is VerificationStatus.FAIL
    assert state.summary.behavior_status is VerificationStatus.FAIL
    assert state.summary.blocking_issue_count > 0


def test_g9_backend_infrastructure_failure_is_unknown_not_engineering_fail() -> None:
    service = VirtualSystemVerificationService(clock=lambda: NOW)
    plan = service.build_plan(authority(), (backend(),))
    result, evidence = failed_result_and_evidence(
        SimulationStatus.INFRASTRUCTURE_ERROR,
        SimulationFailureClass.INFRASTRUCTURE,
        "BACKEND_PROCESS_EXITED",
    )

    state = service.record_execution(
        plan,
        (backend(),),
        scenario_id="scenario-button-led",
        scenario_fingerprint=FP,
        request_fingerprint=FP,
        configuration_fingerprint=FP,
        simulation_result=result,
        evidence=evidence,
    )

    assert state.summary.gate is G9GateStatus.BLOCKED
    assert state.summary.boot_status is VerificationStatus.UNKNOWN
    assert state.summary.interface_status is VerificationStatus.UNKNOWN
    assert state.summary.behavior_status is VerificationStatus.UNKNOWN
    assert state.summary.fail_count == 0
    assert state.summary.blocking_unknown_count > 0


def test_g9_stale_is_selective_and_does_not_stale_g8_package_only() -> None:
    service = VirtualSystemVerificationService(clock=lambda: NOW)
    plan = service.build_plan(authority(), (backend(),))
    result, evidence = real_result_and_evidence()
    state = service.record_execution(
        plan,
        (backend(),),
        scenario_id="scenario-button-led",
        scenario_fingerprint=FP,
        request_fingerprint=FP,
        configuration_fingerprint=FP,
        simulation_result=result,
        evidence=evidence,
    )

    unchanged = service.mark_stale(state, "g8_package_only_change")
    assert unchanged.summary.gate is G9GateStatus.PASS
    assert all(not item.stale for item in unchanged.verification_results)

    stale = service.mark_stale(state, "firmware")
    assert stale.summary.gate is G9GateStatus.BLOCKED
    assert stale.summary.blocking_unknown_count > 0
    assert all(item.stale for item in stale.verification_results if item.scope in {"BOOT", "CPU_RUNTIME", "INTERFACE", "SYSTEM_BEHAVIOR"})
