from __future__ import annotations

import copy
import re
import unicodedata
from typing import Final

from pydantic import Field, field_validator

from embedded_copilot.engineering_context import EngineeringContextSnapshot

from ..contracts import (
    ModelGatewayContract,
    canonical_fingerprint,
    confidence,
    identifier,
    safe_text,
    tuple_only,
)

_IMAGE_REFERENCE = re.compile(
    r"^(?:artifact|s3|workspace)://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,511}$"
)
_METADATA_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_METADATA_UNSAFE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|credential|password|secret|"
    r"endpoint|history|raw[_ -]?(?:payload|response|conversation)|hidden[_ -]?reasoning)",
    re.IGNORECASE,
)

_MAX_COLLECTION_ITEMS: Final[int] = 128


def _metadata(value: object, *, field: str) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple:
        raise ValueError(f"{field} must be a tuple")
    entries = tuple_only(value, field=field)
    if len(entries) > 32:
        raise ValueError(f"{field} is too long")
    checked: list[tuple[str, str]] = []
    keys: set[str] = set()
    for entry in entries:
        if type(entry) is not tuple or len(entry) != 2:
            raise TypeError(f"{field} entries are invalid")
        key, item = entry
        if not isinstance(key, str):
            raise TypeError(f"{field} key is invalid")
        key = key.strip()
        if not _METADATA_KEY.fullmatch(key) or _METADATA_UNSAFE.search(key):
            raise ValueError(f"{field} key is unsafe")
        if key in keys:
            raise ValueError(f"{field} keys must be unique")
        keys.add(key)
        checked_value = safe_text(item, field=f"{field}_value", maximum=512)
        if _METADATA_UNSAFE.search(checked_value):
            raise ValueError(f"{field} value is unsafe")
        checked.append((key, checked_value))
    return tuple(checked)


def _collection(value: object, *, field: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{field} must be a tuple")
    values = tuple_only(value, field=field)
    if len(values) > _MAX_COLLECTION_ITEMS:
        raise ValueError(f"{field} is too long")
    return tuple(safe_text(item, field=f"{field}_item", maximum=256) for item in values)


def _image_reference(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("image_reference is invalid")  # noqa: TRY004
    checked = unicodedata.normalize("NFC", value).strip()
    if (
        not checked
        or "\x00" in checked
        or "\n" in checked
        or "\r" in checked
        or _METADATA_UNSAFE.search(checked)
        or not _IMAGE_REFERENCE.fullmatch(checked)
    ):
        raise ValueError("image_reference is invalid")
    return checked


class VisionRequest(ModelGatewayContract):
    request_id: str
    prompt: str
    image_reference: str
    context: EngineeringContextSnapshot
    metadata: tuple[tuple[str, str], ...] = Field(default=(), max_length=32)

    @field_validator("request_id", mode="before")
    @classmethod
    def validate_request_id(cls, value: object) -> str:
        return identifier(value, field="request_id")

    @field_validator("prompt", mode="before")
    @classmethod
    def validate_prompt(cls, value: object) -> str:
        return safe_text(value, field="prompt", maximum=4096)

    @field_validator("image_reference", mode="before")
    @classmethod
    def validate_image_reference(cls, value: object) -> str:
        return _image_reference(value)

    @field_validator("context", mode="before")
    @classmethod
    def validate_context(cls, value: object) -> EngineeringContextSnapshot:
        if isinstance(value, EngineeringContextSnapshot):
            material = value.model_dump(mode="python")
        elif isinstance(value, dict):
            material = value
        else:
            raise TypeError("context is invalid")
        return EngineeringContextSnapshot.model_validate(copy.deepcopy(material))

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> tuple[tuple[str, str], ...]:
        return _metadata(value, field="metadata")

    @property
    def fingerprint(self) -> str:
        """Return the canonical identity; it is never accepted as input."""
        return canonical_fingerprint(self, exclude={"fingerprint"})


class VisionObservation(ModelGatewayContract):
    observation_id: str
    objects: tuple[str, ...] = Field(default=(), max_length=_MAX_COLLECTION_ITEMS)
    components: tuple[str, ...] = Field(default=(), max_length=_MAX_COLLECTION_ITEMS)
    interfaces: tuple[str, ...] = Field(default=(), max_length=_MAX_COLLECTION_ITEMS)
    issues: tuple[str, ...] = Field(default=(), max_length=_MAX_COLLECTION_ITEMS)
    confidence: float
    evidence: tuple[str, ...] = Field(default=(), max_length=_MAX_COLLECTION_ITEMS)

    @field_validator("observation_id", mode="before")
    @classmethod
    def validate_observation_id(cls, value: object) -> str:
        return identifier(value, field="observation_id")

    @field_validator(
        "objects", "components", "interfaces", "issues", "evidence", mode="before"
    )
    @classmethod
    def validate_collections(cls, value: object, info) -> tuple[str, ...]:
        return _collection(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_observation_confidence(cls, value: object) -> float:
        return confidence(value)

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self, exclude={"fingerprint"})


class VisionResponse(ModelGatewayContract):
    provider: str
    model: str
    observation: VisionObservation
    metadata: tuple[tuple[str, str], ...] = Field(default=(), max_length=32)

    @field_validator("provider", "model", mode="before")
    @classmethod
    def validate_binding(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("observation", mode="before")
    @classmethod
    def validate_observation(cls, value: object) -> VisionObservation:
        if isinstance(value, VisionObservation):
            material = value.model_dump(mode="python")
        elif isinstance(value, dict):
            material = value
        else:
            raise TypeError("observation is invalid")
        return VisionObservation.model_validate(copy.deepcopy(material))

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_response_metadata(cls, value: object) -> tuple[tuple[str, str], ...]:
        return _metadata(value, field="metadata")

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self, exclude={"fingerprint"})


def validate_vision_request(value: object) -> VisionRequest:
    if type(value) is not VisionRequest:
        raise TypeError("vision request is invalid")
    return VisionRequest.model_validate(copy.deepcopy(value.model_dump(mode="python")))


def validate_vision_observation(value: object) -> VisionObservation:
    if type(value) is not VisionObservation:
        raise TypeError("vision observation is invalid")
    return VisionObservation.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def validate_vision_response(value: object) -> VisionResponse:
    if type(value) is not VisionResponse:
        raise TypeError("vision response is invalid")
    return VisionResponse.model_validate(copy.deepcopy(value.model_dump(mode="python")))


__all__ = (
    "VisionObservation",
    "VisionRequest",
    "VisionResponse",
    "validate_vision_observation",
    "validate_vision_request",
    "validate_vision_response",
)
