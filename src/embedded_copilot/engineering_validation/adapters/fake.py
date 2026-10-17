from __future__ import annotations

import copy
import hashlib

from ..contracts import (
    validate_finding,
    validate_finding_id,
    validate_project_id,
    validate_report,
    validate_report_id,
    validate_request,
)
from ..exceptions import ValidationConflict
from ..models import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationReport,
    ValidationRequest,
    ValidationRule,
    ValidationSeverity,
)
from ..ports import ValidationRulePort, ValidationStorePort


class FakeValidationRuleAdapter(ValidationRulePort):
    """Deterministic offline rule adapter for the documented demo board."""

    __slots__ = ("_rule",)

    def __init__(self) -> None:
        self._rule = ValidationRule.create(
            rule_id="POWER-001",
            category="hardware_power",
            description="detect possible voltage mismatch",
        )

    def validate(
        self, request: ValidationRequest
    ) -> tuple[ValidationFinding, ...]:
        checked = validate_request(request)
        if (
            checked.artifact_reference.source_reference != "artifact://demo-board"
            or self._rule.category not in checked.validation_scope
            or "knowledge://power-rule" not in checked.knowledge_refs
        ):
            return ()
        finding_id = (
            "finding-"
            + hashlib.sha256(
                f"{checked.fingerprint}:{self._rule.fingerprint}".encode()
            ).hexdigest()[:48]
        )
        finding = ValidationFinding.create(
            finding_id=finding_id,
            project_id=checked.project_id,
            rule_id=self._rule.rule_id,
            category=self._rule.category,
            severity=ValidationSeverity.WARNING,
            status=ValidationFindingStatus.OPEN,
            message="possible voltage mismatch",
            evidence_refs=("knowledge://power-rule",),
        )
        return (validate_finding(copy.deepcopy(finding)),)


class FakeValidationStoreAdapter(ValidationStorePort):
    """In-memory immutable Finding and Report store for offline tests."""

    __slots__ = ("_findings", "_reports")

    def __init__(self) -> None:
        self._findings: dict[tuple[str, str], ValidationFinding] = {}
        self._reports: dict[tuple[str, str], ValidationReport] = {}

    @property
    def finding_count(self) -> int:
        return len(self._findings)

    @property
    def report_count(self) -> int:
        return len(self._reports)

    def save_finding(self, finding: ValidationFinding) -> ValidationFinding:
        checked = validate_finding(finding)
        key = (checked.project_id, checked.finding_id)
        existing = self._findings.get(key)
        if existing is not None and existing != checked:
            raise ValidationConflict()
        self._findings[key] = checked
        return validate_finding(copy.deepcopy(checked))

    def get_finding(
        self, project_id: str, finding_id: str
    ) -> ValidationFinding | None:
        project = validate_project_id(project_id)
        checked_id = validate_finding_id(finding_id)
        value = self._findings.get((project, checked_id))
        return None if value is None else validate_finding(copy.deepcopy(value))

    def save_report(self, report: ValidationReport) -> ValidationReport:
        checked = validate_report(report)
        key = (checked.project_id, checked.report_id)
        existing = self._reports.get(key)
        if existing is not None and existing != checked:
            raise ValidationConflict()
        self._reports[key] = checked
        return validate_report(copy.deepcopy(checked))

    def get_report(
        self, project_id: str, report_id: str
    ) -> ValidationReport | None:
        project = validate_project_id(project_id)
        checked_id = validate_report_id(report_id)
        value = self._reports.get((project, checked_id))
        return None if value is None else validate_report(copy.deepcopy(value))


__all__ = ("FakeValidationRuleAdapter", "FakeValidationStoreAdapter")
