"""Sanitized diagnostics export; raw engineering data is intentionally excluded."""

from __future__ import annotations

import json
import re
import uuid
import zipfile
from pathlib import Path
from typing import Mapping

from .paths import ProductionStateLayout


_SENSITIVE = re.compile(r"(?i)(api[_-]?key|token|secret|credential|password)\s*[:=]\s*\S+")


def _sanitize(value: str) -> str:
    return _SENSITIVE.sub("REDACTED", value).replace("\\", "/")


class DiagnosticsService:
    def __init__(self, layout: ProductionStateLayout) -> None:
        self._layout = layout

    def export(self, health: Mapping[str, object]) -> Path:
        self._layout.create()
        archive = self._layout.resolve_under(self._layout.diagnostics, f"diagnostics-{uuid.uuid4()}.zip")
        safe_health = json.dumps(health, sort_keys=True, ensure_ascii=False)
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("health.json", _sanitize(safe_health))
            for log in sorted(self._layout.logs.glob("*.log")):
                # Diagnostics are bounded and have no source/artifact inclusion.
                text = log.read_text(encoding="utf-8", errors="replace")[-65_536:]
                output.writestr(f"logs/{log.name}", _sanitize(text))
        return archive


__all__ = ("DiagnosticsService",)
