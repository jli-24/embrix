from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_eda import (
    EDAArtifactReference,
    EDAArtifactType,
    EDABackend,
    EDAExecutionStatus,
    EDAIntegrationLayer,
    EDAOperation,
    EDAOperationKind,
    EDAReadBackStatus,
    JLCEDAUnavailableBackend,
    KiCadEDAIntegrationBackend,
)
from embedded_copilot.engineering_eda.exceptions import EDARejected, EDAUnavailable


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
FP = "sha256:" + "1" * 64
AFTER_FP = "sha256:" + "2" * 64


def artifact(*, basename: str = "control.kicad_pcb", content: str = FP, kind: EDAArtifactType = EDAArtifactType.BOARD) -> EDAArtifactReference:
    return EDAArtifactReference(
        project_id="stm32-control-board",
        artifact_id="pcb-artifact-1",
        artifact_type=kind,
        revision="pcb-r1",
        format_version="kicad-2024",
        basename=basename,
        byte_size=16,
        source_reference="workspace://pcb-artifact-1",
        content_fingerprint=content,
        captured_at=NOW,
    )


def operation(source: EDAArtifactReference) -> EDAOperation:
    return EDAOperation(
        operation_id="eda-op-1",
        backend=EDABackend.KICAD,
        kind=EDAOperationKind.WRITE_BOARD,
        project_id=source.project_id,
        source_artifact=source,
        approval_fingerprint="sha256:" + "3" * 64,
        expected_after_fingerprint=AFTER_FP,
        requested_at=NOW,
    )


class Backend:
    backend = EDABackend.KICAD

    def __init__(self, *, readback_content: str = AFTER_FP) -> None:
        self.current = artifact()
        self.readback_content = readback_content
        self.calls: list[str] = []

    def read(self, request: EDAOperation) -> EDAArtifactReference:
        self.calls.append("read")
        return self.current

    def execute(self, request: EDAOperation, current: EDAArtifactReference) -> EDAArtifactReference:
        self.calls.append("execute")
        self.current = artifact(content=AFTER_FP)
        return self.current


def test_kicad_write_is_read_first_and_read_back_is_typed() -> None:
    backend = Backend()
    receipt = EDAIntegrationLayer(KiCadEDAIntegrationBackend(reader=backend.read, executor=backend.execute)).write_then_read_back(
        operation(artifact()), verification_fingerprint="sha256:" + "4" * 64, now=NOW
    )

    assert backend.calls == ["read", "execute", "read"]
    assert receipt.execution_status is EDAExecutionStatus.EXECUTION_SUCCESS
    assert receipt.readback_status is EDAReadBackStatus.READ_BACK_PASS
    assert receipt.readback_fingerprint == AFTER_FP
    assert receipt.verification_fingerprint == "sha256:" + "4" * 64


def test_readback_drift_cannot_be_reported_as_pass() -> None:
    backend = Backend()

    def drifted_read(request: EDAOperation) -> EDAArtifactReference:
        backend.calls.append("read")
        return artifact(content=FP if len(backend.calls) == 1 else "sha256:" + "5" * 64)

    layer = EDAIntegrationLayer(KiCadEDAIntegrationBackend(reader=drifted_read, executor=backend.execute))
    receipt = layer.write_then_read_back(operation(artifact()), now=NOW)
    assert receipt.readback_status is EDAReadBackStatus.READ_BACK_FAIL
    assert receipt.verification_status.value == "NOT_APPLICABLE"


def test_expected_after_fingerprint_mismatch_is_rejected_before_readback() -> None:
    backend = Backend()
    backend.execute = lambda request, current: artifact(content="sha256:" + "6" * 64)
    layer = EDAIntegrationLayer(KiCadEDAIntegrationBackend(reader=backend.read, executor=backend.execute))

    with pytest.raises(EDARejected, match="approved expected artifact"):
        layer.write_then_read_back(operation(artifact()), now=NOW)

    assert backend.calls == ["read"]


def test_step_and_glb_are_typed_artifact_projections() -> None:
    step = artifact(basename="control.step", kind=EDAArtifactType.STEP)
    glb = artifact(basename="control.glb", kind=EDAArtifactType.GLB)
    assert step.artifact_type is EDAArtifactType.STEP
    assert glb.artifact_type is EDAArtifactType.GLB


def test_jlceda_without_trusted_adapter_is_unavailable() -> None:
    checked = artifact(basename="control.eprj3", kind=EDAArtifactType.PROJECT)
    request = EDAOperation(
        operation_id="eda-read-1",
        backend=EDABackend.JLCEDA,
        kind=EDAOperationKind.READ_PROJECT,
        project_id=checked.project_id,
        source_artifact=checked,
        requested_at=NOW,
    )
    with pytest.raises(EDAUnavailable):
        EDAIntegrationLayer(JLCEDAUnavailableBackend()).read(request)
