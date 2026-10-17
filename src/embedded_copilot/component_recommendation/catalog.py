from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .contracts import ComponentSourcePort, ComponentSourceRecord

_APPROVED_RESOURCE_SIZE = 13214
_APPROVED_RESOURCE_NAME = "approved_components_v1.json"
_APPROVED_SOURCE_IDS = ("st-stm32f103c8t6-lqfp48-ds5319-r20",)
_APPROVED_RESOURCE_SHA256 = "ff19bb238ff76e53de07f3ad968970bc42c10bf4a0c6ba44ae229198294876d2"


def _approved_resource_path() -> Path:
    return Path(__file__).resolve().parent / "resources" / _APPROVED_RESOURCE_NAME


def _validated_approved_source(data: bytes) -> StaticComponentSource:
    if hashlib.sha256(data).hexdigest() != _APPROVED_RESOURCE_SHA256:
        raise ValueError("approved component resource digest mismatch")
    payload = json.loads(data)
    if type(payload) is not dict or set(payload) != {"schema_version", "records"}:
        raise ValueError("approved component resource shape is invalid")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("approved component resource version is invalid")
    raw_records = payload["records"]
    if type(raw_records) is not list or len(raw_records) != len(_APPROVED_SOURCE_IDS):
        raise ValueError("approved component record count is invalid")
    records = tuple(ComponentSourceRecord.model_validate(item, strict=False) for item in raw_records)
    if tuple(item.source_id for item in records) != _APPROVED_SOURCE_IDS:
        raise ValueError("approved component source identity is invalid")
    return StaticComponentSource(records)


def _read_approved_resource() -> bytes:
    root = Path(__file__).resolve().parent / "resources"
    path = _approved_resource_path()
    if path.name != _APPROVED_RESOURCE_NAME or path.resolve().parent != root.resolve():
        raise ValueError("approved component resource layout is invalid")
    for entry in (root, path):
        metadata = entry.lstat()
        if entry.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise ValueError("approved component resource reparse point is invalid")
    if path.stat().st_size != _APPROVED_RESOURCE_SIZE:
        raise ValueError("approved component resource size is invalid")
    with path.open("rb") as stream:
        data = stream.read(_APPROVED_RESOURCE_SIZE + 1)
    if len(data) != _APPROVED_RESOURCE_SIZE:
        raise ValueError("approved component resource size is invalid")
    return data


def load_approved_component_source() -> StaticComponentSource:
    """Load the fixed, reviewed package resource without a request-controlled path."""
    return _validated_approved_source(_read_approved_resource())


class StaticComponentSource:
    """Read-only adapter for an approved, typed component/datasheet catalog."""

    __slots__ = ("_records",)

    def __init__(self, records: tuple[ComponentSourceRecord, ...]) -> None:
        if type(records) is list:
            records = tuple(records)  # type: ignore[assignment]
        if type(records) is not tuple or not records:
            raise TypeError("component source records must be a non-empty tuple")
        checked = tuple(
            ComponentSourceRecord.model_validate(
                copy.deepcopy(item.model_dump(mode="python")), strict=False
            )
            for item in records
        )
        if len({item.source_id for item in checked}) != len(checked):
            raise ValueError("component source identifiers must be unique")
        self._records = checked

    @property
    def records(self) -> tuple[ComponentSourceRecord, ...]:
        return tuple(
            ComponentSourceRecord.model_validate(
                copy.deepcopy(item.model_dump(mode="python")), strict=False
            )
            for item in self._records
        )

    def search(
        self, project_id: str, architecture_id: str, requirement: object
    ) -> tuple[ComponentSourceRecord, ...]:
        del project_id, architecture_id
        role = str(getattr(requirement, "role", "")).casefold()
        category = str(getattr(requirement, "category", "")).casefold()
        protocols = {
            str(value).casefold()
            for value in getattr(requirement, "interface_requirements", ())
            if str(value).casefold() not in {"unknown", ""}
        }
        result: list[ComponentSourceRecord] = []
        for record in self._records:
            roles = {value.casefold() for value in record.supported_roles}
            if role not in roles and category not in roles and record.category.casefold() not in {role, category}:
                continue
            record_protocols = {value.protocol.casefold() for value in record.interface_facts}
            if protocols and not protocols.issubset(record_protocols):
                continue
            result.append(
                ComponentSourceRecord.model_validate(
                    copy.deepcopy(record.model_dump(mode="python")), strict=False
                )
            )
        return tuple(sorted(result, key=lambda item: (item.mpn.casefold(), item.source_id)))


def validate_component_source(value: object) -> ComponentSourcePort:
    if not isinstance(value, ComponentSourcePort):
        raise TypeError("component source port is invalid")
    return value


__all__ = ("StaticComponentSource", "validate_component_source")
