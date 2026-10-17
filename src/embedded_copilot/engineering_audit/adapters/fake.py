from __future__ import annotations

import copy
from datetime import datetime

from ..contracts import (
    validate_event,
    validate_evidence_bundle,
    validate_review_record,
    validate_trail,
)
from ..exceptions import AuditConflict, AuditWriteFailed
from ..models import (
    AuditSourceType,
    AuditTrail,
    EvidenceBundle,
    identifier,
    utc_timestamp,
)
from ..reporting import build_audit_report


class FakeReviewRecordSourceAdapter:
    def __init__(self, records: tuple[object, ...]) -> None:
        if type(records) is not tuple:
            raise TypeError("records must be a tuple")
        self._records: dict[str, object] = {}
        for raw in records:
            record = validate_review_record(raw)
            identity = record.review_request.review_id
            existing = self._records.get(identity)
            if existing is not None and existing != record:
                raise AuditConflict("review record identity conflict")
            self._records[identity] = record

    def get_review_record(self, review_id: str):
        identity = identifier(review_id, field="review_id")
        record = self._records.get(identity)
        return None if record is None else validate_review_record(record)


class FakeEvidenceSourceAdapter:
    def __init__(self, bundles: tuple[EvidenceBundle, ...]) -> None:
        if type(bundles) is not tuple:
            raise TypeError("bundles must be a tuple")
        self._bundles: dict[tuple[str, AuditSourceType, str, str], EvidenceBundle] = {}
        for raw in bundles:
            bundle = validate_evidence_bundle(raw)
            key = (
                bundle.project_id,
                bundle.source_type,
                bundle.source_id,
                bundle.source_fingerprint,
            )
            existing = self._bundles.get(key)
            if existing is not None and existing != bundle:
                raise AuditConflict("evidence bundle identity conflict")
            self._bundles[key] = bundle

    def get_evidence(
        self,
        project_id: str,
        source_type: AuditSourceType,
        source_id: str,
        source_fingerprint: str,
    ) -> EvidenceBundle | None:
        try:
            source = AuditSourceType(source_type)
        except (TypeError, ValueError):
            return None
        key = (project_id, source, source_id, source_fingerprint)
        bundle = self._bundles.get(key)
        return None if bundle is None else validate_evidence_bundle(bundle)


class FakeAuditStoreAdapter:
    def __init__(self, *, fail_on_attempts: tuple[int, ...] = ()) -> None:
        if type(fail_on_attempts) is not tuple or any(
            type(attempt) is not int or attempt < 1 for attempt in fail_on_attempts
        ):
            raise TypeError("fail_on_attempts must contain positive integers")
        self._fail_on_attempts = frozenset(fail_on_attempts)
        self._latest: dict[str, AuditTrail] = {}
        self._versions: dict[str, tuple[AuditTrail, ...]] = {}
        self.attempt_count = 0

    def append(self, event):
        self.attempt_count += 1
        if self.attempt_count in self._fail_on_attempts:
            raise AuditWriteFailed("injected append failure")
        try:
            checked = validate_event(event)
        except Exception as exc:
            raise AuditWriteFailed("event failed store boundary validation") from exc
        current = self._latest.get(checked.audit_id)
        if current is None:
            try:
                trail = AuditTrail.create(
                    audit_id=checked.audit_id,
                    project_id=checked.project_id,
                    events=(checked,),
                    created_at=checked.timestamp,
                )
            except Exception as exc:
                raise AuditConflict("event cannot begin an audit trail") from exc
        else:
            existing = next(
                (
                    saved
                    for saved in current.events
                    if saved.event_id == checked.event_id
                ),
                None,
            )
            if existing is not None:
                if existing != checked:
                    raise AuditConflict("audit event identity conflict")
                return validate_trail(current)
            try:
                trail = current.append_event(checked)
            except Exception as exc:
                raise AuditConflict("audit event append conflict") from exc
        self._latest[checked.audit_id] = validate_trail(trail)
        previous = self._versions.get(checked.audit_id, ())
        self._versions[checked.audit_id] = (*previous, validate_trail(trail))
        return validate_trail(trail)

    def get_trail(self, audit_id: str) -> AuditTrail | None:
        identity = identifier(audit_id, field="audit_id")
        trail = self._latest.get(identity)
        return None if trail is None else validate_trail(trail)

    def get_versions(self, audit_id: str) -> tuple[AuditTrail, ...]:
        identity = identifier(audit_id, field="audit_id")
        return tuple(validate_trail(item) for item in self._versions.get(identity, ()))

    def get_trail_for_test(self, project_id: str) -> AuditTrail | None:
        from ..models import project_audit_id

        return self.get_trail(project_audit_id(project_id))


class FakeReportGeneratorAdapter:
    def generate(self, trail: AuditTrail, generated_at: datetime):
        return build_audit_report(trail, generated_at)


class FakeAuditClockAdapter:
    def __init__(self, fixed: datetime) -> None:
        self._fixed = utc_timestamp(fixed, field="fixed")

    def now(self) -> datetime:
        return copy.deepcopy(self._fixed)

