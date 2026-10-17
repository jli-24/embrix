from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from .bringup_contracts import (
    BootEvidence,
    FlashEvidence,
    HardwareConnection,
    HardwareEvidence,
    HardwareSession,
    UartEvidence,
)

_KINDS = {
    "HardwareConnection": HardwareConnection,
    "HardwareSession": HardwareSession,
    "FlashEvidence": FlashEvidence,
    "BootEvidence": BootEvidence,
    "UartEvidence": UartEvidence,
}


class EvidenceStore:
    """Small append-only JSON projection for real hardware evidence."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)
        self._path = self._root / "hardware-evidence.json"
        self._lock = RLock()

    def save(self, value: HardwareEvidence) -> HardwareEvidence:
        checked = self._validate(value)
        with self._lock:
            records = self._read()
            existing = records.get(checked_id(checked))
            payload = {"kind": type(checked).__name__, "value": checked.model_dump(mode="json")}
            if existing is not None and existing != payload:
                raise ValueError("hardware evidence identity conflict")
            records[checked_id(checked)] = payload
            self._write(records)
        return checked

    def get(self, evidence_id: str) -> HardwareEvidence | None:
        with self._lock:
            item = self._read().get(evidence_id)
        if item is None:
            return None
        model = _KINDS.get(item.get("kind"))
        if model is None:
            raise ValueError("hardware evidence kind is invalid")
        return model.model_validate(item.get("value"), strict=False)

    def _validate(self, value: HardwareEvidence) -> HardwareEvidence:
        model = _KINDS.get(type(value).__name__)
        if model is None or type(value) is not model:
            raise TypeError("hardware evidence type is invalid")
        return model.model_validate(value.model_dump(mode="python"))

    def _read(self) -> dict[str, dict[str, object]]:
        if not self._path.is_file():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("hardware evidence store is unreadable") from error
        if type(raw) is not dict:
            raise ValueError("hardware evidence store is invalid")
        return raw

    def _write(self, records: dict[str, dict[str, object]]) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(self._path)


def checked_id(value: HardwareEvidence) -> str:
    evidence_id = getattr(value, "evidence_id", None)
    if evidence_id is not None:
        return evidence_id
    if type(value) is HardwareConnection:
        return value.connection_id
    if type(value) is HardwareSession:
        return value.session_id
    raise TypeError("hardware evidence identity is invalid")


__all__ = ("EvidenceStore",)
