from __future__ import annotations

import copy
import hashlib

from .contracts import validate_finding, validate_report, validate_request
from .exceptions import (
    ValidationConflict,
    ValidationRejected,
    ValidationUnavailable,
)
from .models import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationReport,
    ValidationRequest,
    ValidationSeverity,
)
from .ports import ValidationRulePort, ValidationStorePort


class ValidationService:
    """Validate immutable engineering references without executing changes."""

    __slots__ = ("_rule", "_store")

    def __init__(
        self,
        *,
        rule: ValidationRulePort,
        store: ValidationStorePort,
    ) -> None:
        if not isinstance(rule, ValidationRulePort):
            raise TypeError("validation rule port is invalid")
        if not isinstance(store, ValidationStorePort):
            raise TypeError("validation store port is invalid")
        self._rule = rule
        self._store = store

    def validate(self, request: ValidationRequest) -> ValidationReport:
        try:
            checked_request = validate_request(request)
        except Exception as error:
            raise ValidationRejected() from error

        try:
            returned = self._rule.validate(copy.deepcopy(checked_request))
        except Exception as error:
            raise ValidationUnavailable() from error
        if type(returned) is not tuple:
            raise ValidationUnavailable()

        try:
            findings = tuple(validate_finding(item) for item in returned)
        except Exception as error:
            raise ValidationUnavailable() from error
        self._validate_findings(checked_request, findings)
        ordered = tuple(sorted(findings, key=lambda item: item.finding_id))

        report = ValidationReport.create(
            report_id=_report_id(checked_request, ordered),
            project_id=checked_request.project_id,
            findings=ordered,
            summary=_summary(ordered),
        )
        for finding in ordered:
            try:
                stored = validate_finding(
                    self._store.save_finding(copy.deepcopy(finding))
                )
            except ValidationConflict:
                raise
            except Exception as error:
                raise ValidationUnavailable() from error
            if stored != finding:
                raise ValidationUnavailable()

        try:
            stored_report = validate_report(
                self._store.save_report(copy.deepcopy(report))
            )
        except ValidationConflict:
            raise
        except Exception as error:
            raise ValidationUnavailable() from error
        if stored_report != report:
            raise ValidationUnavailable()
        return stored_report

    @staticmethod
    def _validate_findings(
        request: ValidationRequest,
        findings: tuple[ValidationFinding, ...],
    ) -> None:
        if any(
            finding.status is not ValidationFindingStatus.OPEN
            or finding.project_id != request.project_id
            or finding.category not in request.validation_scope
            or not set(finding.evidence_refs).issubset(request.knowledge_refs)
            for finding in findings
        ):
            raise ValidationRejected()
        finding_ids = tuple(finding.finding_id for finding in findings)
        fingerprints = tuple(finding.fingerprint for finding in findings)
        if (
            len(finding_ids) != len(set(finding_ids))
            or len(fingerprints) != len(set(fingerprints))
        ):
            raise ValidationRejected()


def _report_id(
    request: ValidationRequest,
    findings: tuple[ValidationFinding, ...],
) -> str:
    material = ":".join(
        (request.fingerprint, *(finding.fingerprint for finding in findings))
    )
    return "report-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]


def _summary(findings: tuple[ValidationFinding, ...]) -> str:
    counts = {
        severity: sum(finding.severity is severity for finding in findings)
        for severity in ValidationSeverity
    }
    return (
        f"findings={len(findings)}; "
        f"INFO={counts[ValidationSeverity.INFO]}; "
        f"WARNING={counts[ValidationSeverity.WARNING]}; "
        f"ERROR={counts[ValidationSeverity.ERROR]}; "
        f"CRITICAL={counts[ValidationSeverity.CRITICAL]}"
    )


__all__ = ("ValidationService",)
