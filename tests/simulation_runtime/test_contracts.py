from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.execution_worker.contracts import ArtifactIdentity
from embedded_copilot.simulation_runtime.contracts import (
    SimulationAssertion,
    SimulationAssertionKind,
    SimulationBackend,
    SimulationExecutionBinding,
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulationTransition,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
FP = "sha256:" + "1" * 64


def artifact(*, project_id: str = "project-1") -> ArtifactIdentity:
    return ArtifactIdentity(
        artifact_id="artifact-1",
        tenant_id="tenant-1",
        project_id=project_id,
        target_id="target-1",
        workspace_fingerprint=FP,
        build_operation_id="build-1",
        toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3",
        build_configuration="DEBUG",
        artifact_type="ELF",
        artifact_sha256="sha256:" + "2" * 64,
        size_bytes=4096,
        created_at=NOW,
    )


def binding() -> SimulationExecutionBinding:
    return SimulationExecutionBinding.create(
        requester_id="engineer-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-1",
    )


def request(**changes: object) -> SimulationRequest:
    values: dict[str, object] = {
        "simulation_id": "simulation-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "backend": SimulationBackend.RENODE,
        "firmware_artifact": artifact(),
        "platform_descriptor": "stm32f446re-platform",
        "scenario": "uart-boot-smoke",
        "assertions": (
            SimulationAssertion.create(
                assertion_id="uart-ready",
                kind=SimulationAssertionKind.UART_CONTAINS,
                target="uart0",
                expected="READY",
            ),
        ),
        "timeout_seconds": 30,
        "execution_binding": binding(),
    }
    values.update(changes)
    return SimulationRequest.create(**values)


def test_request_is_strict_fingerprinted_and_identity_bound() -> None:
    checked = request()

    assert checked.fingerprint.startswith("sha256:")
    assert checked.firmware_artifact.project_id == checked.project_id
    assert checked.firmware_artifact.target_id == checked.execution_binding.target_id
    assert (
        checked.firmware_artifact.workspace_fingerprint
        == checked.execution_binding.workspace_fingerprint
    )


def test_request_rejects_cross_project_artifact() -> None:
    with pytest.raises(ValidationError, match="artifact identity binding mismatch"):
        request(firmware_artifact=artifact(project_id="project-2"))


def test_contract_rejects_fingerprint_tampering_and_extra_path_fields() -> None:
    checked = request()
    material = checked.model_dump(mode="python")
    material["scenario"] = "changed-scenario"
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        SimulationRequest.model_validate(material)

    material = checked.model_dump(mode="python")
    material["host_path"] = "C:/private/firmware.elf"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SimulationRequest.model_validate(material)


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        (SimulationAssertionKind.UART_CONTAINS, 0),
        (SimulationAssertionKind.UART_NOT_CONTAINS, 1),
        (SimulationAssertionKind.EXIT_STATUS, "zero"),
    ),
)
def test_assertion_rejects_malformed_expected_value(kind, expected) -> None:
    with pytest.raises(
        ValidationError, match="expected value does not match assertion kind"
    ):
        SimulationAssertion.create(
            assertion_id="assertion-1",
            kind=kind,
            target="uart0"
            if kind is not SimulationAssertionKind.EXIT_STATUS
            else "process",
            expected=expected,
        )


def test_identifier_only_descriptors_reject_path_traversal_and_shell_payloads() -> None:
    for malicious in ("../board.repl", "board;calc", "C:/board.repl", "$(whoami)"):
        with pytest.raises((TypeError, ValueError, ValidationError)):
            request(platform_descriptor=malicious)

    unsafe_artifact = artifact()
    unsafe_material = unsafe_artifact.model_dump(mode="python")
    unsafe_material["artifact_id"] = "firmware/../secret"
    with pytest.raises((TypeError, ValueError, ValidationError)):
        request(firmware_artifact=ArtifactIdentity.model_validate(unsafe_material))


def test_contract_rejects_sensitive_or_raw_output_text() -> None:
    for sensitive in ("token: abc", "password=abc", "raw stdout from device"):
        with pytest.raises((TypeError, ValueError, ValidationError)):
            request(scenario=sensitive)


@pytest.mark.parametrize(
    ("reason_code", "failure_class"),
    (
        ("BACKEND_START_TIMEOUT", SimulationFailureClass.INFRASTRUCTURE),
        ("PROCESS_CONTROL_TIMEOUT", SimulationFailureClass.INFRASTRUCTURE),
        ("WORKSPACE_TIMEOUT", SimulationFailureClass.INFRASTRUCTURE),
        ("ASSERTION_DEADLINE_EXCEEDED", SimulationFailureClass.ENGINEERING),
        ("EXPECTED_UART_NOT_OBSERVED", SimulationFailureClass.ENGINEERING),
        ("FIRMWARE_BOOT_DEADLINE_EXCEEDED", SimulationFailureClass.ENGINEERING),
    ),
)
def test_timeout_classification_is_bound_to_cause(reason_code, failure_class) -> None:
    result = SimulationResult.create(
        simulation_id="simulation-1",
        operation_id="operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        backend=SimulationBackend.RENODE,
        status=SimulationStatus.TIMEOUT,
        failure_class=failure_class,
        reason_code=reason_code,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=30),
        assertion_results=(),
        artifact_references=("artifact:artifact-1",),
        telemetry_references=(),
        diagnostic_summary="bounded simulation timeout",
        request_fingerprint=request().fingerprint,
    )

    assert result.failure_class is failure_class


def test_timeout_rejects_incorrect_causal_classification() -> None:
    with pytest.raises(
        ValidationError, match="timeout failure classification mismatch"
    ):
        SimulationResult.create(
            simulation_id="simulation-1",
            operation_id="operation-1",
            tenant_id="tenant-1",
            project_id="project-1",
            backend=SimulationBackend.RENODE,
            status=SimulationStatus.TIMEOUT,
            failure_class=SimulationFailureClass.INFRASTRUCTURE,
            reason_code="EXPECTED_UART_NOT_OBSERVED",
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=30),
            assertion_results=(),
            artifact_references=("artifact:artifact-1",),
            telemetry_references=(),
            diagnostic_summary="UART deadline exceeded",
            request_fingerprint=request().fingerprint,
        )


def test_transition_rejects_terminal_state_reentry() -> None:
    with pytest.raises(
        ValidationError, match="simulation status transition is invalid"
    ):
        SimulationTransition(
            previous=SimulationStatus.PASS,
            current=SimulationStatus.RUNNING,
        )
