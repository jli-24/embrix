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
_LOGICAL_REFERENCE = re.compile(
    r"^(?:artifact|s3|workspace)://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,383}$"
)
_UNSAFE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret)\s*[:=]|"
    r"raw\s+(?:provider\s+)?(?:payload|response|output)|hidden\s+reasoning|"
    r"chain\s+of\s+thought|data:|base64|file://|\\\\|\x00|\r|\n",
    re.IGNORECASE,
)


class MultimodalContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


def safe_text(
    value: object, *, field: str, maximum: int = 2048, allow_newlines: bool = False
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} is invalid")  # noqa: TRY004 - Pydantic validator
    checked = unicodedata.normalize("NFC", value).strip()
    if (
        not checked
        or len(checked) > maximum
        or (not allow_newlines and ("\n" in checked or "\r" in checked))
        or _UNSAFE.search(checked)
    ):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if _ID.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def logical_reference(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=384)
    if _LOGICAL_REFERENCE.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def utc_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} is invalid")
    return value.astimezone(UTC)


def tuple_text(
    value: object, *, field: str, maximum_items: int = 128
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{field} must be a tuple")
    if len(value) > maximum_items:
        raise ValueError(f"{field} is too long")
    checked = tuple(
        safe_text(item, field=f"{field}_item", maximum=512) for item in value
    )
    if len(checked) != len(set(checked)):
        raise ValueError(f"{field} must be unique")
    return checked


def canonical_fingerprint(value: BaseModel, *, exclude: set[str]) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def deterministic_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:48]}"


class FingerprintedContract(MultimodalContract):
    fingerprint: str = _AUTO

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint_shape(cls, value: object) -> str:
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
    "FingerprintedContract",
    "MultimodalContract",
    "canonical_fingerprint",
    "deterministic_id",
    "fingerprint",
    "identifier",
    "logical_reference",
    "safe_text",
    "tuple_text",
    "utc_datetime",
)
