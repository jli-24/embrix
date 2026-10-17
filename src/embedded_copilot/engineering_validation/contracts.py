from __future__ import annotations

import copy

from .models import (
    ValidationArtifactReference,
    ValidationFinding,
    ValidationReport,
    ValidationRequest,
    ValidationRule,
    identifier,
)


def _validate_exact(value: object, cls, *, message: str):
    if type(value) is not cls:
        raise TypeError(message)
    return cls.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_artifact_reference(value: object) -> ValidationArtifactReference:
    return _validate_exact(
        value,
        ValidationArtifactReference,
        message="artifact reference is invalid",
    )


def validate_request(value: object) -> ValidationRequest:
    return _validate_exact(value, ValidationRequest, message="request is invalid")


def validate_rule(value: object) -> ValidationRule:
    return _validate_exact(value, ValidationRule, message="rule is invalid")


def validate_finding(value: object) -> ValidationFinding:
    return _validate_exact(value, ValidationFinding, message="finding is invalid")


def validate_report(value: object) -> ValidationReport:
    return _validate_exact(value, ValidationReport, message="report is invalid")


def validate_project_id(value: object) -> str:
    return identifier(value, field="project_id")


def validate_finding_id(value: object) -> str:
    return identifier(value, field="finding_id")


def validate_report_id(value: object) -> str:
    return identifier(value, field="report_id")


__all__ = (
    "validate_artifact_reference",
    "validate_finding",
    "validate_finding_id",
    "validate_project_id",
    "validate_report",
    "validate_report_id",
    "validate_request",
    "validate_rule",
)
