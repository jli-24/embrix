from __future__ import annotations

import copy
from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel

from embedded_copilot.engineering_review import ReviewRecord

from .models import (
    AuditEvent,
    AuditTrail,
    EngineeringAuditReport,
    EvidenceBundle,
    ReviewRecordReference,
    RiskAuditRequest,
    utc_timestamp,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def revalidate(value: object, cls: type[ModelT]) -> ModelT:
    if type(value) is not cls:
        raise TypeError(f"expected {cls.__name__}")
    return cls.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_review_record(value: object) -> ReviewRecord:
    return revalidate(value, ReviewRecord)


def validate_review_reference(value: object) -> ReviewRecordReference:
    return revalidate(value, ReviewRecordReference)


def validate_evidence_bundle(value: object) -> EvidenceBundle:
    return revalidate(value, EvidenceBundle)


def validate_event(value: object) -> AuditEvent:
    return revalidate(value, AuditEvent)


def validate_trail(value: object) -> AuditTrail:
    return revalidate(value, AuditTrail)


def validate_risk_request(value: object) -> RiskAuditRequest:
    return revalidate(value, RiskAuditRequest)


def validate_report(value: object) -> EngineeringAuditReport:
    return revalidate(value, EngineeringAuditReport)


def validate_generated_at(value: object) -> datetime:
    return utc_timestamp(value, field="generated_at")

