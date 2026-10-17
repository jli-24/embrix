from __future__ import annotations

import copy
import json
import re
from typing import Protocol, runtime_checkable

from .exceptions import MemoryOperationConflict
from .foundation import (
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
)

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}")


def _revalidate_record(value: object) -> FoundationEngineeringMemoryRecord:
    if type(value) is not FoundationEngineeringMemoryRecord:
        raise TypeError("foundation memory record is invalid")
    return FoundationEngineeringMemoryRecord.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _revalidate_query(value: object) -> FoundationMemoryQuery:
    if type(value) is not FoundationMemoryQuery:
        raise TypeError("foundation memory query is invalid")
    return FoundationMemoryQuery.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(item.casefold() for item in _TOKEN.findall(value))


def _search_text(record: FoundationEngineeringMemoryRecord) -> str:
    payload = json.dumps(
        record.payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{record.memory_id} {record.memory_type.value} {payload}"


@runtime_checkable
class EngineeringMemoryRepositoryPort(Protocol):
    def save(
        self, record: FoundationEngineeringMemoryRecord
    ) -> FoundationEngineeringMemoryRecord: ...

    def search(
        self, query: FoundationMemoryQuery
    ) -> tuple[FoundationEngineeringMemoryRecord, ...]: ...


class InMemoryEngineeringMemoryRepository:
    __slots__ = ("_records",)

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], FoundationEngineeringMemoryRecord] = {}

    def save(
        self, record: FoundationEngineeringMemoryRecord
    ) -> FoundationEngineeringMemoryRecord:
        checked = _revalidate_record(record)
        key = checked.identity
        existing = self._records.get(key)
        if existing is not None:
            if existing.fingerprint != checked.fingerprint:
                raise MemoryOperationConflict()
            return _revalidate_record(existing)
        self._records[key] = checked
        return _revalidate_record(checked)

    def search(
        self, query: FoundationMemoryQuery
    ) -> tuple[FoundationEngineeringMemoryRecord, ...]:
        checked = _revalidate_query(query)
        query_tokens = _tokens(checked.query)
        values = [
            value
            for value in self._records.values()
            if value.tenant_id == checked.tenant_id
            and value.project_id == checked.project_id
            and (
                checked.memory_type is None
                or value.memory_type is checked.memory_type
            )
        ]

        def sort_key(value: FoundationEngineeringMemoryRecord) -> tuple[object, ...]:
            score = len(query_tokens & _tokens(_search_text(value)))
            return (-score, value.created_at, value.memory_id, value.fingerprint)

        ordered = sorted(values, key=sort_key)[: checked.limit]
        return tuple(_revalidate_record(value) for value in ordered)


__all__ = (
    "EngineeringMemoryRepositoryPort",
    "InMemoryEngineeringMemoryRepository",
)
