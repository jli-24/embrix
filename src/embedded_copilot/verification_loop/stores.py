from __future__ import annotations

import copy
import hashlib
import json
import threading
from pathlib import Path

from embedded_copilot.engineering_copilot.common import ident

from .contracts import FunctionalVerificationRecord


def _record(value: object) -> FunctionalVerificationRecord:
    if type(value) is not FunctionalVerificationRecord:
        raise TypeError("functional verification record is invalid")
    return FunctionalVerificationRecord.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


class EphemeralFunctionalVerificationStore:
    """Test-only in-memory store; production composition uses the durable store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[tuple[str, str], FunctionalVerificationRecord] = {}

    def put(self, record: FunctionalVerificationRecord) -> FunctionalVerificationRecord:
        checked = _record(record)
        key = (checked.result.project_id, checked.result.verification_result_id)
        with self._lock:
            existing = self._values.get(key)
            if existing is not None and existing.fingerprint != checked.fingerprint:
                raise ValueError("functional verification identity conflict")
            self._values[key] = checked
        return _record(checked)

    def get(self, project_id: str, verification_result_id: str) -> FunctionalVerificationRecord | None:
        key = (ident(project_id, "project_id"), ident(verification_result_id, "verification_result_id"))
        with self._lock:
            value = self._values.get(key)
        return None if value is None else _record(value)


class JsonFunctionalVerificationStore:
    """Durable atomic projection of a functional result and its evidence."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, project_id: str, verification_result_id: str) -> Path:
        project = ident(project_id, "project_id")
        result = ident(verification_result_id, "verification_result_id")
        digest = hashlib.sha256(f"{project}|{result}".encode()).hexdigest()
        return self._root / f"functional-verification-{digest}.json"

    def put(self, record: FunctionalVerificationRecord) -> FunctionalVerificationRecord:
        checked = _record(record)
        path = self._path(checked.result.project_id, checked.result.verification_result_id)
        payload = json.dumps(
            checked.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )
        with self._lock:
            if path.exists():
                existing = _record(
                    FunctionalVerificationRecord.model_validate(
                        json.loads(path.read_text(encoding="utf-8")), strict=False
                    )
                )
                if existing.fingerprint != checked.fingerprint:
                    raise ValueError("functional verification identity conflict")
            else:
                temporary = path.with_suffix(".tmp")
                temporary.write_text(payload, encoding="utf-8")
                temporary.replace(path)
        return _record(checked)

    def get(self, project_id: str, verification_result_id: str) -> FunctionalVerificationRecord | None:
        path = self._path(project_id, verification_result_id)
        with self._lock:
            if not path.is_file():
                return None
            return _record(
                FunctionalVerificationRecord.model_validate(
                    json.loads(path.read_text(encoding="utf-8")), strict=False
                )
            )


__all__ = (
    "EphemeralFunctionalVerificationStore",
    "JsonFunctionalVerificationStore",
)
