from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)\s*[:=]"
    r"|raw\s+(?:llm\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|\x00|\r|\n)",
    re.IGNORECASE,
)


class ReviewContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ReviewTargetType(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    VALIDATION = "VALIDATION"
    OPTIMIZATION = "OPTIMIZATION"
    EVOLUTION = "EVOLUTION"
    ENGINEERING_EVIDENCE = "ENGINEERING_EVIDENCE"


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    DECIDED = "DECIDED"
    CLOSED = "CLOSED"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


ModelT = TypeVar("ModelT", bound=BaseModel)


def safe_text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _SECRET.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if _IDENTIFIER.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def utc_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def canonical_fingerprint(
    value: BaseModel, *, exclude: set[str] | frozenset[str] = frozenset()
) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _with_fingerprint(cls: type[ModelT], values: dict[str, object]) -> ModelT:
    material = dict(values)
    material.pop("fingerprint", None)
    provisional = cls.model_construct(
        **{**material, "fingerprint": "sha256:" + "0" * 64}
    )
    material["fingerprint"] = canonical_fingerprint(
        provisional, exclude={"fingerprint"}
    )
    return cls.model_validate(material)


def _nested_exact(value: object, cls: type[ModelT], *, field: str) -> ModelT:
    if type(value) is cls:
        material = copy.deepcopy(value.model_dump(mode="python"))
    elif type(value) is dict:
        material = copy.deepcopy(value)
    else:
        raise TypeError(f"{field} is invalid")
    return cls.model_validate(material)


class ReviewTargetReference(ReviewContract):
    target_type: ReviewTargetType
    target_id: str
    target_fingerprint: str

    @field_validator("target_id", mode="before")
    @classmethod
    def validate_target_id(cls, value: object) -> str:
        return identifier(value, field="target_id")

    @field_validator("target_fingerprint", mode="before")
    @classmethod
    def validate_target_fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="target_fingerprint")

    @classmethod
    def create(cls, **values: object) -> ReviewTargetReference:
        material = dict(values)
        material["target_type"] = ReviewTargetType(material["target_type"])
        material["target_id"] = identifier(material["target_id"], field="target_id")
        material["target_fingerprint"] = fingerprint(
            material["target_fingerprint"], field="target_fingerprint"
        )
        return cls.model_validate(material)


class ReviewRequest(ReviewContract):
    review_id: str
    project_id: str
    target_reference: ReviewTargetReference
    requester: str
    created_at: datetime
    fingerprint: str

    @field_validator("review_id", "project_id", "requester", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target_reference", mode="before")
    @classmethod
    def validate_target_reference(
        cls, value: object
    ) -> ReviewTargetReference:
        return _nested_exact(
            value, ReviewTargetReference, field="target_reference"
        )

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="created_at")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ReviewRequest:
        material = dict(values)
        material["review_id"] = identifier(material["review_id"], field="review_id")
        material["project_id"] = identifier(
            material["project_id"], field="project_id"
        )
        material["target_reference"] = _nested_exact(
            material["target_reference"],
            ReviewTargetReference,
            field="target_reference",
        )
        material["requester"] = identifier(material["requester"], field="requester")
        material["created_at"] = utc_timestamp(
            material["created_at"], field="created_at"
        )
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> ReviewRequest:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("review request fingerprint mismatch")
        return self


class HumanDecision(ReviewContract):
    decision_id: str
    review_id: str
    project_id: str
    decision: ReviewDecision
    reviewer: str
    comment: str
    decided_at: datetime
    fingerprint: str

    @field_validator(
        "decision_id", "review_id", "project_id", "reviewer", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("comment", mode="before")
    @classmethod
    def validate_comment(cls, value: object) -> str:
        return safe_text(value, field="comment")

    @field_validator("decided_at", mode="before")
    @classmethod
    def validate_decided_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="decided_at")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> HumanDecision:
        material = dict(values)
        for field in ("decision_id", "review_id", "project_id", "reviewer"):
            material[field] = identifier(material[field], field=field)
        material["decision"] = ReviewDecision(material["decision"])
        material["comment"] = safe_text(material["comment"], field="comment")
        material["decided_at"] = utc_timestamp(
            material["decided_at"], field="decided_at"
        )
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> HumanDecision:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("human decision fingerprint mismatch")
        return self


class ReviewRecord(ReviewContract):
    review_request: ReviewRequest
    decision: HumanDecision
    status: ReviewStatus
    fingerprint: str

    @field_validator("review_request", mode="before")
    @classmethod
    def validate_review_request(cls, value: object) -> ReviewRequest:
        return _nested_exact(value, ReviewRequest, field="review_request")

    @field_validator("decision", mode="before")
    @classmethod
    def validate_decision(cls, value: object) -> HumanDecision:
        return _nested_exact(value, HumanDecision, field="decision")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(
        cls, *, review_request: object, decision: object
    ) -> ReviewRecord:
        request = _nested_exact(
            review_request, ReviewRequest, field="review_request"
        )
        checked_decision = _nested_exact(
            decision, HumanDecision, field="decision"
        )
        return _with_fingerprint(
            cls,
            {
                "review_request": request,
                "decision": checked_decision,
                "status": ReviewStatus.DECIDED,
            },
        )

    @model_validator(mode="after")
    def verify(self) -> ReviewRecord:
        if self.status is ReviewStatus.OPEN:
            raise ValueError("review record cannot be open")
        if (
            self.decision.review_id != self.review_request.review_id
            or self.decision.project_id != self.review_request.project_id
            or self.decision.decided_at < self.review_request.created_at
        ):
            raise ValueError("review record binding mismatch")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("review record fingerprint mismatch")
        return self


__all__ = (
    "HumanDecision",
    "ReviewContract",
    "ReviewDecision",
    "ReviewRecord",
    "ReviewRequest",
    "ReviewStatus",
    "ReviewTargetReference",
    "ReviewTargetType",
    "canonical_fingerprint",
    "fingerprint",
    "identifier",
    "safe_text",
    "utc_timestamp",
)
