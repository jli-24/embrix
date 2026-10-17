from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_AUTO = "sha256:" + "0" * 64
_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_REF = re.compile(
    r"^(?:artifact|workspace|staging)://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,255}$"
)
_UNSAFE = re.compile(
    r"(?:api[_ -]?key|token|password|credential|secret|prompt|hidden reasoning|"
    r"chain of thought|stdout|stderr|diff --git|@@\s|file://|\\|\x00|\r|\n)",
    re.IGNORECASE,
)


class EDAContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


def safe_text(value: object, *, field: str, maximum: int = 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if _ID.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def logical_reference(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=256)
    if _REF.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def utc_datetime(value: object, *, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} is invalid")
    return value.astimezone(UTC)


def tuple_only(value: object, *, field: str) -> tuple:
    if type(value) is not tuple:
        raise ValueError(f"{field} must be a tuple")
    return value


def canonical_fingerprint(value: BaseModel, *, exclude: set[str]) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def deterministic_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:48]


class FingerprintedEDAContract(EDAContract):
    fingerprint: str = _AUTO

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("fingerprint mismatch")
        return self


__all__ = (
    "EDAContract",
    "FingerprintedEDAContract",
    "canonical_fingerprint",
    "deterministic_id",
    "fingerprint",
    "identifier",
    "logical_reference",
    "safe_text",
    "tuple_only",
    "utc_datetime",
)
