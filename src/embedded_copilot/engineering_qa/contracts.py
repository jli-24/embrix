from __future__ import annotations

import hashlib
import json

from pydantic import field_validator, model_validator

from embedded_copilot.model_gateway.contracts import (
    ModelGatewayContract,
    ModelOpenQuestion,
    ModelRequirementProposal,
    ModelUsage,
    canonical_fingerprint,
    confidence,
    identifier,
    safe_text,
    tuple_only,
)

_AUTO = "sha256:" + "0" * 64


class EngineeringQuestion(ModelGatewayContract):
    question_id: str
    project_id: str
    question: str
    constraints: tuple[str, ...] = ()
    fingerprint: str = _AUTO

    @field_validator("question_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("question", mode="before")
    @classmethod
    def validate_question(cls, value: object) -> str:
        return safe_text(value, field="question", maximum=4096)

    @field_validator("constraints", mode="before")
    @classmethod
    def validate_constraints(cls, value: object) -> tuple[str, ...]:
        return tuple(safe_text(item, field="constraint", maximum=256) for item in tuple_only(value, field="constraints"))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        if not isinstance(value, str) or not value.startswith("sha256:"):
            raise ValueError("fingerprint is invalid")
        return value

    @model_validator(mode="after")
    def verify(self) -> EngineeringQuestion:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("engineering question fingerprint mismatch")
        return self


class ModelInfo(ModelGatewayContract):
    provider: str
    model_name: str
    finish_reason: str
    usage: ModelUsage | None = None
    fingerprint: str = _AUTO

    @field_validator("provider", "model_name", "finish_reason", mode="before")
    @classmethod
    def validate_names(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("usage", mode="before")
    @classmethod
    def validate_usage(cls, value: object) -> ModelUsage | None:
        return None if value is None else ModelUsage.model_validate(value)

    @model_validator(mode="after")
    def verify(self) -> ModelInfo:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("model info fingerprint mismatch")
        return self


class EngineeringAnswer(ModelGatewayContract):
    answer_id: str = ""
    question_id: str
    content: str
    confidence: float
    evidence_refs: tuple[str, ...]
    model_info: ModelInfo
    requirement_proposals: tuple[ModelRequirementProposal, ...] = ()
    open_questions: tuple[ModelOpenQuestion, ...] = ()
    fingerprint: str = _AUTO

    @field_validator("answer_id", mode="before")
    @classmethod
    def validate_answer_id(cls, value: object) -> str:
        return "" if value == "" else identifier(value, field="answer_id")

    @field_validator("question_id", mode="before")
    @classmethod
    def validate_question_id(cls, value: object) -> str:
        return identifier(value, field="question_id")

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value: object) -> str:
        return safe_text(value, field="content", maximum=16384, allow_newlines=True)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float:
        return confidence(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(identifier(item, field="evidence_ref") for item in tuple_only(value, field="evidence_refs"))

    @field_validator("model_info", mode="before")
    @classmethod
    def validate_model_info(cls, value: object) -> ModelInfo:
        return ModelInfo.model_validate(value)

    @model_validator(mode="after")
    def verify(self) -> EngineeringAnswer:
        material = self.model_dump(mode="json", exclude={"answer_id", "fingerprint"})
        derived = "answer-" + hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
        if not self.answer_id:
            object.__setattr__(self, "answer_id", derived)
        elif self.answer_id != derived:
            raise ValueError("engineering answer id mismatch")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("engineering answer fingerprint mismatch")
        return self


__all__ = ("EngineeringAnswer", "EngineeringQuestion", "ModelInfo")
