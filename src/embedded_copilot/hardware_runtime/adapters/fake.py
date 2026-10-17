from __future__ import annotations

import copy
import hashlib
from datetime import datetime

from ..exceptions import HardwareConflict, HardwareUnavailable
from ..models import (
    FirmwareArtifactReference,
    HardwareAuditEvent,
    HardwareBuildReceipt,
    HardwareBuildRequest,
    HardwareCapability,
    HardwareFlashReceipt,
    HardwareFlashRequest,
    HardwareObservation,
    HardwareObservationRequest,
    HardwareObservationStatus,
    HardwareObservationType,
    HardwareOperationPlan,
    HardwareReceiptStatus,
    HardwareResourceGrant,
    HardwareReviewDecision,
    HardwareReviewDecisionType,
    HardwareReviewRecord,
    HardwareReviewRequest,
    HardwareReviewStatus,
    HardwareTargetReference,
    UARTObservationContent,
)


def _copy(value, model):
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedHardwareClock:
    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeHardwareTargetSource:
    __slots__ = ("_targets",)

    def __init__(self, targets: tuple[HardwareTargetReference, ...]) -> None:
        self._targets = {
            (item.project_id, item.device_id): _copy(item, HardwareTargetReference)
            for item in targets
        }

    def get_target(
        self, project_id: str, device_id: str
    ) -> HardwareTargetReference | None:
        value = self._targets.get((project_id, device_id))
        return None if value is None else _copy(value, HardwareTargetReference)


class FakeHardwareResourcePolicy:
    __slots__ = ("_grants",)

    def __init__(self, grants: tuple[HardwareResourceGrant, ...]) -> None:
        identities = tuple(
            (item.project_id, item.requester, item.target_fingerprint)
            for item in grants
        )
        if len(identities) != len(set(identities)):
            raise HardwareConflict("duplicate resource grant")
        self._grants = {
            key: _copy(value, HardwareResourceGrant)
            for key, value in zip(identities, grants, strict=True)
        }

    def authorize(
        self, request: HardwareObservationRequest, capability: HardwareCapability
    ) -> HardwareResourceGrant:
        grant = self._grants.get(
            (request.project_id, request.requester, request.target.fingerprint)
        )
        if grant is None or capability not in grant.capabilities:
            raise HardwareUnavailable("resource grant unavailable")
        return _copy(grant, HardwareResourceGrant)


class FakeHardwareOperationPlanStore:
    __slots__ = ("_plans",)

    def __init__(self) -> None:
        self._plans: dict[str, HardwareOperationPlan] = {}

    def save(self, plan: HardwareOperationPlan) -> HardwareOperationPlan:
        checked = _copy(plan, HardwareOperationPlan)
        existing = self._plans.get(checked.plan_id)
        if existing is not None and existing != checked:
            raise HardwareConflict("operation plan conflict")
        self._plans.setdefault(checked.plan_id, checked)
        return _copy(self._plans[checked.plan_id], HardwareOperationPlan)

    def get(self, plan_id: str) -> HardwareOperationPlan | None:
        value = self._plans.get(plan_id)
        return None if value is None else _copy(value, HardwareOperationPlan)


class FakeHardwareReviewPort:
    __slots__ = ("_clock", "_records")

    def __init__(self, clock: FixedHardwareClock) -> None:
        self._clock = clock
        self._records: dict[str, HardwareReviewRecord] = {}

    def submit(self, request: HardwareReviewRequest) -> HardwareReviewRecord:
        record = HardwareReviewRecord(
            request=request, decision=None, status=HardwareReviewStatus.OPEN
        )
        existing = self._records.get(request.review_id)
        if existing is not None and existing.request != request:
            raise HardwareConflict("hardware review conflict")
        self._records.setdefault(request.review_id, record)
        return _copy(self._records[request.review_id], HardwareReviewRecord)

    def decide(
        self,
        review_id: str,
        decision: HardwareReviewDecisionType,
        reviewer: str,
        comment: str = "reviewed",
    ) -> HardwareReviewRecord:
        current = self._records.get(review_id)
        if current is None:
            raise HardwareUnavailable("hardware review unavailable")
        decided = HardwareReviewDecision(
            decision_id=f"decision-{review_id}",
            review_id=review_id,
            project_id=current.request.project_id,
            decision=decision,
            reviewer=reviewer,
            comment=comment,
            decided_at=self._clock.now(),
        )
        record = HardwareReviewRecord(
            request=current.request,
            decision=decided,
            status=HardwareReviewStatus.DECIDED,
        )
        if current.status is HardwareReviewStatus.DECIDED:
            if current != record:
                raise HardwareConflict("hardware decision conflict")
            return _copy(current, HardwareReviewRecord)
        self._records[review_id] = record
        return _copy(record, HardwareReviewRecord)

    def get_record(self, review_id: str) -> HardwareReviewRecord | None:
        value = self._records.get(review_id)
        return None if value is None else _copy(value, HardwareReviewRecord)


class FakeHardwareBuildPort:
    __slots__ = ("_clock", "_receipts", "calls")

    def __init__(self, clock: FixedHardwareClock) -> None:
        self._clock = clock
        self._receipts: dict[str, HardwareBuildReceipt] = {}
        self.calls = 0

    def build(self, request: HardwareBuildRequest) -> HardwareBuildReceipt:
        existing = self._receipts.get(request.fingerprint)
        if existing is not None:
            return _copy(existing, HardwareBuildReceipt)
        self.calls += 1
        digest = "sha256:" + hashlib.sha256(request.fingerprint.encode()).hexdigest()
        artifact = FirmwareArtifactReference(
            artifact_id=f"firmware-{request.request_id}",
            project_id=request.project_id,
            workspace_snapshot_fingerprint=digest,
            target=request.plan.target,
            firmware_fingerprint=digest,
            build_provenance_fingerprint=request.fingerprint,
            created_at=self._clock.now(),
        )
        receipt = HardwareBuildReceipt(
            request_id=request.request_id,
            project_id=request.project_id,
            request_fingerprint=request.fingerprint,
            target_fingerprint=request.plan.target.fingerprint,
            status=HardwareReceiptStatus.SUCCEEDED,
            firmware_artifact=artifact,
            completed_at=self._clock.now(),
        )
        self._receipts[request.fingerprint] = receipt
        return _copy(receipt, HardwareBuildReceipt)


class FakeHardwareFlashPort:
    __slots__ = ("_clock", "_receipts", "calls")

    def __init__(self, clock: FixedHardwareClock) -> None:
        self._clock = clock
        self._receipts: dict[str, HardwareFlashReceipt] = {}
        self.calls = 0

    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt:
        existing = self._receipts.get(request.fingerprint)
        if existing is not None:
            return _copy(existing, HardwareFlashReceipt)
        self.calls += 1
        artifact = request.plan.firmware_artifact
        assert artifact is not None
        receipt = HardwareFlashReceipt(
            request_id=request.request_id,
            project_id=request.project_id,
            request_fingerprint=request.fingerprint,
            target_fingerprint=request.plan.target.fingerprint,
            firmware_fingerprint=artifact.firmware_fingerprint,
            status=HardwareReceiptStatus.SUCCEEDED,
            completed_at=self._clock.now(),
        )
        self._receipts[request.fingerprint] = receipt
        return _copy(receipt, HardwareFlashReceipt)


class FakeHardwareMeasurementPort:
    __slots__ = ("_clock", "_observations", "calls", "firmware_fingerprint")

    def __init__(
        self, clock: FixedHardwareClock, firmware_fingerprint: str | None = None
    ) -> None:
        self._clock = clock
        self._observations: dict[str, HardwareObservation] = {}
        self.calls = 0
        self.firmware_fingerprint = firmware_fingerprint

    def observe(self, request: HardwareObservationRequest) -> HardwareObservation:
        existing = self._observations.get(request.fingerprint)
        if existing is not None:
            return _copy(existing, HardwareObservation)
        self.calls += 1
        if request.observation_type not in {
            HardwareObservationType.UART_BOOT_LOG,
            HardwareObservationType.UART_ERROR_LOG,
        }:
            result = HardwareObservation(
                observation_id=f"observation-{request.request_id}",
                request_fingerprint=request.fingerprint,
                project_id=request.project_id,
                target=request.target,
                firmware_fingerprint=self.firmware_fingerprint,
                observation_type=request.observation_type,
                content=None,
                status=HardwareObservationStatus.UNAVAILABLE,
                adapter_id="fake-measurement",
                evidence_refs=(),
                observed_at=self._clock.now(),
            )
            self._observations[request.fingerprint] = result
            return _copy(result, HardwareObservation)
        content = UARTObservationContent(
            baud_rate=115200,
            data_bits=8,
            parity="NONE",
            stop_bits=1,
            lines=("boot completed",),
        )
        result = HardwareObservation(
            observation_id=f"observation-{request.request_id}",
            request_fingerprint=request.fingerprint,
            project_id=request.project_id,
            target=request.target,
            firmware_fingerprint=self.firmware_fingerprint,
            observation_type=request.observation_type,
            content=content,
            status=HardwareObservationStatus.OBSERVED,
            adapter_id="fake-uart",
            evidence_refs=(f"evidence-{request.request_id}",),
            observed_at=self._clock.now(),
        )
        self._observations[request.fingerprint] = result
        return _copy(result, HardwareObservation)


class FakeHardwareAuditSink:
    __slots__ = ("_events", "fail_next")

    def __init__(self) -> None:
        self._events: dict[str, HardwareAuditEvent] = {}
        self.fail_next = False

    def append(self, event: HardwareAuditEvent) -> HardwareAuditEvent:
        if self.fail_next:
            self.fail_next = False
            raise HardwareUnavailable("injected audit failure")
        checked = _copy(event, HardwareAuditEvent)
        existing = self._events.get(checked.event_id)
        if existing is not None and existing != checked:
            raise HardwareConflict("hardware audit conflict")
        self._events.setdefault(checked.event_id, checked)
        return _copy(self._events[checked.event_id], HardwareAuditEvent)

    def get(self, event_id: str) -> HardwareAuditEvent | None:
        value = self._events.get(event_id)
        return None if value is None else _copy(value, HardwareAuditEvent)


__all__ = tuple(name for name in globals() if name.startswith(("Fake", "Fixed")))
