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
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,255}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_UNSAFE_TEXT = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)\s*[:=]"
    r"|raw\s+(?:llm\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|\x00|\r|\n)",
    re.IGNORECASE,
)


class EvolutionContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class EvolutionTarget(StrEnum):
    MEMORY = "MEMORY"
    GRAPH = "GRAPH"
    RETRIEVAL = "RETRIEVAL"


class EvolutionProposalStatus(StrEnum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class EvolutionReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


ModelT = TypeVar("ModelT", bound=BaseModel)


def safe_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE_TEXT.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if _IDENTIFIER.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def reference(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=256)
    if _REFERENCE.fullmatch(checked) is None:
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


def evidence_refs(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError("evidence_refs must be a tuple")
    checked = tuple(
        reference(item, field="evidence_ref") for item in copy.deepcopy(value)
    )
    if not checked or len(checked) != len(set(checked)):
        raise ValueError("evidence_refs must be non-empty and unique")
    return tuple(sorted(checked))


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


def _with_fingerprint(
    cls: type[ModelT], values: dict[str, object], *, exclude: set[str]
) -> ModelT:
    material = dict(values)
    material.pop("fingerprint", None)
    provisional = cls.model_construct(
        **{**material, "fingerprint": "sha256:" + "0" * 64}
    )
    material["fingerprint"] = canonical_fingerprint(provisional, exclude=exclude)
    return cls.model_validate(material)


class EvolutionProposalRequest(EvolutionContract):
    request_id: str
    project_id: str
    knowledge_id: str
    knowledge_fingerprint: str
    target: EvolutionTarget
    created_by: str
    created_at: datetime
    fingerprint: str

    @field_validator(
        "request_id", "project_id", "knowledge_id", "created_by", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("knowledge_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="created_at")

    @classmethod
    def create(cls, **values: object) -> EvolutionProposalRequest:
        material = dict(values)
        for field in ("request_id", "project_id", "knowledge_id", "created_by"):
            material[field] = identifier(material[field], field=field)
        material["knowledge_fingerprint"] = fingerprint(
            material["knowledge_fingerprint"], field="knowledge_fingerprint"
        )
        material["target"] = EvolutionTarget(material["target"])
        material["created_at"] = utc_timestamp(
            material["created_at"], field="created_at"
        )
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> EvolutionProposalRequest:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("proposal request fingerprint mismatch")
        return self


class KnowledgeEvolutionProposal(EvolutionContract):
    proposal_id: str
    request_id: str
    project_id: str
    source_knowledge_id: str
    source_knowledge_fingerprint: str
    source_candidate_id: str
    source_approved_by: str
    target: EvolutionTarget
    claim: str
    evidence_refs: tuple[str, ...]
    created_by: str
    created_at: datetime
    status: EvolutionProposalStatus
    fingerprint: str

    @field_validator(
        "proposal_id",
        "request_id",
        "project_id",
        "source_knowledge_id",
        "source_candidate_id",
        "source_approved_by",
        "created_by",
        mode="before",
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_knowledge_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("claim", mode="before")
    @classmethod
    def validate_claim(cls, value: object) -> str:
        return safe_text(value, field="claim", maximum=2048)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return evidence_refs(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="created_at")

    @classmethod
    def create(cls, **values: object) -> KnowledgeEvolutionProposal:
        material = dict(values)
        for field in (
            "proposal_id",
            "request_id",
            "project_id",
            "source_knowledge_id",
            "source_candidate_id",
            "source_approved_by",
            "created_by",
        ):
            material[field] = identifier(material[field], field=field)
        material["source_knowledge_fingerprint"] = fingerprint(
            material["source_knowledge_fingerprint"],
            field="source_knowledge_fingerprint",
        )
        material["target"] = EvolutionTarget(material["target"])
        material["claim"] = safe_text(material["claim"], field="claim", maximum=2048)
        material["evidence_refs"] = evidence_refs(material["evidence_refs"])
        material["created_at"] = utc_timestamp(
            material["created_at"], field="created_at"
        )
        material["status"] = EvolutionProposalStatus(material["status"])
        return _with_fingerprint(cls, material, exclude={"fingerprint", "status"})

    def with_status(
        self, status: EvolutionProposalStatus
    ) -> KnowledgeEvolutionProposal:
        return KnowledgeEvolutionProposal.model_validate(
            {
                **self.model_dump(mode="python"),
                "status": EvolutionProposalStatus(status),
                "fingerprint": self.fingerprint,
            }
        )

    @model_validator(mode="after")
    def verify(self) -> KnowledgeEvolutionProposal:
        if self.fingerprint != canonical_fingerprint(
            self, exclude={"fingerprint", "status"}
        ):
            raise ValueError("evolution proposal fingerprint mismatch")
        return self


class EvolutionReviewRequest(EvolutionContract):
    review_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    reviewer: str
    decision: EvolutionReviewDecision
    comment: str
    reviewed_at: datetime
    fingerprint: str

    @field_validator(
        "review_id", "project_id", "proposal_id", "reviewer", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("comment", mode="before")
    @classmethod
    def validate_comment(cls, value: object) -> str:
        return safe_text(value, field="comment", maximum=2048)

    @field_validator("reviewed_at", mode="before")
    @classmethod
    def validate_reviewed_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="reviewed_at")

    @classmethod
    def create(cls, **values: object) -> EvolutionReviewRequest:
        material = dict(values)
        for field in ("review_id", "project_id", "proposal_id", "reviewer"):
            material[field] = identifier(material[field], field=field)
        material["proposal_fingerprint"] = fingerprint(
            material["proposal_fingerprint"], field="proposal_fingerprint"
        )
        material["decision"] = EvolutionReviewDecision(material["decision"])
        material["comment"] = safe_text(
            material["comment"], field="comment", maximum=2048
        )
        material["reviewed_at"] = utc_timestamp(
            material["reviewed_at"], field="reviewed_at"
        )
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> EvolutionReviewRequest:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("evolution review fingerprint mismatch")
        return self


class EvolutionApplyRequest(EvolutionContract):
    application_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    review_fingerprint: str
    applied_by: str
    applied_at: datetime
    fingerprint: str

    @field_validator(
        "application_id", "project_id", "proposal_id", "applied_by", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "proposal_fingerprint", "review_fingerprint", "fingerprint", mode="before"
    )
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("applied_at", mode="before")
    @classmethod
    def validate_applied_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="applied_at")

    @classmethod
    def create(cls, **values: object) -> EvolutionApplyRequest:
        material = dict(values)
        for field in ("application_id", "project_id", "proposal_id", "applied_by"):
            material[field] = identifier(material[field], field=field)
        for field in ("proposal_fingerprint", "review_fingerprint"):
            material[field] = fingerprint(material[field], field=field)
        material["applied_at"] = utc_timestamp(
            material["applied_at"], field="applied_at"
        )
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> EvolutionApplyRequest:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("evolution apply request fingerprint mismatch")
        return self


class EvolutionSinkReceipt(EvolutionContract):
    receipt_id: str
    application_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    review_fingerprint: str
    target: EvolutionTarget
    accepted_at: datetime
    fingerprint: str

    @field_validator(
        "receipt_id", "application_id", "project_id", "proposal_id", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "proposal_fingerprint", "review_fingerprint", "fingerprint", mode="before"
    )
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("accepted_at", mode="before")
    @classmethod
    def validate_accepted_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="accepted_at")

    @classmethod
    def create(cls, **values: object) -> EvolutionSinkReceipt:
        material = dict(values)
        for field in ("receipt_id", "application_id", "project_id", "proposal_id"):
            material[field] = identifier(material[field], field=field)
        for field in ("proposal_fingerprint", "review_fingerprint"):
            material[field] = fingerprint(material[field], field=field)
        material["target"] = EvolutionTarget(material["target"])
        material["accepted_at"] = utc_timestamp(
            material["accepted_at"], field="accepted_at"
        )
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> EvolutionSinkReceipt:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("evolution sink receipt fingerprint mismatch")
        return self


class EvolutionApplicationRecord(EvolutionContract):
    request: EvolutionApplyRequest
    receipt: EvolutionSinkReceipt
    fingerprint: str

    @field_validator("request", mode="before")
    @classmethod
    def validate_request(cls, value: object) -> EvolutionApplyRequest:
        if type(value) is EvolutionApplyRequest:
            material = value.model_dump(mode="python")
        elif type(value) is dict:
            material = value
        else:
            raise TypeError("apply request is invalid")
        return EvolutionApplyRequest.model_validate(copy.deepcopy(material))

    @field_validator("receipt", mode="before")
    @classmethod
    def validate_receipt(cls, value: object) -> EvolutionSinkReceipt:
        if type(value) is EvolutionSinkReceipt:
            material = value.model_dump(mode="python")
        elif type(value) is dict:
            material = value
        else:
            raise TypeError("sink receipt is invalid")
        return EvolutionSinkReceipt.model_validate(copy.deepcopy(material))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> EvolutionApplicationRecord:
        request = values.get("request")
        receipt = values.get("receipt")
        if type(request) is not EvolutionApplyRequest:
            raise TypeError("apply request is invalid")
        if type(receipt) is not EvolutionSinkReceipt:
            raise TypeError("sink receipt is invalid")
        material = {
            "request": EvolutionApplyRequest.model_validate(
                copy.deepcopy(request.model_dump(mode="python"))
            ),
            "receipt": EvolutionSinkReceipt.model_validate(
                copy.deepcopy(receipt.model_dump(mode="python"))
            ),
        }
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> EvolutionApplicationRecord:
        request = self.request
        receipt = self.receipt
        if (
            receipt.application_id != request.application_id
            or receipt.project_id != request.project_id
            or receipt.proposal_id != request.proposal_id
            or receipt.proposal_fingerprint != request.proposal_fingerprint
            or receipt.review_fingerprint != request.review_fingerprint
            or receipt.accepted_at < request.applied_at
        ):
            raise ValueError("application record binding mismatch")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("application record fingerprint mismatch")
        return self


def proposal_id(
    project_id: str,
    knowledge_id: str,
    knowledge_fingerprint: str,
    target: EvolutionTarget,
) -> str:
    material = (
        f"{identifier(project_id, field='project_id')}:"
        f"{identifier(knowledge_id, field='knowledge_id')}:"
        f"{fingerprint(knowledge_fingerprint, field='knowledge_fingerprint')}:"
        f"{EvolutionTarget(target).value}"
    ).encode()
    return "evolution-" + hashlib.sha256(material).hexdigest()[:48]


__all__ = (
    "EvolutionApplicationRecord",
    "EvolutionApplyRequest",
    "EvolutionContract",
    "EvolutionProposalRequest",
    "EvolutionProposalStatus",
    "EvolutionReviewDecision",
    "EvolutionReviewRequest",
    "EvolutionSinkReceipt",
    "EvolutionTarget",
    "KnowledgeEvolutionProposal",
    "canonical_fingerprint",
    "identifier",
    "proposal_id",
)
