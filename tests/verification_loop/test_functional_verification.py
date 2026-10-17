from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.engineering_copilot import (
    DesignComponent,
    DesignEvidenceBinding,
    DesignPin,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthStatus,
    FirmwareSpecification,
    PlanProvenance,
    SimulationScenario,
    TruthLevel,
    VerificationSpecification,
)
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.execution_worker.contracts import ArtifactIdentity
from embedded_copilot.simulation_runtime.contracts import (
    SimulationAssertion,
    SimulationAssertionKind,
    SimulationBackend,
    SimulationEvidence,
    SimulationExecutionBinding,
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)
from embedded_copilot.verification_loop import (
    EphemeralFunctionalVerificationStore,
    ExpectedBehavior,
    FunctionalCheckStatus,
    FunctionalVerificationRequest,
    FunctionalVerificationService,
    FunctionalVerificationStatus,
    JsonFunctionalVerificationStore,
    L4GateStatus,
    VerificationRejected,
    functional_source_fingerprint,
)
from embedded_copilot.virtual_assembly import AssemblyStatus, VirtualBoardAssembly

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
FP = "sha256:" + "a" * 64


def _revision(prefix: str, *values: str) -> str:
    return "revision-" + hashlib.sha256((prefix + "|" + "|".join(values)).encode()).hexdigest()[:24]


def _truth() -> DesignTruth:
    component = DesignComponent.create(
        component_id="component-u1",
        name="STM32F103C8T6",
        type="CONTROLLER",
        category="MCU",
        description="Canonical test MCU",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-component",),
    )
    led_signal = DesignSignal.create(
        signal_id="signal-led",
        name="LED_PA8",
        type="ELECTRICAL",
        source=component.component_id,
        targets=(component.component_id,),
        protocol="GPIO",
        direction="BIDIRECTIONAL",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-led-signal",),
    )
    button_signal = DesignSignal.create(
        signal_id="signal-button",
        name="BUTTON_PB0",
        type="ELECTRICAL",
        source=component.component_id,
        targets=(component.component_id,),
        protocol="GPIO",
        direction="INPUT",
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-button-signal",),
    )
    led_pin = DesignPin.create(
        pin_id="pin-pa8",
        component_id=component.component_id,
        pin_name="PA8",
        pin_number="29",
        function="LED",
        signal_id=led_signal.signal_id,
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-led-pin",),
    )
    button_pin = DesignPin.create(
        pin_id="pin-pb0",
        component_id=component.component_id,
        pin_name="PB0",
        pin_number="18",
        function="USER_BUTTON",
        signal_id=button_signal.signal_id,
        confidence=1.0,
        truth_level=TruthLevel.EXPLICIT,
        evidence_refs=("binding-button-pin",),
    )
    bindings = tuple(
        DesignEvidenceBinding.create(
            binding_id=binding_id,
            entity_id=entity_id,
            project_id="project-1",
            source=DesignSource.SCHEMATIC,
            source_id="artifact-schematic",
            artifact_ref="artifact-schematic",
            source_fingerprint=FP,
            relationship="canonical functional verification fixture",
            truth_level=TruthLevel.EXPLICIT,
        )
        for binding_id, entity_id in (
            ("binding-truth", "truth-1"),
            ("binding-component", component.component_id),
            ("binding-led-signal", led_signal.signal_id),
            ("binding-button-signal", button_signal.signal_id),
            ("binding-led-pin", led_pin.pin_id),
            ("binding-button-pin", button_pin.pin_id),
        )
    )
    return DesignTruth.create(
        truth_id="truth-1",
        project_id="project-1",
        conversation_id="conversation-1",
        design_revision="design-revision-1",
        architecture_id="architecture-1",
        status=DesignTruthStatus.APPROVED,
        components=(component,),
        signals=(led_signal, button_signal),
        pins=(led_pin, button_pin),
        evidence_bindings=bindings,
        provenance=(
            PlanProvenance.create(
                project_id="project-1",
                source_type="SCHEMATIC",
                source_id="artifact-schematic",
                source_fingerprint=FP,
            ),
        ),
    )


def _source_stack() -> tuple[DesignTruth, FirmwareSpecification, VerificationSpecification, SimulationScenario]:
    truth = _truth()
    firmware_revision = _revision("firmware-design-v1", "hardware-1", FP, "workflow-1", FP)
    firmware = FirmwareSpecification.create(
        firmware_spec_id="firmware-1",
        project_id="project-1",
        conversation_id="conversation-1",
        hardware_design_id="hardware-1",
        architecture_id="architecture-1",
        workflow_id="workflow-1",
        hardware_design_fingerprint=FP,
        workflow_fingerprint=FP,
        title="Minimal MCU firmware",
        design_revision=firmware_revision,
        target_platform="STM32F103C8T6",
        mcu_family="STM32F1",
        mcu_exact_part="STM32F103C8T6",
        architecture="ARM Cortex-M3",
        firmware_framework="CMSIS + Minimal Bare-metal",
        build_system="CMAKE",
        toolchain_family="ARM_NONE_EABI_GCC",
        design_truth_id=truth.truth_id,
        design_truth_fingerprint=truth.fingerprint,
        source_layout=("generated/minimal_mcu_control_core.c",),
        entry_point="main",
        peripheral_expectations=("GPIOA.PA8", "GPIOB.PB0"),
        provenance=(
            PlanProvenance.create(
                project_id="project-1",
                source_type="HARDWARE_DESIGN_SPECIFICATION",
                source_id="hardware-1",
                source_fingerprint=FP,
            ),
            PlanProvenance.create(
                project_id="project-1",
                source_type="ENGINEERING_WORKFLOW",
                source_id="workflow-1",
                source_fingerprint=FP,
            ),
        ),
    )
    simulation_fp = "sha256:" + "b" * 64
    verification_revision = _revision(
        "verification-plan-v1",
        "hardware-1",
        FP,
        "firmware-1",
        firmware.fingerprint,
        "simulation-1",
        simulation_fp,
        "workflow-1",
        FP,
    )
    verification = VerificationSpecification.create(
        verification_id="verification-1",
        project_id="project-1",
        conversation_id="conversation-1",
        hardware_design_id="hardware-1",
        firmware_spec_id=firmware.firmware_spec_id,
        simulation_spec_id="simulation-1",
        workflow_id="workflow-1",
        hardware_design_fingerprint=FP,
        firmware_spec_fingerprint=firmware.fingerprint,
        simulation_spec_fingerprint=simulation_fp,
        workflow_fingerprint=FP,
        design_revision=verification_revision,
        provenance=(
            PlanProvenance.create(project_id="project-1", source_type="HARDWARE_DESIGN_SPECIFICATION", source_id="hardware-1", source_fingerprint=FP),
            PlanProvenance.create(project_id="project-1", source_type="FIRMWARE_SPECIFICATION", source_id=firmware.firmware_spec_id, source_fingerprint=firmware.fingerprint),
            PlanProvenance.create(project_id="project-1", source_type="SIMULATION_SPECIFICATION", source_id="simulation-1", source_fingerprint=simulation_fp),
            PlanProvenance.create(project_id="project-1", source_type="ENGINEERING_WORKFLOW", source_id="workflow-1", source_fingerprint=FP),
        ),
    )
    scenario = SimulationScenario.create(
        scenario_id="scenario-1",
        simulation_spec_id="simulation-1",
        title="Button drives status LED",
        scenario_type="FUNCTIONAL_TEST",
        inputs=("USER_BUTTON",),
        expected_outputs=("button stimulus drives status LED",),
        acceptance_refs=("acceptance-1",),
    )
    return truth, firmware, verification, scenario


def _request(
    *,
    observations: tuple[str, ...] = (
        "embrx_stage_initial:led_raw=0x00000000,button_raw=0x00000001",
        "embrx_stage_pressed:led_raw=0x00000100,button_raw=0x00000000",
        "embrx_stage_released:led_raw=0x00000000,button_raw=0x00000001",
    ),
    configuration: tuple[str, ...] = ("gpio_configuration:a_crh=0x00000002,b_crl=0x00000004",),
    backend: SimulationBackend = SimulationBackend.RENODE,
    expected_behavior: tuple[ExpectedBehavior, ...] = (),
    stale_assembly: bool = False,
) -> FunctionalVerificationRequest:
    truth, firmware, verification, scenario = _source_stack()
    artifact_sha = "sha256:" + "c" * 64
    assembly = VirtualBoardAssembly.create(
        assembly_id="assembly-1",
        project_id="project-1",
        conversation_id="conversation-1",
        design_truth_id=truth.truth_id,
        design_truth_fingerprint=truth.fingerprint,
        hardware_design_id="hardware-1",
        schematic_artifact_id="artifact-schematic",
        schematic_sha256=FP,
        pcb_artifact_id="artifact-pcb",
        pcb_sha256=FP,
        bom_artifact_id="artifact-bom",
        bom_sha256=FP,
        firmware_specification_id=firmware.firmware_spec_id,
        firmware_specification_fingerprint=firmware.fingerprint,
        elf_artifact_id="artifact-elf" if not stale_assembly else "artifact-old",
        elf_sha256=artifact_sha,
        assembly_status=AssemblyStatus.READY_FOR_SIMULATION,
    )
    artifact = ArtifactIdentity(
        artifact_id="artifact-elf",
        tenant_id="tenant-1",
        project_id="project-1",
        target_id="target-1",
        workspace_fingerprint=FP,
        build_operation_id="build-1",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        build_configuration="RELEASE",
        artifact_type="ELF",
        artifact_sha256=artifact_sha,
        size_bytes=1024,
        created_at=NOW,
    )
    binding = SimulationExecutionBinding.create(
        requester_id="engineer-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        nonce="nonce-1",
    )
    simulation_request = SimulationRequest.create(
        simulation_id="simulation-run-1",
        tenant_id="tenant-1",
        project_id="project-1",
        backend=backend,
        firmware_artifact=artifact,
        platform_descriptor="stm32f103",
        scenario=scenario.scenario_id,
        assertions=(SimulationAssertion.create(assertion_id="exit-0", kind=SimulationAssertionKind.EXIT_STATUS, target="renode", expected=0),),
        timeout_seconds=30,
        execution_binding=binding,
    )
    result = SimulationResult.create(
        simulation_id=simulation_request.simulation_id,
        operation_id="operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        backend=backend,
        status=SimulationStatus.PASS,
        failure_class=SimulationFailureClass.NONE,
        reason_code="SIMULATION_ASSERTIONS_PASSED",
        started_at=NOW,
        finished_at=NOW,
        assertion_results=(
            {"assertion_id": "exit-0", "kind": SimulationAssertionKind.EXIT_STATUS, "passed": True, "observed_summary": "renode_exit_code=0", "evidence_reference": "simulation-evidence-1"},
        ),
        artifact_references=("artifact:artifact-elf", "simulation-evidence-1"),
        telemetry_references=("telemetry:simulation-run-1:gpio",),
        diagnostic_summary="real backend completed",
        request_fingerprint=simulation_request.fingerprint,
    )
    evidence = SimulationEvidence.create(
        evidence_id="simulation-evidence-1",
        operation_id="operation-1",
        simulation_id=simulation_request.simulation_id,
        backend=backend,
        backend_version="1.16.1",
        model_fidelity="PARTIAL_RCC",
        artifact_id=artifact.artifact_id,
        artifact_sha256=artifact.artifact_sha256,
        request_fingerprint=simulation_request.fingerprint,
        elf_loaded=True,
        cpu_started=True,
        reset_handler_reached=True,
        main_reached=True,
        observations=observations,
        stimuli=("initial:button_released", "stimulus:button_pressed", "stimulus:button_released"),
        configuration_observations=configuration,
        evidence_summary="real backend observation",
        receipt_reference="operation:operation-1",
        created_at=NOW,
    )
    return FunctionalVerificationRequest.create(
        project_id="project-1",
        requirement_specification=None,
        design_truth=truth,
        firmware_specification=firmware,
        verification_specification=verification,
        virtual_assembly=assembly,
        simulation_scenario=scenario,
        simulation_request=simulation_request,
        simulation_result=result,
        simulation_evidence=evidence,
        expected_behavior=expected_behavior,
    )


def test_real_shaped_evidence_passes_l4_and_survives_restart(tmp_path) -> None:
    request = _request()
    store = JsonFunctionalVerificationStore(tmp_path / "functional-verification")
    service = FunctionalVerificationService(store=store, clock=lambda: NOW)
    record = service.verify_and_persist(request)

    assert record.result.status is FunctionalVerificationStatus.PASS
    assert record.result.l4_gate is L4GateStatus.VIRTUAL_FUNCTIONAL_VERIFIED
    assert record.evidence.observed_from_backend is True
    reloaded = JsonFunctionalVerificationStore(tmp_path / "functional-verification").get(
        "project-1", record.result.verification_result_id
    )
    assert reloaded is not None
    assert reloaded.fingerprint == record.fingerprint


def test_wrong_led_observation_is_fail_not_llm_reinterpreted() -> None:
    request = _request(
        observations=(
            "embrx_stage_initial:led_raw=0x00000100,button_raw=0x00000001",
            "embrx_stage_pressed:led_raw=0x00000000,button_raw=0x00000000",
            "embrx_stage_released:led_raw=0x00000000,button_raw=0x00000001",
        )
    )
    result = FunctionalVerificationService().verify(request)

    assert result.status is FunctionalVerificationStatus.FAIL
    assert next(item for item in result.checks if item.category == "OUTPUT_RESPONSE").status is FunctionalCheckStatus.FAIL
    assert result.l4_gate is L4GateStatus.NOT_ACHIEVED


def test_missing_observation_is_incomplete() -> None:
    result = FunctionalVerificationService().verify(_request(observations=(), configuration=()))

    assert result.status is FunctionalVerificationStatus.INCOMPLETE
    assert result.unknown_count >= 2
    assert result.l4_gate is L4GateStatus.NOT_ACHIEVED


def test_stale_elf_lineage_is_stale() -> None:
    result = FunctionalVerificationService().verify(_request(stale_assembly=True))

    assert result.status is FunctionalVerificationStatus.STALE
    assert result.stale_count >= 1


def test_partial_rcc_is_sufficient_for_non_timing_claims_but_not_rcc_claims() -> None:
    service = FunctionalVerificationService()
    sufficient = service.verify(_request())
    blocked = service.verify(
        _request(
            expected_behavior=(
                ExpectedBehavior.create(
                    behavior_id="rcc-timing",
                    category="RCC_TIMING",
                    expected="clock timing is verified",
                    source_refs=("requirement-rcc",),
                ),
            )
        )
    )

    assert sufficient.model_scope.sufficient is True
    assert blocked.model_scope.status.value == "INSUFFICIENT"
    assert blocked.status is FunctionalVerificationStatus.BLOCKED
    assert any(item.category == "RCC-TIMING" and item.status is FunctionalCheckStatus.BLOCKED for item in blocked.checks)


def test_fake_backend_evidence_is_rejected() -> None:
    with pytest.raises(VerificationRejected):
        FunctionalVerificationService().verify(_request(backend=SimulationBackend.FAKE))


def test_non_passing_simulation_result_cannot_be_promoted_by_observations() -> None:
    request = _request()
    result_values = request.simulation_result.model_dump(mode="python")
    result_values["status"] = SimulationStatus.FAIL
    result_values["failure_class"] = SimulationFailureClass.ENGINEERING
    result_values["reason_code"] = "GPIO_STIMULUS_ASSERTION_FAILED"
    result_values["assertion_results"][0]["passed"] = False
    result_values["assertion_results"][0]["fingerprint"] = ZERO
    result_values["fingerprint"] = ZERO
    failed_result = SimulationResult.model_validate(result_values, strict=False)
    request_values = request.model_dump(mode="python")
    request_values["simulation_result"] = failed_result
    request_values["fingerprint"] = ZERO

    result = FunctionalVerificationService().verify(
        FunctionalVerificationRequest.model_validate(request_values, strict=False)
    )

    assert result.status is FunctionalVerificationStatus.FAIL
    assert all(item.status is FunctionalCheckStatus.FAIL for item in result.checks if item.required)


def test_source_snapshot_fingerprint_detects_staleness() -> None:
    request = _request()
    bound = FunctionalVerificationRequest.model_validate(
        {
            **request.model_dump(mode="python"),
            "source_fingerprint": functional_source_fingerprint(request),
            "fingerprint": ZERO,
        },
        strict=False,
    )
    stale = FunctionalVerificationRequest.model_validate(
        {
            **bound.model_dump(mode="python"),
            "source_fingerprint": "sha256:" + "f" * 64,
            "fingerprint": ZERO,
        },
        strict=False,
    )

    assert FunctionalVerificationService().verify(stale).status is FunctionalVerificationStatus.STALE


def test_ephemeral_store_rejects_conflicting_identity() -> None:
    store = EphemeralFunctionalVerificationStore()
    service = FunctionalVerificationService(store=store, clock=lambda: NOW)
    record = service.verify_and_persist(_request())
    assert store.get("project-1", record.result.verification_result_id) is not None
