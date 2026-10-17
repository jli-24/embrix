from __future__ import annotations

import copy
from datetime import datetime

from ..exceptions import HILConflict, HILUnavailable
from ..models import (
    EngineeringProposalReference,
    HardwareApprovalRecord,
    HardwareApprovalRequest,
    HardwareHILContextOverlay,
    HardwareObservation,
    HardwareObservationRequest,
    HILAuditEvent,
    HILExecutionReceipt,
    HILExecutionRequest,
    HILExecutionStatus,
    HILMemoryCandidateReference,
)


def _copy(value, model):
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedClock:
    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeTargetPort:
    __slots__ = ("_items",)

    def __init__(self, items: tuple) -> None:
        self._items = {
            (item.project_id, item.device_id): _copy(item, type(item)) for item in items
        }

    def get_target(self, project_id: str, device_id: str):
        value = self._items.get((project_id, device_id))
        return None if value is None else _copy(value, type(value))


class FakeFirmwarePort:
    __slots__ = ("_items",)

    def __init__(self, items: tuple) -> None:
        self._items = {
            (item.project_id, item.artifact_id): _copy(item, type(item))
            for item in items
        }

    def get_firmware(self, project_id: str, artifact_id: str):
        value = self._items.get((project_id, artifact_id))
        return None if value is None else _copy(value, type(value))


class FakeProposalSource:
    __slots__ = ("_items",)

    def __init__(self, items: tuple[EngineeringProposalReference, ...]) -> None:
        self._items = {
            (item.project_id, item.kind, item.proposal_id): _copy(
                item, EngineeringProposalReference
            )
            for item in items
        }

    def get_proposal(self, reference: EngineeringProposalReference):
        value = self._items.get(
            (reference.project_id, reference.kind, reference.proposal_id)
        )
        return None if value is None else _copy(value, EngineeringProposalReference)


class FakeApprovalPort:
    __slots__ = ("_records", "_requests")

    def __init__(self, records: tuple[HardwareApprovalRecord, ...] = ()) -> None:
        self._requests = {}
        self._records = {
            item.approval_id: _copy(item, HardwareApprovalRecord) for item in records
        }

    def submit(self, request: HardwareApprovalRequest) -> HardwareApprovalRequest:
        checked = _copy(request, HardwareApprovalRequest)
        prior = self._requests.get(checked.approval_id)
        if prior is not None and prior != checked:
            raise HILConflict("approval request conflict")
        self._requests[checked.approval_id] = checked
        return _copy(checked, HardwareApprovalRequest)

    def get_record(self, approval_id: str):
        value = self._records.get(approval_id)
        return None if value is None else _copy(value, HardwareApprovalRecord)


class FakeExecutionPort:
    __slots__ = ("_clock", "_receipts", "fail")

    def __init__(self, clock: FixedClock) -> None:
        self._clock, self._receipts, self.fail = clock, {}, False

    def execute(self, request: HILExecutionRequest) -> HILExecutionReceipt:
        checked = _copy(request, HILExecutionRequest)
        if self.fail:
            raise HILUnavailable("injected HIL execution failure")
        receipt = HILExecutionReceipt(
            execution_id=checked.execution_id,
            request_fingerprint=checked.fingerprint,
            project_id=checked.proposal.project_id,
            target_fingerprint=checked.target.fingerprint,
            firmware_fingerprint=checked.firmware.firmware_fingerprint,
            status=HILExecutionStatus.COMPLETED,
            occurred_at=self._clock.now(),
        )
        prior = self._receipts.get(receipt.execution_id)
        if prior is not None and prior != receipt:
            raise HILConflict("execution conflict")
        self._receipts[receipt.execution_id] = receipt
        return _copy(receipt, HILExecutionReceipt)


class FakeMeasurementPort:
    __slots__ = ("_clock", "_content", "fail")

    def __init__(self, clock: FixedClock, *, content: str = "observed") -> None:
        self._clock, self._content, self.fail = clock, content, False

    def collect(self, request: HardwareObservationRequest) -> HardwareObservation:
        if self.fail:
            raise HILUnavailable("injected measurement failure")
        checked = _copy(request, HardwareObservationRequest)
        return HardwareObservation(
            observation_id=f"observation-{checked.fingerprint[-16:]}",
            request_fingerprint=checked.fingerprint,
            project_id=checked.execution.project_id,
            target_fingerprint=checked.execution.target_fingerprint,
            firmware_fingerprint=checked.execution.firmware_fingerprint,
            observation_type=checked.observation_type,
            content=self._content,
            observed_at=self._clock.now(),
        )


class FakeObservationSink:
    __slots__ = ("_items", "fail")

    def __init__(self) -> None:
        self._items, self.fail = {}, False

    def append(self, observation: HardwareObservation) -> HardwareObservation:
        if self.fail:
            raise HILUnavailable("injected observation sink failure")
        checked = _copy(observation, HardwareObservation)
        prior = self._items.get(checked.observation_id)
        if prior is not None and prior != checked:
            raise HILConflict("observation conflict")
        self._items[checked.observation_id] = checked
        return _copy(checked, HardwareObservation)

    def get(self, observation_id: str):
        value = self._items.get(observation_id)
        return None if value is None else _copy(value, HardwareObservation)


class FakeContextProjectionPort:
    def project(self, overlay: HardwareHILContextOverlay) -> HardwareHILContextOverlay:
        return _copy(overlay, HardwareHILContextOverlay)


class FakeMemoryCandidatePort:
    def create(
        self, overlay: HardwareHILContextOverlay
    ) -> tuple[HILMemoryCandidateReference, ...]:
        checked = _copy(overlay, HardwareHILContextOverlay)
        return (
            HILMemoryCandidateReference(
                project_id=checked.project_id,
                overlay_fingerprint=checked.fingerprint,
                candidate_id=f"memory-{checked.overlay_id}",
                candidate_fingerprint=checked.fingerprint,
            ),
        )


class FakeAuditPort:
    __slots__ = ("_items", "fail")

    def __init__(self) -> None:
        self._items, self.fail = {}, False

    def append(self, event: HILAuditEvent) -> HILAuditEvent:
        if self.fail:
            raise HILUnavailable("injected audit failure")
        checked = _copy(event, HILAuditEvent)
        prior = self._items.get(checked.event_id)
        if prior is not None and prior != checked:
            raise HILConflict("audit conflict")
        self._items[checked.event_id] = checked
        return _copy(checked, HILAuditEvent)

    def get(self, event_id: str):
        value = self._items.get(event_id)
        return None if value is None else _copy(value, HILAuditEvent)
