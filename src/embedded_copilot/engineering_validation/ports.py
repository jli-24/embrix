from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ValidationFinding, ValidationReport, ValidationRequest


@runtime_checkable
class ValidationRulePort(Protocol):
    def validate(
        self, request: ValidationRequest
    ) -> tuple[ValidationFinding, ...]: ...


@runtime_checkable
class ValidationStorePort(Protocol):
    def save_finding(self, finding: ValidationFinding) -> ValidationFinding: ...

    def get_finding(
        self, project_id: str, finding_id: str
    ) -> ValidationFinding | None: ...

    def save_report(self, report: ValidationReport) -> ValidationReport: ...

    def get_report(
        self, project_id: str, report_id: str
    ) -> ValidationReport | None: ...


__all__ = ("ValidationRulePort", "ValidationStorePort")
