from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from .contracts import FabricationRecord


def _key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FabricationStore:
    """Durable immutable store for fabrication readiness records.

    The record body is self-fingerprinted and contains only relative artifact
    paths. The filesystem key is hashed so project or record identifiers can
    never become path fragments.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve(strict=False)
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, record: FabricationRecord) -> FabricationRecord:
        record = FabricationRecord.model_validate(record.model_dump(mode="python"), strict=False)
        project_root = self._root / _key(record.readiness.project_id)
        project_root.mkdir(parents=True, exist_ok=True)
        target = (project_root / f"record-{_key(record.readiness.result_id)}.json").resolve(strict=False)
        if not target.is_relative_to(self._root) or target.parent != project_root.resolve(strict=False):
            raise ValueError("fabrication store path escaped")
        payload = record.model_dump_json(indent=None)
        if target.exists():
            if target.is_symlink() or target.read_text(encoding="utf-8") != payload:
                raise ValueError("fabrication record is immutable")
            return FabricationRecord.model_validate_json(payload)
        fd, temporary_name = tempfile.mkstemp(prefix=".fabrication-", suffix=".tmp", dir=project_root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                raise ValueError("fabrication record appeared during save")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return record

    def get(self, *, project_id: str, result_id: str) -> FabricationRecord | None:
        target = self._root / _key(project_id) / f"record-{_key(result_id)}.json"
        if not target.is_file() or target.is_symlink():
            return None
        record = FabricationRecord.model_validate_json(target.read_text(encoding="utf-8"))
        if record.readiness.project_id != project_id or record.readiness.result_id != result_id:
            raise ValueError("fabrication record identity mismatch")
        return record

    def list(self, *, project_id: str) -> tuple[FabricationRecord, ...]:
        project_root = self._root / _key(project_id)
        if not project_root.is_dir():
            return ()
        records: list[FabricationRecord] = []
        for path in sorted(project_root.glob("record-*.json")):
            if path.is_symlink():
                raise ValueError("fabrication record symlink rejected")
            record = FabricationRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if record.readiness.project_id != project_id:
                raise ValueError("fabrication record project mismatch")
            records.append(record)
        return tuple(records)


__all__ = ("FabricationStore",)
