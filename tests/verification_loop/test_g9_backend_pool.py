from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.simulation_runtime.contracts import (
    SimulationBackend,
    SimulationBackendCapability,
    SimulationCapabilityStatus,
    SimulationExecutionMode,
)
from embedded_copilot.verification_loop import (
    BackendAvailability,
    BackendProbeStatus,
    BackendTrustStatus,
    G9AuthoritySnapshot,
    VirtualBackendDescriptor,
    VirtualBackendPool,
    VirtualNeedDisposition,
    VirtualNeedScope,
    VirtualVerificationApplicabilityResolver,
    VirtualVerificationNeed,
    backend_descriptor_from_capability,
    classify_backend_process_failure,
)

NOW = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
TARGET = "STM32F103C8T6"


def _fp(char: str) -> str:
    return "sha256:" + char * 64


def _authority(*, interfaces: tuple[str, ...] = ("GPIO", "PA8", "PB0")) -> G9AuthoritySnapshot:
    return G9AuthoritySnapshot.create(
        project_id="project-canonical",
        conversation_id="conversation-canonical",
        hardware_identity="truth-canonical",
        board_identity="board-canonical",
        mcu_identity=TARGET,
        current_pcb_fingerprint=_fp("1"),
        current_pcb_state_fingerprint=_fp("2"),
        firmware_spec_fingerprint=_fp("3"),
        firmware_implementation_fingerprint=_fp("4"),
        build_configuration_fingerprint=_fp("5"),
        build_receipt_fingerprint=_fp("6"),
        elf_fingerprint=_fp("7"),
        bin_fingerprint=_fp("8"),
        hw_fw_consistency_fingerprint=_fp("9"),
        requirement_refs=("requirement-canonical",),
        interface_set=interfaces,
        expected_system_behaviors=("button_pressed -> led_on",),
        g8_manifest_fingerprint=_fp("a"),
    )


def _descriptor(
    backend_id: str,
    *,
    scopes: tuple[VirtualNeedScope, ...],
    capabilities: tuple[str, ...],
    availability: BackendAvailability = BackendAvailability.AVAILABLE,
    trust: BackendTrustStatus = BackendTrustStatus.TRUSTED,
    probe: BackendProbeStatus = BackendProbeStatus.VERIFIED,
) -> VirtualBackendDescriptor:
    return VirtualBackendDescriptor.create(
        backend_id=backend_id,
        backend_type="TEST_BACKEND",
        tool_name=backend_id,
        tool_path=f"{backend_id}-runtime",
        tool_version="1.0.0" if availability is BackendAvailability.AVAILABLE else None,
        availability=availability,
        trust_status=trust,
        supported_targets=(TARGET,),
        supported_architectures=("ARM Cortex-M3",),
        supported_scopes=scopes,
        supported_capabilities=capabilities,
        limitations=(),
        discovery_source="test-discovery",
        probe_status=probe,
        last_probe_at=NOW,
    )


def _need(
    scope: VirtualNeedScope,
    *,
    required: bool,
    capabilities: tuple[str, ...],
    evidence: tuple[str, ...],
    blocking: bool,
) -> VirtualVerificationNeed:
    return VirtualVerificationNeed.create(
        need_id=f"need-{scope.value.casefold()}",
        scope=scope,
        target=TARGET,
        required=required,
        required_capabilities=capabilities,
        source_dependencies=(_fp("b"),),
        expected_evidence=evidence,
        blocking_if_unverified=blocking,
        disposition=VirtualNeedDisposition.REQUIRED if required else VirtualNeedDisposition.OPTIONAL,
    )


def test_applicability_resolver_uses_current_authority_and_marks_absent_interfaces_na() -> None:
    needs = VirtualVerificationApplicabilityResolver().resolve(_authority())
    by_scope = {item.scope: item for item in needs}
    assert by_scope[VirtualNeedScope.GPIO].disposition is VirtualNeedDisposition.REQUIRED
    assert by_scope[VirtualNeedScope.SYSTEM_BEHAVIOR].disposition is VirtualNeedDisposition.REQUIRED
    assert by_scope[VirtualNeedScope.UART].disposition is VirtualNeedDisposition.NOT_APPLICABLE
    assert by_scope[VirtualNeedScope.ANALOG_POWER].disposition is VirtualNeedDisposition.NOT_APPLICABLE


def test_backend_discovery_probe_and_right_backend_selection() -> None:
    capability = SimulationBackendCapability.create(
        backend=SimulationBackend.RENODE,
        status=SimulationCapabilityStatus.VERIFIED,
        version="1.16.1",
        execution_mode=SimulationExecutionMode.LOCAL_PROCESS,
        supported_targets=(TARGET,),
        reason_code="RENODE_MODEL_AVAILABLE",
    )
    renode = backend_descriptor_from_capability(
        capability,
        backend_id="renode",
        tool_path="renode.exe",
        supported_scopes=(VirtualNeedScope.BOOT, VirtualNeedScope.GPIO),
        supported_capabilities=("elf_loaded", "gpio_transition"),
        last_probe_at=NOW,
    )
    host = _descriptor(
        "host-harness",
        scopes=(VirtualNeedScope.SYSTEM_BEHAVIOR,),
        capabilities=("state_transition",),
    )
    pool = VirtualBackendPool((renode, host))
    boot = _need(
        VirtualNeedScope.BOOT,
        required=True,
        capabilities=("elf_loaded",),
        evidence=("elf_loaded",),
        blocking=True,
    )
    behavior = _need(
        VirtualNeedScope.SYSTEM_BEHAVIOR,
        required=True,
        capabilities=("state_transition",),
        evidence=("state_transition",),
        blocking=True,
    )
    assert pool.select(boot).selected_backend_id == "renode"
    assert pool.select(behavior).selected_backend_id == "host-harness"
    snapshot = pool.snapshot((boot, behavior), updated_at=NOW)
    assert snapshot.blocking_unknowns == ()


def test_non_equivalent_fallback_is_rejected_and_required_unavailable_blocks() -> None:
    host = _descriptor(
        "host-harness",
        scopes=(VirtualNeedScope.SYSTEM_BEHAVIOR,),
        capabilities=("state_transition",),
    )
    pool = VirtualBackendPool((host,))
    boot = _need(
        VirtualNeedScope.BOOT,
        required=True,
        capabilities=("elf_loaded",),
        evidence=("elf_loaded",),
        blocking=True,
    )
    optional_protocol = _need(
        VirtualNeedScope.PROTOCOL,
        required=False,
        capabilities=("uart",),
        evidence=("uart",),
        blocking=False,
    )
    snapshot = pool.snapshot((boot, optional_protocol), updated_at=NOW)
    assert snapshot.selections[0].evidence_requirement_satisfied is False
    assert snapshot.selections[0].reason == "REQUIRED_BACKEND_UNAVAILABLE"
    assert snapshot.blocking_unknowns == ("need-boot",)
    assert snapshot.selections[1].reason == "OPTIONAL_BACKEND_UNAVAILABLE"
    assert snapshot.limitations == ("need-protocol:OPTIONAL_BACKEND_UNAVAILABLE",)


def test_backend_process_crash_is_unavailable_not_engineering_failure() -> None:
    descriptor = _descriptor(
        "renode",
        scopes=(VirtualNeedScope.BOOT,),
        capabilities=("elf_loaded",),
    )
    failure = classify_backend_process_failure(
        descriptor,
        exit_status=0xE0434352,
        log_refs=("renode-startup-log",),
    )
    assert failure.reason == "BACKEND_PROCESS_CRASH"
    assert failure.availability is BackendAvailability.ERROR
    assert failure.engineering_failure is False
