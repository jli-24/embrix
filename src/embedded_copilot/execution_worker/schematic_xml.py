"""Canonical read-only XML adapter. No discovery or default executable exists."""
from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from embedded_copilot.engineering_copilot.schematic_evidence import parse_xml_netlist

from .adapters import run_kicad_cli
from .contracts import (
    ArtifactIdentity,
    SchematicParseOperation,
    SchematicReadBackOperation,
    SchematicXmlResult,
    fingerprint,
)
from .ports import AdapterResult


class SchematicXmlExportPort(Protocol):
    def export_xml(self, operation: SchematicParseOperation, input_bytes: bytes) -> tuple[int, bytes]: ...


class KiCadCliSchematicXmlExporter:
    """Export one approved schematic from an isolated copy with a fixed CLI."""

    _MAX_INPUT_BYTES = 16 * 1024 * 1024
    _MAX_XML_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        *,
        executable: str | Path,
        tool_version: str,
        timeout_seconds: float = 120.0,
        runner: Callable[..., object] | None = None,
    ) -> None:
        path = Path(executable) if isinstance(executable, (str, Path)) else None
        if (
            path is None
            or not path.is_absolute()
            or path.name.casefold() not in {"kicad-cli", "kicad-cli.exe"}
            or path.is_symlink()
            or not path.is_file()
        ):
            raise ValueError("trusted KiCad executable is required")
        if (
            not isinstance(tool_version, str)
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?", tool_version) is None
        ):
            raise ValueError("fixed KiCad version is required")
        if type(timeout_seconds) not in {int, float} or not 1 <= timeout_seconds <= 600:
            raise ValueError("KiCad timeout is invalid")
        if runner is not None and not callable(runner):
            raise TypeError("KiCad runner is invalid")
        self._executable = path.resolve(strict=True)
        self._executable_sha256 = self._file_sha256(self._executable)
        self._tool_version = tool_version
        self._timeout_seconds = float(timeout_seconds)
        self._runner = runner or run_kicad_cli

    @property
    def executable_sha256(self) -> str:
        return self._executable_sha256

    @property
    def tool_version(self) -> str:
        return self._tool_version

    def export_xml(self, operation: SchematicParseOperation, input_bytes: bytes) -> tuple[int, bytes]:
        if not isinstance(input_bytes, bytes) or not input_bytes or len(input_bytes) > self._MAX_INPUT_BYTES:
            raise ValueError("schematic input invalid")
        input_hash = "sha256:" + hashlib.sha256(input_bytes).hexdigest()
        if input_hash != operation.artifact_fingerprint:
            raise ValueError("schematic input artifact mismatch")
        self._check_executable()
        with tempfile.TemporaryDirectory(prefix="embrix-kicad-xml-") as temporary:
            root = Path(temporary)
            input_path = root / "hardware" / "main.kicad_sch"
            input_path.parent.mkdir()
            input_path.write_bytes(input_bytes)
            config = root / "config"
            cache = root / "cache"
            config.mkdir()
            cache.mkdir()
            profile = root / "profile"
            appdata = profile / "AppData" / "Roaming"
            localappdata = profile / "AppData" / "Local"
            temporary_files = root / "temp"
            appdata.mkdir(parents=True)
            localappdata.mkdir()
            temporary_files.mkdir()
            output_path = root / "netlist.xml"
            argv = (
                str(self._executable), "sch", "export", "netlist",
                "--format", "kicadxml", "--output", str(output_path),
                "hardware/main.kicad_sch",
            )
            completed = self._runner(
                argv,
                cwd=root,
                timeout_seconds=self._timeout_seconds,
                environment={
                    "KICAD_CONFIG_HOME": str(config),
                    "KICAD_CACHE_HOME": str(cache),
                    "APPDATA": str(appdata),
                    "LOCALAPPDATA": str(localappdata),
                    "TEMP": str(temporary_files),
                    "TMP": str(temporary_files),
                    "USERPROFILE": str(profile),
                    "HOMEDRIVE": profile.drive or "/",
                    "HOMEPATH": str(profile)[len(profile.drive):],
                },
            )
            self._check_executable()
            if input_path.is_symlink() or input_path.read_bytes() != input_bytes:
                raise ValueError("schematic input changed during XML export")
            self._check_workspace(root)
            if output_path.is_symlink() or not output_path.is_file():
                raise ValueError("KiCad XML output missing")
            with output_path.open("rb") as source:
                xml = source.read(self._MAX_XML_BYTES + 1)
            if not xml or len(xml) > self._MAX_XML_BYTES:
                raise ValueError("KiCad XML output invalid")
            exit_code = getattr(completed, "returncode", None)
            if type(exit_code) is not int:
                raise ValueError("KiCad XML exit status invalid")
            return exit_code, xml

    def _check_executable(self) -> None:
        if self._executable.is_symlink() or not self._executable.is_file() or self._file_sha256(self._executable) != self._executable_sha256:
            raise ValueError("KiCad executable drift")

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def _check_workspace(root: Path) -> None:
        for entry in root.rglob("*"):
            if entry.is_symlink():
                raise ValueError("KiCad XML workspace side effect")
            name = entry.relative_to(root).as_posix()
            if name in {"hardware", "hardware/main.kicad_sch", "netlist.xml", "config", "cache"}:
                continue
            if name in {"profile", "temp"} or name.startswith(("config/", "cache/", "profile/", "temp/")):
                continue
            raise ValueError("KiCad XML workspace side effect")


class SchematicXmlAdapter:
    def __init__(self, *, read_input: Callable[[SchematicParseOperation], bytes], exporter: SchematicXmlExportPort,
                 tool_executable_sha256: str, tool_version: str, clock: Callable[[], datetime] | None = None,
                 library_observer: Callable[..., tuple[ArtifactIdentity, bytes]] | None = None) -> None:
        self._reader = read_input
        self._library_observer = library_observer
        self._exporter = exporter
        self._tool_hash = fingerprint(tool_executable_sha256)
        self._version = tool_version
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, operation: SchematicParseOperation) -> AdapterResult:
        operation = type(operation).model_validate(operation.model_dump(mode="python"))
        before = self._reader(operation)
        before_hash = "sha256:" + hashlib.sha256(before).hexdigest()
        if before_hash != operation.artifact_fingerprint:
            raise ValueError("schematic input artifact mismatch")
        exit_code, xml = self._exporter.export_xml(operation, before)
        if type(exit_code) is not int or not isinstance(xml, bytes) or not xml or len(xml) > 4 * 1024 * 1024:
            raise ValueError("XML export result invalid")
        after = self._reader(operation)
        after_hash = "sha256:" + hashlib.sha256(after).hexdigest()
        coverage = ()
        loss = ("XML_COVERAGE_UNKNOWN",)
        try:
            full = isinstance(operation, SchematicReadBackOperation)
            parse_xml_netlist(xml, require_properties=full)
            coverage = ("references", "part_properties", "physical_pin_numbers", "nets") if full else ("references", "physical_pin_numbers", "nets")
            loss = ()
        except ValueError:
            pass
        state = "PASS" if exit_code == 0 and before == after and coverage else "FAIL" if exit_code != 0 or before != after else "UNKNOWN"
        created = self._clock()
        xml_hash = "sha256:" + hashlib.sha256(xml).hexdigest()
        identity = ArtifactIdentity(
            artifact_id="schematic-xml-" + hashlib.sha256((operation.operation_id + xml_hash).encode()).hexdigest()[:32],
            tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id,
            workspace_fingerprint=operation.workspace_fingerprint, build_operation_id=operation.operation_id,
            toolchain_id="KICAD_XML_NETLIST", toolchain_version=self._version,
            build_configuration="READ_ONLY_XML", artifact_type="NETLIST", artifact_sha256=xml_hash,
            size_bytes=len(xml), created_at=created,
        )
        evidence = SchematicXmlResult(
            operation_id=operation.operation_id, source_write_operation_id=operation.source_write_operation_id,
            source_write_receipt_fingerprint=operation.source_write_receipt_fingerprint,
            input_artifact_path=operation.artifact_path, fingerprint_before=before_hash, fingerprint_after=after_hash,
            tool_executable_sha256=self._tool_hash, tool_version=self._version,
            command_identity="kicad-cli sch export netlist --format kicadxml", netlist_artifact=identity,
            coverage=coverage, loss_or_unknown=loss, exit_code=exit_code, parse_status=state, created_at=created,
        )
        additional = ()
        evidence_refs = ()
        if isinstance(operation, SchematicReadBackOperation) and operation.library_map_evidence_id is not None:
            if state != "PASS" or self._library_observer is None:
                raise ValueError("library observation unavailable")
            library_identity, library_bytes = self._library_observer(
                operation, xml, created_at=created, tool_version=self._version)
            additional = ((library_identity, library_bytes),)
            evidence_refs = (library_identity.artifact_id,)
        return AdapterResult(status="SUCCEEDED" if state == "PASS" else "FAILED",
                             summary="Schematic XML " + state, artifact=identity, artifact_content=xml,
                             schematic_xml_result=evidence, additional_artifacts=additional,
                             evidence_references=evidence_refs)
