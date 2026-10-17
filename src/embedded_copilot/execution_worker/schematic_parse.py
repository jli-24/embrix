from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_copilot import SchematicParseValidationEvidence

from .adapters import run_kicad_cli


class KiCadCliSchematicParseAdapter:
    """Validate a generated schematic through a read-only KiCad CLI export."""

    _COMMAND_IDENTITY = "kicad-cli sch export netlist"

    def __init__(
        self,
        *,
        executable: str,
        kicad_version: str,
        timeout_seconds: float = 120.0,
        max_output_bytes: int = 256 * 1024,
        runner: Callable[..., object] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(executable, str) or not executable:
            raise ValueError("KiCad executable is invalid")
        executable_name = Path(executable).name.casefold()
        if executable_name not in {"kicad-cli", "kicad-cli.exe"}:
            raise ValueError("KiCad executable is not trusted")
        executable_path = Path(executable)
        if runner is None and (
            not executable_path.is_absolute()
            or executable_path.is_symlink()
            or not executable_path.is_file()
        ):
            raise ValueError("KiCad executable path is not trusted")
        if not isinstance(kicad_version, str) or not kicad_version.strip():
            raise ValueError("KiCad version is invalid")
        if type(timeout_seconds) not in {int, float} or not 1 <= timeout_seconds <= 600:
            raise ValueError("KiCad timeout is invalid")
        if (
            type(max_output_bytes) is not int
            or not 1024 <= max_output_bytes <= 4 * 1024 * 1024
        ):
            raise ValueError("KiCad output limit is invalid")
        self._executable = (
            str(executable_path.resolve())
            if executable_path.is_absolute()
            else executable
        )
        self._kicad_version = kicad_version.strip()
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._runner = runner or run_kicad_cli
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        project_id: str,
        conversation_id: str,
        operation_id: str,
        artifact: Path,
        artifact_path: str = "hardware/main.kicad_sch",
        expected_artifact_fingerprint: str | None = None,
    ) -> SchematicParseValidationEvidence:
        if (
            not isinstance(artifact, Path)
            or artifact.is_symlink()
            or not artifact.is_file()
        ):
            raise ValueError("schematic artifact is unavailable")
        if artifact.stat().st_size <= 0:
            raise ValueError("schematic artifact is empty")
        before_bytes = artifact.read_bytes()
        before_fingerprint = "sha256:" + hashlib.sha256(before_bytes).hexdigest()
        if (
            expected_artifact_fingerprint is not None
            and before_fingerprint != expected_artifact_fingerprint
        ):
            raise ValueError("schematic artifact fingerprint mismatch")

        completed = None
        output_bytes = b""
        error_bytes = b""
        exit_code = -1
        output_exists = False
        with tempfile.TemporaryDirectory(prefix="embrix-kicad-parse-") as temporary:
            output_path = Path(temporary) / "schematic.net"
            argv = (
                self._executable,
                "sch",
                "export",
                "netlist",
                "--output",
                str(output_path),
                str(artifact.resolve(strict=True)),
            )
            try:
                completed = self._runner(
                    argv,
                    cwd=artifact.parent.resolve(strict=True),
                    timeout_seconds=self._timeout_seconds,
                )
                exit_code = int(getattr(completed, "returncode", -1))
                output_bytes = bytes(getattr(completed, "stdout", b"") or b"")[
                    : self._max_output_bytes
                ]
                error_bytes = bytes(getattr(completed, "stderr", b"") or b"")[
                    : self._max_output_bytes
                ]
                output_exists = output_path.is_file() and output_path.stat().st_size > 0
            except (OSError, PermissionError, TimeoutError) as error:
                error_bytes = str(error).encode("utf-8", errors="replace")[
                    : self._max_output_bytes
                ]

        after_bytes = artifact.read_bytes()
        after_fingerprint = "sha256:" + hashlib.sha256(after_bytes).hexdigest()
        unchanged = (
            before_bytes == after_bytes and before_fingerprint == after_fingerprint
        )
        status = (
            "PARSE_VERIFIED"
            if exit_code == 0 and output_exists and unchanged
            else "FAILED"
        )
        validated_at = self._clock()
        if validated_at.tzinfo is None:
            raise ValueError("parse validation clock must be timezone-aware")
        validation_id = (
            "schematic-parse-validation-"
            + hashlib.sha256(
                f"{project_id}|{conversation_id}|{operation_id}|{before_fingerprint}|{validated_at.isoformat()}".encode()
            ).hexdigest()[:32]
        )
        return SchematicParseValidationEvidence.create(
            validation_id=validation_id,
            project_id=project_id,
            conversation_id=conversation_id,
            operation_id=operation_id,
            artifact_path=artifact_path,
            artifact_fingerprint=before_fingerprint,
            kicad_cli_path=self._executable,
            kicad_version=self._kicad_version,
            command_identity=self._COMMAND_IDENTITY,
            exit_code=exit_code,
            validation_status=status,
            artifact_unchanged=unchanged,
            stdout_summary=_safe_summary(output_bytes),
            stderr_summary=_safe_summary(error_bytes),
            validated_at=validated_at,
        )


def _safe_summary(value: bytes) -> str:
    lines: list[str] = []
    for line in value.decode("utf-8", errors="replace").splitlines()[:40]:
        line = line.strip()
        if not line:
            continue
        if re.search(
            r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|"
            r"secret)\s*[:=]",
            line,
            re.IGNORECASE,
        ):
            line = "[redacted diagnostic]"
        if re.search(r"(?:[A-Za-z]:[\\/]|\\\\|file://)", line):
            line = re.sub(
                r"[A-Za-z]:[\\/][^\s]*|\\\\[^\s]*|file://[^\s]*",
                "[redacted path]",
                line,
            )
        lines.append(line[:240])
    return " | ".join(lines)[:2048]


__all__ = ("KiCadCliSchematicParseAdapter",)
