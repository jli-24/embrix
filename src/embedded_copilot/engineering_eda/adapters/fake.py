from __future__ import annotations

import copy
import hashlib
from datetime import datetime

from ..exceptions import EDAConflict, EDAUnavailable
from ..models import (
    EDAAnalysisResult,
    EDAApplyReceipt,
    EDAApplyRequest,
    EDAApplyStatus,
    EDAArtifactReference,
    EDAAuditEvent,
    EDAChangeSet,
    EDAReviewDecision,
    EDAReviewDecisionType,
    EDAReviewRecord,
    EDAReviewRequest,
    EDAReviewStatus,
    EDAValidationKind,
    EDAValidationReport,
    EDAValidationStatus,
    PCBDesignProposal,
)


def _copy(value, model):
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedEDAClock:
    __slots__ = ("_now",)

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeEDAArtifactSource:
    __slots__ = ("_artifacts",)

    def __init__(self, artifacts: tuple[EDAArtifactReference, ...]) -> None:
        self._artifacts = {
            (item.project_id, item.artifact_id): _copy(item, EDAArtifactReference)
            for item in artifacts
        }

    def get_artifact(
        self, project_id: str, artifact_id: str
    ) -> EDAArtifactReference | None:
        value = self._artifacts.get((project_id, artifact_id))
        return None if value is None else _copy(value, EDAArtifactReference)

    def replace(self, artifact: EDAArtifactReference) -> None:
        self._artifacts[(artifact.project_id, artifact.artifact_id)] = _copy(
            artifact, EDAArtifactReference
        )


class FakeEDAProposalPort:
    __slots__ = ("_items",)

    def __init__(self, proposals: tuple[PCBDesignProposal, ...] = ()) -> None:
        self._items = proposals

    def propose(self, result: EDAAnalysisResult) -> tuple[PCBDesignProposal, ...]:
        return tuple(
            _copy(item, PCBDesignProposal)
            for item in self._items
            if item.analysis_fingerprint == result.fingerprint
        )


class FakeEDAChangeSetSource:
    __slots__ = ("_items",)

    def __init__(self, changes: tuple[EDAChangeSet, ...]) -> None:
        self._items = {
            item.change_set_id: _copy(item, EDAChangeSet) for item in changes
        }

    def get_change_set(self, change_set_id: str) -> EDAChangeSet | None:
        value = self._items.get(change_set_id)
        return None if value is None else _copy(value, EDAChangeSet)


class FakeEDAReviewPort:
    __slots__ = ("_clock", "_records")

    def __init__(self, clock: FixedEDAClock) -> None:
        self._clock = clock
        self._records: dict[str, EDAReviewRecord] = {}

    def submit(self, request: EDAReviewRequest) -> EDAReviewRecord:
        record = EDAReviewRecord(
            request=_copy(request, EDAReviewRequest),
            decision=None,
            status=EDAReviewStatus.OPEN,
        )
        existing = self._records.get(request.review_id)
        if existing is not None and existing.request != request:
            raise EDAConflict("review identity conflict")
        if existing is None:
            self._records[request.review_id] = record
        return _copy(self._records[request.review_id], EDAReviewRecord)

    def decide(
        self,
        review_id: str,
        decision: EDAReviewDecisionType,
        reviewer: str,
        comment: str = "reviewed",
    ) -> EDAReviewRecord:
        current = self._records.get(review_id)
        if current is None:
            raise EDAUnavailable("review unavailable")
        decided = EDAReviewDecision(
            decision_id=f"decision-{review_id}",
            review_id=review_id,
            project_id=current.request.project_id,
            decision=decision,
            reviewer=reviewer,
            comment=comment,
            decided_at=self._clock.now(),
        )
        record = EDAReviewRecord(
            request=current.request, decision=decided, status=EDAReviewStatus.DECIDED
        )
        if current.status is EDAReviewStatus.DECIDED:
            if current != record:
                raise EDAConflict("decision identity conflict")
            return _copy(current, EDAReviewRecord)
        self._records[review_id] = record
        return _copy(record, EDAReviewRecord)

    def get_record(self, review_id: str) -> EDAReviewRecord | None:
        value = self._records.get(review_id)
        return None if value is None else _copy(value, EDAReviewRecord)


class FakeEDARevisionWorkspace:
    __slots__ = ("created",)

    def __init__(self) -> None:
        self.created: list[EDAArtifactReference] = []

    def create_staging(
        self, artifact: EDAArtifactReference, staging_revision: str
    ) -> EDAArtifactReference:
        existing = next(
            (item for item in self.created if item.revision == staging_revision), None
        )
        if existing is not None:
            if (
                existing.project_id != artifact.project_id
                or existing.artifact_id != artifact.artifact_id
                or existing.content_fingerprint != artifact.content_fingerprint
            ):
                raise EDAConflict("staging revision conflict")
            return _copy(existing, EDAArtifactReference)
        staged = EDAArtifactReference(
            **{
                **artifact.model_dump(
                    mode="python",
                    exclude={"revision", "source_reference", "fingerprint"},
                ),
                "revision": staging_revision,
                "source_reference": f"staging://{staging_revision}/{artifact.artifact_id}",
            }
        )
        self.created.append(staged)
        return _copy(staged, EDAArtifactReference)


class FakeEDAApplyPort:
    __slots__ = ("_clock", "_receipts", "calls")

    def __init__(self, clock: FixedEDAClock) -> None:
        self._clock = clock
        self._receipts: dict[str, EDAApplyReceipt] = {}
        self.calls = 0

    def apply(
        self, request: EDAApplyRequest, staging_artifact: EDAArtifactReference
    ) -> EDAApplyReceipt:
        existing = self._receipts.get(request.fingerprint)
        if existing is not None:
            return _copy(existing, EDAApplyReceipt)
        self.calls += 1
        digest = hashlib.sha256(
            (
                staging_artifact.content_fingerprint + request.change_set.fingerprint
            ).encode()
        ).hexdigest()
        after = EDAArtifactReference(
            **{
                **staging_artifact.model_dump(
                    mode="python", exclude={"content_fingerprint", "fingerprint"}
                ),
                "content_fingerprint": "sha256:" + digest,
            }
        )
        receipt = EDAApplyReceipt(
            apply_id=request.apply_id,
            project_id=request.project_id,
            apply_request_fingerprint=request.fingerprint,
            change_set_fingerprint=request.change_set.fingerprint,
            staging_revision=request.staging_revision,
            before_fingerprint=request.before_fingerprint,
            after_artifact=after,
            status=EDAApplyStatus.STAGED,
            staged_at=self._clock.now(),
        )
        self._receipts[request.fingerprint] = receipt
        return _copy(receipt, EDAApplyReceipt)


class FakeEDAValidationPort:
    __slots__ = ("_clock", "_reports", "tool_version")

    def __init__(
        self,
        clock: FixedEDAClock,
        reports: tuple[EDAValidationReport, ...] = (),
        tool_version: str = "kicad-10.0",
    ) -> None:
        self._clock = clock
        self._reports = {
            (item.artifact.content_fingerprint, item.validation_kind): _copy(
                item, EDAValidationReport
            )
            for item in reports
        }
        self.tool_version = tool_version

    def validate(
        self, artifact: EDAArtifactReference, kind: EDAValidationKind
    ) -> EDAValidationReport:
        existing = self._reports.get((artifact.content_fingerprint, kind))
        if existing is not None:
            return _copy(existing, EDAValidationReport)
        return EDAValidationReport(
            report_id=f"report-{kind.value.casefold()}-{artifact.artifact_id}",
            project_id=artifact.project_id,
            artifact=artifact,
            validation_kind=kind,
            findings=(),
            status=EDAValidationStatus.PASSED,
            tool_version=self.tool_version,
            validated_at=self._clock.now(),
        )


class FakeEDAAuditSink:
    __slots__ = ("_events", "fail_next")

    def __init__(self) -> None:
        self._events: dict[str, EDAAuditEvent] = {}
        self.fail_next = False

    def append(self, event: EDAAuditEvent) -> EDAAuditEvent:
        if self.fail_next:
            self.fail_next = False
            raise EDAUnavailable("injected audit failure")
        checked = _copy(event, EDAAuditEvent)
        existing = self._events.get(checked.event_id)
        if existing is not None and existing != checked:
            raise EDAConflict("audit identity conflict")
        self._events.setdefault(checked.event_id, checked)
        return _copy(self._events[checked.event_id], EDAAuditEvent)

    def get(self, event_id: str) -> EDAAuditEvent | None:
        value = self._events.get(event_id)
        return None if value is None else _copy(value, EDAAuditEvent)


__all__ = tuple(name for name in globals() if name.startswith(("Fake", "Fixed")))
