"""Shared contracts for the Engineering Object Runtime MVP.

The runtime is deliberately in-memory in this phase.  These contracts are
storage-neutral and keep identity, provenance and fingerprint rules in one
place so a later persistence adapter cannot silently change semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict


class RuntimeContract(BaseModel):
    """Strict immutable DTO used at every MVP runtime boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        strict=True,
        hide_input_in_errors=True,
    )


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
REVISION_PATTERN = re.compile(r"^r[1-9][0-9]*$")
FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_FINGERPRINT = "sha256:" + "0" * 64


def normalize_text(value: str, *, field: str, allow_empty: bool = False) -> str:
    value = unicodedata.normalize("NFC", value).strip()
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) > 8192:
        raise ValueError(f"{field} exceeds the runtime length limit")
    return value


def validate_identifier(value: str, *, field: str) -> str:
    value = normalize_text(value, field=field)
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field} has an invalid identifier")
    return value


def validate_revision(value: str) -> str:
    value = normalize_text(value, field="revision")
    if not REVISION_PATTERN.fullmatch(value):
        raise ValueError("revision must use rN format")
    return value


def validate_fingerprint(value: str, *, field: str = "fingerprint") -> str:
    value = normalize_text(value, field=field)
    if not FINGERPRINT_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return value


def validate_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def validate_text_tuple(values: Iterable[str], *, field: str) -> tuple[str, ...]:
    result = tuple(normalize_text(value, field=field) for value in values)
    if len(result) > 256:
        raise ValueError(f"{field} contains too many items")
    return result


def canonical_fingerprint(model: BaseModel, *, exclude: set[str] | None = None) -> str:
    """Fingerprint the canonical JSON representation of a contract."""

    excluded = set(exclude or ())
    payload = model.model_dump(mode="json", exclude=excluded)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def assert_project_ref(project_id: str, source_ref: str) -> None:
    """Reject the most common cross-project provenance mix-up.

    A provenance adapter may use any opaque source reference.  When it uses
    the conventional ``project-id:...`` form, the prefix is checked here.
    """

    prefix = source_ref.split(":", 1)[0]
    if prefix.startswith("project-") and prefix != project_id:
        raise ValueError("provenance source_ref belongs to another project")


def ensure_fingerprint(model: BaseModel, *, field: str = "fingerprint") -> None:
    expected = canonical_fingerprint(model, exclude={field})
    actual = getattr(model, field)
    if actual != expected:
        raise ValueError(f"{field} does not match the object content")
