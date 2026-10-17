from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,127}$")
_FP = re.compile(r"^sha256:[0-9a-f]{64}$")
_UNSAFE = re.compile(
    r"(?:\x00|[A-Za-z]:[\\/]|\\\\|file://|(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret)\s*[:=])",
    re.IGNORECASE,
)
ZERO = "sha256:" + "0" * 64


class Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


def text(value: object, field: str, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > maximum or _UNSAFE.search(value):
        raise ValueError(f"{field} is unsafe")
    return value


def ident(value: object, field: str) -> str:
    value = text(value, field, 128)
    if not _ID.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def tuple_values(value: object, field: str) -> tuple:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return tuple(copy.deepcopy(value))


def fp(value: object, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or not _FP.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def confidence(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("confidence is invalid")
    return value


def canonical(
    value: BaseModel, exclude: set[str] | frozenset[str] = frozenset()
) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def finalize(cls: type[BaseModel], values: dict[str, Any]) -> Any:
    values = copy.deepcopy(values)
    values.setdefault("fingerprint", ZERO)
    obj = cls.model_validate(values)
    if obj.fingerprint == ZERO:
        object.__setattr__(obj, "fingerprint", canonical(obj, exclude={"fingerprint"}))
    return obj


def validate_copy(value: object, cls: type[BaseModel]) -> Any:
    if not isinstance(value, cls):
        raise TypeError("invalid contract")
    return cls.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class Fingerprinted(Contract):
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset()
    fingerprint: str = ZERO

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fp(value)

    @model_validator(mode="after")
    def _verify(self):
        expected = canonical(self, exclude={"fingerprint", *self._fingerprint_exclude})
        if self.fingerprint == ZERO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            legacy = self._legacy_fingerprint_candidates()
            if self.fingerprint not in legacy:
                raise ValueError("fingerprint mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        """Return fingerprints accepted only for explicitly compatible records."""

        return ()
