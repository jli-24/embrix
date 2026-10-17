from __future__ import annotations

import copy
import json
import re
import threading
from pathlib import Path

from .contracts import PreparedSimulation, SimulationEvidence, SimulationResult, validate_result

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")


def _key(value: object) -> str:
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        raise ValueError("ephemeral simulation key is invalid")
    return value


def _prepared(value: object) -> PreparedSimulation:
    if type(value) is not PreparedSimulation:
        raise TypeError("prepared simulation is invalid")
    return PreparedSimulation.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


class EphemeralPreparedSimulationStore:
    """Process-local handoff for one existing TestOperation.

    Values are removed before adapter execution. This is intentionally not a
    persistent simulation-session model.
    """

    __slots__ = ("_lock", "_values")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, PreparedSimulation] = {}

    def put(self, key: str, value: PreparedSimulation) -> PreparedSimulation:
        checked_key = _key(key)
        checked = _prepared(value)
        with self._lock:
            existing = self._values.get(checked_key)
            if existing is not None and existing.fingerprint != checked.fingerprint:
                raise ValueError("prepared simulation identity conflict")
            self._values[checked_key] = checked
        return _prepared(checked)

    def take(self, key: str) -> PreparedSimulation | None:
        checked_key = _key(key)
        with self._lock:
            value = self._values.pop(checked_key, None)
        return None if value is None else _prepared(value)


class EphemeralSimulationResultStore:
    """Process-local normalized result store; never a session database."""

    __slots__ = ("_lock", "_values")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: dict[str, SimulationResult] = {}

    def put(self, operation_id: str, value: SimulationResult) -> SimulationResult:
        checked_id = _key(operation_id)
        checked = validate_result(value)
        if checked.operation_id != checked_id:
            raise ValueError("simulation result operation binding mismatch")
        with self._lock:
            existing = self._values.get(checked_id)
            if existing is not None and existing.fingerprint != checked.fingerprint:
                raise ValueError("simulation result identity conflict")
            self._values[checked_id] = checked
        return validate_result(checked)

    def get(self, operation_id: str) -> SimulationResult | None:
        checked_id = _key(operation_id)
        with self._lock:
            value = self._values.get(checked_id)
        return None if value is None else validate_result(value)


class JsonSimulationResultStore:
    """Small durable result projection used by the local simulator boundary."""

    __slots__ = ("_lock", "_root")

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, operation_id: str) -> Path:
        return self._root / f"{_key(operation_id)}.json"

    def put(self, operation_id: str, value: SimulationResult) -> SimulationResult:
        checked = validate_result(value)
        checked_id = _key(operation_id)
        if checked.operation_id != checked_id:
            raise ValueError("simulation result operation binding mismatch")
        path = self._path(checked_id)
        payload = json.dumps(checked.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self._lock:
            if path.exists() and path.read_text(encoding="utf-8") != payload:
                existing = validate_result(SimulationResult.model_validate(json.loads(path.read_text(encoding="utf-8")), strict=False))
                if existing.fingerprint != checked.fingerprint:
                    raise ValueError("simulation result identity conflict")
            else:
                path.write_text(payload, encoding="utf-8")
        return validate_result(checked)

    def get(self, operation_id: str) -> SimulationResult | None:
        path = self._path(operation_id)
        with self._lock:
            if not path.is_file():
                return None
            return validate_result(SimulationResult.model_validate(json.loads(path.read_text(encoding="utf-8")), strict=False))


class JsonSimulationEvidenceStore:
    """Durable sanitized evidence projection; raw backend output is never stored."""

    __slots__ = ("_lock", "_root")

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, evidence_id: str) -> Path:
        return self._root / f"{_key(evidence_id)}.json"

    def put(self, evidence: SimulationEvidence) -> SimulationEvidence:
        checked = SimulationEvidence.model_validate(copy.deepcopy(evidence.model_dump(mode="python")))
        path = self._path(checked.evidence_id)
        payload = json.dumps(checked.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with self._lock:
            if path.exists() and path.read_text(encoding="utf-8") != payload:
                existing = SimulationEvidence.model_validate(json.loads(path.read_text(encoding="utf-8")), strict=False)
                if existing.fingerprint != checked.fingerprint:
                    raise ValueError("simulation evidence identity conflict")
            else:
                path.write_text(payload, encoding="utf-8")
        return SimulationEvidence.model_validate(copy.deepcopy(checked.model_dump(mode="python")))

    def get(self, evidence_id: str) -> SimulationEvidence | None:
        path = self._path(evidence_id)
        with self._lock:
            if not path.is_file():
                return None
            return SimulationEvidence.model_validate(json.loads(path.read_text(encoding="utf-8")), strict=False)


__all__ = (
    "EphemeralPreparedSimulationStore",
    "EphemeralSimulationResultStore",
    "JsonSimulationEvidenceStore",
    "JsonSimulationResultStore",
)
