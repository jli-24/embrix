from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_AUTO = "sha256:" + "0" * 64
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,127}$")
_UNSAFE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|"
    r"prompt|provider payload|hidden reasoning|chain of thought|raw log|"
    r"file://|https?://|[A-Za-z]:[\\/]|\\\\|\x00)",
    re.IGNORECASE,
)


class ChatContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


class ResponseType(StrEnum):
    """Stable response presentation type shared by chat and catalog views."""

    CHAT = "CHAT"
    DOCUMENT = "DOCUMENT"
    ARTIFACT = "ARTIFACT"


def safe_text(value: object, *, field: str, maximum: int = 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
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


def utc_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} is invalid") from error
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field} is invalid")
    return value.astimezone(UTC)


def tuple_only(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return value


class FingerprintedChatContract(ChatContract):
    fingerprint: str = _AUTO

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        encoded = json.dumps(
            self.model_dump(mode="json", exclude={"fingerprint"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            legacy = self._legacy_fingerprint_candidates()
            if self.fingerprint not in legacy:
                raise ValueError("chat fingerprint mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        return ()


__all__ = (
    "ChatContract",
    "FingerprintedChatContract",
    "ResponseType",
    "fingerprint",
    "identifier",
    "safe_text",
    "tuple_only",
    "utc_datetime",
)
