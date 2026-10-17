from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")
_RELATIVE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+\-]{0,255}$")
_UNSAFE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|password|credential|secret|"
    r"chain of thought|hidden reasoning|prompt|provider payload|"
    r"(?:^|[\s(])(?:file://|https?://|[A-Za-z]:[\\/]|\\\\)|\x00)",
    re.IGNORECASE,
)


class ApplyContract(BaseModel):
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


def preview_text(value: object, *, field: str, maximum: int = 64 * 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value)
    if not checked or len(checked) > maximum or "\x00" in checked:
        raise ValueError(f"{field} is unsafe")
    if re.search(r"(?:[A-Za-z]:[\\/]|\\\\|file://)", checked):
        raise ValueError(f"{field} contains an absolute path")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=160)
    if _IDENTIFIER.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def relative_reference(value: object, *, field: str = "relative_file_reference") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    normalized = value.replace("\\", "/")
    if (
        _RELATIVE.fullmatch(normalized) is None
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
        or ":" in normalized.split("/")[0]
        or ":" in normalized
    ):
        raise ValueError(f"{field} is invalid")
    return normalized


def utc_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{field} is invalid") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} is invalid")
    return value.astimezone(UTC)


class FingerprintedApplyContract(ApplyContract):
    fingerprint: str = "sha256:" + "0" * 64

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("apply contract fingerprint mismatch")
        return self


def deterministic_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:48]}"


def content_fingerprint(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


__all__ = (
    "ApplyContract",
    "FingerprintedApplyContract",
    "content_fingerprint",
    "deterministic_id",
    "fingerprint",
    "identifier",
    "preview_text",
    "relative_reference",
    "safe_text",
    "utc_datetime",
)
