from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from embedded_copilot.execution_worker.adapters import (
    SafeProcessRunner,
    _minimal_environment,
)
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    KiCadManufacturingOperation,
    OperationType,
)
from embedded_copilot.execution_worker.ports import AdapterResult, ExecutionContext

_MAX_OUTPUT_BYTES = 512 * 1024 * 1024


def _under(root: Path, relative: str) -> Path:
    raw = root / relative
    for ancestor in (raw, *raw.parents):
        try:
            if ancestor.is_symlink():
                raise ValueError("manufacturing path contains a symlink")
        except OSError as exc:
            raise ValueError("manufacturing path cannot be inspected") from exc
    candidate = raw.resolve(strict=False)
    if not candidate.is_relative_to(root.resolve(strict=False)):
        raise ValueError("manufacturing path escaped workspace")
    return candidate


def _safe_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


class KiCadManufacturingAdapter:
    """Run the allow-listed KiCad manufacturing commands through the worker.

    This adapter deliberately does not implement a Gerber, Excellon or CPL
    writer. KiCad is the production source of those formats; this class only
    binds its fixed command shapes to the typed execution contract and returns
    immutable artifact bytes for later validation.
    """

    def __init__(self, *, runner: object | None = None) -> None:
        self._runner = runner or SafeProcessRunner()

    def execute(self, operation: KiCadManufacturingOperation, context: ExecutionContext) -> AdapterResult:
        if context.tool is None or context.tool.status != "AVAILABLE" or not context.tool.version_verified:
            return AdapterResult("UNAVAILABLE", "trusted KiCad CLI unavailable")
        if context.tool.definition.tool_version != operation.tool_version:
            return AdapterResult("UNAVAILABLE", "KiCad tool version is not bound")
        if operation.operation_type is OperationType.MANUFACTURING_PACKAGE_BUILD:
            try:
                self._prepare_owned_workspace(
                    _under(context.workspace_root.resolve(strict=False), operation.output_workspace_relative),
                    operation,
                )
            except (OSError, ValueError):
                return AdapterResult("FAILED", "manufacturing package workspace is not owned")
            return AdapterResult("SUCCEEDED", "manufacturing package workspace reserved")
        if context.tool.executable is None:
            return AdapterResult("UNAVAILABLE", "trusted KiCad CLI unavailable")
        executable = context.tool.executable.resolve(strict=False)
        if not executable.is_file() or executable.name.casefold() not in {"kicad-cli.exe", "kicad-cli"}:
            return AdapterResult("FAILED", "KiCad executable is not trusted")

        workspace = context.workspace_root.resolve(strict=False)
        source = _under(workspace, operation.source_pcb_relative_path)
        if source.is_symlink() or not source.is_file():
            return AdapterResult("FAILED", "PCB source is unavailable")
        if "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest() != operation.pcb_sha256:
            return AdapterResult("FAILED", "PCB source fingerprint mismatch")
        output = _under(workspace, operation.output_workspace_relative)
        try:
            self._prepare_owned_workspace(output, operation)
            if operation.operation_type is OperationType.KICAD_GERBER_EXPORT:
                argv = (
                    str(executable), "pcb", "export", "gerbers",
                    "--output", operation.output_workspace_relative,
                    "--layers", ",".join(operation.layer_list),
                    operation.source_pcb_relative_path,
                )
            elif operation.operation_type is OperationType.KICAD_DRILL_EXPORT:
                argv = (
                    str(executable), "pcb", "export", "drill",
                    "--output", operation.output_workspace_relative,
                    "--format", "excellon", "--drill-origin", "absolute",
                    "--excellon-units", "mm", "--excellon-separate-th",
                    operation.source_pcb_relative_path,
                )
            elif operation.operation_type is OperationType.KICAD_POSITION_EXPORT:
                argv = (
                    str(executable), "pcb", "export", "pos",
                    "--output", f"{operation.output_workspace_relative}/placement.csv",
                    "--format", "csv", "--units", "mm", "--side", "both",
                    "--exclude-dnp", operation.source_pcb_relative_path,
                )
            elif operation.operation_type is OperationType.MANUFACTURING_PACKAGE_BUILD:
                return AdapterResult("SUCCEEDED", "manufacturing package workspace reserved")
            else:
                return AdapterResult("FAILED", "manufacturing operation is unsupported")
            code, _stdout, _stderr = self._run(argv, cwd=workspace, timeout_seconds=context.tool.definition.timeout_seconds, cancellation=context.cancellation)
        except InterruptedError:
            return AdapterResult("CANCELLED", "manufacturing export cancelled")
        except TimeoutError:
            return AdapterResult("TIMED_OUT", "manufacturing export timed out")
        except (OSError, PermissionError, ValueError):
            return AdapterResult("FAILED", "manufacturing export failed closed")
        if code != 0:
            return AdapterResult("FAILED", "KiCad manufacturing export failed")

        try:
            artifacts = self._collect_artifacts(operation, output)
        except (OSError, ValueError):
            return AdapterResult("FAILED", "manufacturing output read-back failed")
        if not artifacts:
            return AdapterResult("FAILED", "manufacturing output is empty")
        return AdapterResult(
            "SUCCEEDED",
            "KiCad manufacturing export completed",
            evidence_references=tuple(item.artifact_id for item, _ in artifacts),
            additional_artifacts=artifacts,
        )

    def _run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int, cancellation: object) -> tuple[int, bytes, bytes]:
        runner = self._runner
        run = getattr(runner, "run", None)
        if callable(run):
            return run(
                argv,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                environment=_minimal_environment(),
                cancellation=cancellation,
            )
        if callable(runner):
            completed = runner(argv, cwd=cwd, timeout_seconds=timeout_seconds)
            return int(getattr(completed, "returncode", 1)), bytes(getattr(completed, "stdout", b"") or b""), bytes(getattr(completed, "stderr", b"") or b"")
        raise ValueError("manufacturing runner is invalid")

    @staticmethod
    def _prepare_owned_workspace(output: Path, operation: KiCadManufacturingOperation) -> None:
        output_root = output.parent.resolve(strict=False)
        if not output_root.is_dir():
            output_root.mkdir(parents=True, exist_ok=True)
        if output.exists() and output.is_symlink():
            raise ValueError("manufacturing output workspace cannot be a symlink")
        marker = output / ".embrix-manufacturing-owner"
        owner = f"{operation.project_id}\n{operation.project_revision}\n{operation.manufacturing_specification_fingerprint}\n"
        if output.exists():
            if not output.is_dir() or marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8") != owner:
                raise ValueError("manufacturing output workspace is not owned")
            for child in output.iterdir():
                if child.name == marker.name:
                    continue
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
        else:
            output.mkdir(parents=True)
        marker.write_text(owner, encoding="utf-8", newline="\n")

    @staticmethod
    def _collect_artifacts(operation: KiCadManufacturingOperation, output: Path) -> tuple[tuple[ArtifactIdentity, bytes], ...]:
        if not output.is_dir() or output.is_symlink():
            raise ValueError("manufacturing output workspace is invalid")
        contents: list[tuple[str, bytes]] = []
        total_bytes = 0
        for path in sorted(output.rglob("*")):
            if path.name == ".embrix-manufacturing-owner":
                continue
            if operation.operation_type is OperationType.KICAD_GERBER_EXPORT and path.suffix.casefold() == ".gbrjob":
                # KiCad's job JSON describes the export but is not a Gerber
                # layer and must not be parsed as one.
                continue
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(output).as_posix()
            data = path.read_bytes()
            if not data or len(data) > _MAX_OUTPUT_BYTES:
                raise ValueError("manufacturing output has invalid size")
            total_bytes += len(data)
            if total_bytes > _MAX_OUTPUT_BYTES:
                raise ValueError("manufacturing output exceeds size limit")
            contents.append((relative, data))
        artifact_type = {
            OperationType.KICAD_GERBER_EXPORT: "GERBER",
            OperationType.KICAD_DRILL_EXPORT: "DRILL",
            OperationType.KICAD_POSITION_EXPORT: "CPL",
        }.get(operation.operation_type)
        if artifact_type is None:
            return ()
        result: list[tuple[ArtifactIdentity, bytes]] = []
        for relative, data in contents:
            suffix = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
            artifact = ArtifactIdentity(
                artifact_id=f"manufacturing-{_safe_component(operation.operation_id)}-{suffix}",
                tenant_id=operation.tenant_id,
                project_id=operation.project_id,
                target_id=operation.target_id,
                workspace_fingerprint=operation.workspace_fingerprint,
                build_operation_id=operation.operation_id,
                toolchain_id=operation.tool_id,
                toolchain_version=operation.tool_version,
                build_configuration=operation.project_revision,
                artifact_type=artifact_type,
                artifact_sha256="sha256:" + hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                created_at=operation.created_at,
            )
            result.append((artifact, data))
        return tuple(result)


__all__ = ("KiCadManufacturingAdapter",)
