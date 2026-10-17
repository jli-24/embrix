from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService
from embedded_copilot.pcb_production import (
    DrcIssue,
    PCBArtifactRecord,
    PCBDrcIssueClassification,
    PCBDrcIssueSeverity,
    PCBDrcResult,
    PCBDrcStatus,
    PCBDrcViolation,
    PCBEngineeringStatus,
    PCBParseValidationEvidence,
    PCBParseValidationStatus,
)

from .adapters import run_kicad_cli
from .contracts import OperationType, PcbDrcOperation, PCBWriteOperation
from .kicad_toolchain import discover_kicad_footprint_root
from .ports import AdapterResult


class PcbDrcCommandBuilder(Protocol):
    def __call__(self, executable: str, board_path: Path, report_path: Path) -> tuple[str, ...]: ...


class PcbParseCommandBuilder(Protocol):
    def __call__(self, executable: str, board_path: Path, output_directory: Path) -> tuple[str, ...]: ...


def discover_kicad_pcb_drc_command(executable: str, *, cwd: Path, timeout_seconds: float = 15.0) -> PcbDrcCommandBuilder | None:
    """Select a DRC argv shape only from the installed KiCad help output."""
    try:
        completed = run_kicad_cli((executable, "pcb", "drc", "--help"), cwd=cwd, timeout_seconds=timeout_seconds)
    except (OSError, PermissionError, TimeoutError):
        return None
    output = (bytes(getattr(completed, "stdout", b"") or b"") + b"\n" + bytes(getattr(completed, "stderr", b"") or b""))[:64 * 1024].decode("utf-8", errors="ignore")
    if (
        "--output" not in output
        or "--format" not in output
        or "json" not in output.casefold()
        or "--severity-error" not in output
        or "--severity-warning" not in output
        or "--exit-code-violations" not in output
        or "drc" not in output.casefold()
    ):
        return None
    supports_zone_refill = "--refill-zones" in output

    def _builder(trusted_executable: str, board_path: Path, report_path: Path) -> tuple[str, ...]:
        command = (
            trusted_executable, "pcb", "drc", "--output", str(report_path),
            "--format", "json", "--severity-error", "--severity-warning", "--exit-code-violations",
        )
        if supports_zone_refill:
            command += ("--refill-zones",)
        return command + (str(board_path),)

    return _builder


def discover_kicad_pcb_parse_command(executable: str, *, cwd: Path, timeout_seconds: float = 15.0) -> PcbParseCommandBuilder | None:
    """Select a read-only PCB export command that forces KiCad to parse the board."""
    try:
        completed = run_kicad_cli((executable, "pcb", "export", "gerbers", "--help"), cwd=cwd, timeout_seconds=timeout_seconds)
    except (OSError, PermissionError, TimeoutError):
        return None
    output = (bytes(getattr(completed, "stdout", b"") or b"") + b"\n" + bytes(getattr(completed, "stderr", b"") or b""))[:64 * 1024].decode("utf-8", errors="ignore")
    if "gerber" not in output.casefold() or "--output" not in output and "-o" not in output:
        return None

    def _builder(trusted_executable: str, board_path: Path, output_directory: Path) -> tuple[str, ...]:
        return (
            trusted_executable, "pcb", "export", "gerbers", "--output", str(output_directory), str(board_path),
        )

    return _builder


class PCBWriteAdapter:
    """Canonical PCB mutation adapter; all workspace writes remain in WorkspaceApplyService."""

    def __init__(self, workspace: WorkspaceApplyService) -> None:
        if not isinstance(workspace, WorkspaceApplyService):
            raise TypeError("canonical workspace apply service is required")
        self._workspace = workspace

    def execute(self, operation: PCBWriteOperation, context=None) -> AdapterResult:
        checked = PCBWriteOperation.model_validate(operation.model_dump(mode="python"), strict=False)
        receipt = self._workspace.apply(
            changeset=checked.changeset,
            approval=checked.workspace_approval,
            operation_id=checked.operation_id,
        )
        applied = receipt.status.value in {"APPLIED", "RECOVERED"}
        artifact = None
        if applied:
            artifact = PCBArtifactRecord.create(
                artifact_id=f"pcb-artifact-{checked.operation_id}",
                project_id=checked.project_id,
                path=checked.target_relative_path,
                content_hash=checked.content_fingerprint,
                source_operation_id=checked.operation_id,
                proposal_fingerprint=checked.pcb_proposal_fingerprint,
                schematic_source_ref=checked.schematic_artifact_id,
            )
        return AdapterResult(
            status="SUCCEEDED" if applied else "FAILED",
            summary="KiCad PCB workspace write applied." if applied else "KiCad PCB workspace write failed closed.",
            evidence_references=() if artifact is None else (artifact.artifact_id,),
            workspace_apply_receipt=receipt,
            pcb_artifact=artifact,
        )


class KiCadCliPCBParseAdapter:
    """Validate a PCB by making KiCad parse it in a read-only temporary export."""

    def __init__(
        self,
        *,
        executable: str,
        workspace_resolver: Callable[[PcbDrcOperation], Path],
        command_builder: PcbParseCommandBuilder | None,
        kicad_version: str,
        timeout_seconds: int = 120,
        runner: Callable[..., object] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not executable or not Path(executable).is_file():
            raise ValueError("trusted KiCad executable is unavailable")
        executable_path = Path(executable)
        if executable_path.name.casefold() not in {"kicad-cli", "kicad-cli.exe"} or executable_path.is_symlink():
            raise ValueError("trusted KiCad executable is invalid")
        if not callable(workspace_resolver) or not kicad_version:
            raise TypeError("KiCad PCB parse binding is incomplete")
        self._executable = str(executable_path.resolve())
        self._workspace_resolver = workspace_resolver
        self._command_builder = command_builder
        self._kicad_version = kicad_version
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, operation: PcbDrcOperation | PCBWriteOperation, context=None) -> PCBParseValidationEvidence:
        operation_values = operation.model_dump(mode="python")
        if operation_values.get("operation_type") == OperationType.KICAD_PCB_WRITE:
            checked_write = PCBWriteOperation.model_validate(operation_values, strict=False)
            project_id = checked_write.project_id
            conversation_id = checked_write.conversation_id
            source_write_operation_id = checked_write.operation_id
            pcb_content_fingerprint = checked_write.content_fingerprint
            target_relative_path = checked_write.target_relative_path
            checked = checked_write
        else:
            checked_drc = PcbDrcOperation.model_validate(operation_values, strict=False)
            project_id = checked_drc.project_id
            conversation_id = checked_drc.conversation_id
            source_write_operation_id = checked_drc.source_write_operation_id
            pcb_content_fingerprint = checked_drc.pcb_content_fingerprint
            target_relative_path = checked_drc.target_relative_path
            checked = checked_drc
        root = Path(self._workspace_resolver(checked)).resolve()
        board = (root / target_relative_path).resolve()
        before = None
        exit_code = -1
        stdout = b""
        stderr = b""
        export_exists = False
        if board.is_relative_to(root) and board.is_file() and not board.is_symlink():
            content = board.read_bytes()
            before = "sha256:" + hashlib.sha256(content).hexdigest()
            if before != pcb_content_fingerprint:
                stderr = b"PCB content fingerprint drifted."
            elif self._command_builder is not None:
                try:
                    with tempfile.TemporaryDirectory(prefix="embrix-kicad-pcb-parse-") as temporary:
                        output_directory = Path(temporary) / "gerbers"
                        argv = tuple(self._command_builder(self._executable, board, output_directory))
                        if not argv or argv[0] != self._executable or any(not isinstance(item, str) or not item for item in argv):
                            raise ValueError("trusted KiCad PCB parse invocation rejected")
                        if self._runner is None:
                            completed = run_kicad_cli(argv, cwd=root, timeout_seconds=self._timeout_seconds)
                        else:
                            completed = self._runner(argv, root, self._timeout_seconds)
                        exit_code = int(getattr(completed, "returncode", -1))
                        stdout = bytes(getattr(completed, "stdout", b"") or b"")[:4096]
                        stderr = bytes(getattr(completed, "stderr", b"") or b"")[:4096]
                        export_exists = output_directory.is_dir() and any(path.is_file() for path in output_directory.rglob("*"))
                except (OSError, PermissionError, TimeoutError, ValueError) as error:
                    stderr = str(error).encode("utf-8", errors="replace")[:4096]
        after = None
        if board.is_relative_to(root) and board.is_file() and not board.is_symlink():
            after = "sha256:" + hashlib.sha256(board.read_bytes()).hexdigest()
        unchanged = before is not None and before == after
        status = PCBParseValidationStatus.PARSE_VERIFIED if exit_code == 0 and export_exists and unchanged else PCBParseValidationStatus.FAILED
        validated_at = self._clock()
        return PCBParseValidationEvidence.create(
            validation_id="pcb-parse-validation-" + hashlib.sha256(f"{project_id}|{conversation_id}|{source_write_operation_id}|{pcb_content_fingerprint}|{validated_at.isoformat()}".encode()).hexdigest()[:32],
            project_id=project_id,
            conversation_id=conversation_id,
            operation_id=source_write_operation_id,
            artifact_path=target_relative_path,
            artifact_fingerprint=pcb_content_fingerprint,
            kicad_cli_path=self._executable,
            kicad_version=self._kicad_version,
            command_identity="kicad-cli pcb export gerbers (parse validation)",
            exit_code=exit_code,
            validation_status=status,
            artifact_unchanged=unchanged,
            stdout_summary=_safe_summary(stdout),
            stderr_summary=_safe_summary(stderr),
            validated_at=validated_at,
        )


class KiCadCliPCBDrCAdapter:
    """Trusted KiCad DRC adapter. The command family is injected from inspected KiCad help."""

    def __init__(
        self,
        *,
        executable: str,
        workspace_resolver: Callable[[PcbDrcOperation], Path],
        command_builder: PcbDrcCommandBuilder | None,
        kicad_version: str,
        timeout_seconds: int = 120,
        runner: Callable[..., object] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not executable or not Path(executable).is_file():
            raise ValueError("trusted KiCad executable is unavailable")
        executable_path = Path(executable)
        if executable_path.name.casefold() not in {"kicad-cli", "kicad-cli.exe"} or executable_path.is_symlink():
            raise ValueError("trusted KiCad executable is invalid")
        if not callable(workspace_resolver) or not kicad_version:
            raise TypeError("KiCad DRC binding is incomplete")
        self._executable = str(executable_path.resolve())
        self._workspace_resolver = workspace_resolver
        self._command_builder = command_builder
        self._kicad_version = kicad_version
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, operation: PcbDrcOperation, context=None) -> AdapterResult:
        checked = PcbDrcOperation.model_validate(operation.model_dump(mode="python"), strict=False)
        started = self._clock()
        root = Path(self._workspace_resolver(checked)).resolve()
        board = (root / checked.target_relative_path).resolve()
        if not board.is_relative_to(root) or not board.is_file():
            return self._result(checked, started, PCBDrcStatus.DRC_BLOCKED, -2, (), ("PCB target is unavailable.",), None, False, None, None)
        content = board.read_bytes()
        content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        if content_hash != checked.pcb_content_fingerprint:
            return self._result(checked, started, PCBDrcStatus.DRC_BLOCKED, -2, (), ("PCB content fingerprint drifted.",), None, False, None, None)
        if self._command_builder is None:
            return self._result(checked, started, PCBDrcStatus.DRC_UNAVAILABLE, -1, (), ("KiCad DRC command family is not configured.",), None, False, None, None)
        report = (root / ".embedded-copilot-state" / "pcb-drc" / f"{checked.operation_id}.json").resolve()
        if not report.is_relative_to(root):
            return self._result(checked, started, PCBDrcStatus.DRC_BLOCKED, -2, (), ("DRC report path escaped workspace.",), None, False, None, None)
        report.parent.mkdir(parents=True, exist_ok=True)
        try:
            argv = tuple(self._command_builder(self._executable, board, report))
            if not argv or argv[0] != self._executable or any(not isinstance(item, str) or not item for item in argv):
                raise ValueError("trusted KiCad DRC invocation rejected")
            if self._runner is None:
                completed = run_kicad_cli(argv, cwd=root, timeout_seconds=self._timeout_seconds)
            else:
                completed = self._runner(argv, root, self._timeout_seconds)
            exit_status = int(getattr(completed, "returncode", -1))
        except TimeoutError:
            return self._result(checked, started, PCBDrcStatus.DRC_BLOCKED, -2, (), ("KiCad DRC timed out.",), None, True, None, None)
        except (OSError, PermissionError, ValueError):
            return self._result(checked, started, PCBDrcStatus.DRC_BLOCKED, -2, (), ("KiCad DRC invocation failed closed.",), None, True, None, None)
        parsed = self._parse_report(report)
        if parsed is None:
            return self._result(checked, started, PCBDrcStatus.DRC_BLOCKED, exit_status, (), ("KiCad DRC report is unavailable or invalid.",), None, True, None, None)
        violations, warnings = parsed
        report_ref = f"artifact://pcb-drc/report/{checked.operation_id}"
        report_fingerprint = "sha256:" + hashlib.sha256(report.read_bytes()).hexdigest()
        issues = self._issues(violations, checked, report_ref)
        if exit_status != 0 and not issues and not warnings:
            issues = (
                DrcIssue.create(
                    issue_id="drc-issue-" + hashlib.sha256(f"{checked.operation_id}|exit-status|{exit_status}".encode()).hexdigest()[:32],
                    severity=PCBDrcIssueSeverity.ERROR,
                    rule_id="kicad-cli-exit-status",
                    message=f"KiCad DRC exited with status {exit_status} without structured violations.",
                    raw_evidence_ref=report_ref,
                    pcb_artifact_fingerprint=checked.pcb_content_fingerprint,
                    classification=PCBDrcIssueClassification.TOOLCHAIN_OR_ENVIRONMENT,
                ),
            )
        status = (
            PCBDrcStatus.DRC_FAIL
            if any(item.severity is PCBDrcIssueSeverity.ERROR for item in issues)
            else PCBDrcStatus.DRC_WARNINGS
            if issues or warnings
            else PCBDrcStatus.DRC_PASS
        )
        return self._result(checked, started, status, exit_status, violations, warnings, report_ref, True, report_fingerprint, None, issues)

    def _result(self, operation, started, status, exit_status, violations, warnings, report_ref, tool_observed, raw_report_fingerprint, parse_validation, issues=()):
        engineering_status = {
            PCBDrcStatus.DRC_PASS: PCBEngineeringStatus.PASS,
            PCBDrcStatus.DRC_WARNINGS: PCBEngineeringStatus.WARNINGS_REMAIN,
            PCBDrcStatus.DRC_FAIL: PCBEngineeringStatus.BLOCKED,
            PCBDrcStatus.DRC_BLOCKED: PCBEngineeringStatus.BLOCKED,
            PCBDrcStatus.DRC_UNAVAILABLE: PCBEngineeringStatus.UNAVAILABLE,
        }[status]
        result = PCBDrcResult.create(
            operation_id=operation.operation_id,
            pcb_artifact_ref=operation.pcb_artifact_ref,
            pcb_content_hash=operation.pcb_content_fingerprint,
            kicad_version=self._kicad_version,
            status=status,
            exit_status=exit_status,
            violations=tuple(violations),
            warnings=tuple(warnings),
            report_artifact_ref=report_ref,
            started_at=started,
            finished_at=self._clock(),
            receipt_ref=operation.source_write_receipt_fingerprint,
            tool_observed=tool_observed,
            project_id=operation.project_id,
            conversation_id=operation.conversation_id,
            kicad_cli_path=self._executable,
            command_identity="kicad-cli pcb drc --format json",
            raw_report_fingerprint=raw_report_fingerprint,
            tool_execution_status="SUCCEEDED" if tool_observed and exit_status >= 0 else "UNAVAILABLE",
            engineering_status=engineering_status,
            parse_validation=parse_validation,
            issues=tuple(issues),
        )
        adapter_status = "SUCCEEDED" if status in {PCBDrcStatus.DRC_PASS, PCBDrcStatus.DRC_WARNINGS, PCBDrcStatus.DRC_FAIL} else "UNAVAILABLE"
        return AdapterResult(status=adapter_status, summary=f"KiCad PCB DRC {status.value}.", evidence_references=() if report_ref is None else (report_ref,), pcb_drc_result=result)

    @staticmethod
    def _parse_report(path: Path) -> tuple[tuple[PCBDrcViolation, ...], tuple[str, ...]] | None:
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        raw_violations: list[dict[str, object]] = []
        categories = ("violations", "unconnected_items", "schematic_parity")
        category_present = False
        for category in categories:
            if category in payload:
                category_present = True
            values = payload.get(category, [])
            if not isinstance(values, list):
                return None
            raw_violations.extend(
                {**item, "_category": category}
                for item in values
                if isinstance(item, dict)
            )
            if any(not isinstance(item, dict) for item in values):
                return None
        if not category_present:
            if "drc_violations" not in payload:
                return None
            legacy = payload["drc_violations"]
            if not isinstance(legacy, list) or any(not isinstance(item, dict) for item in legacy):
                return None
            raw_violations = list(legacy)
        violations = []
        for index, item in enumerate(raw_violations):
            object_refs = _object_refs(item)
            violations.append(
                PCBDrcViolation.create(
                    severity=_severity(item.get("severity", "ERROR")),
                    rule=str(item.get("rule", item.get("type", item.get("_category", "UNKNOWN")))),
                    message=str(item.get("message", item.get("description", "KiCad reported a DRC violation"))),
                    object_refs=object_refs,
                    net_ref=_optional_text(item.get("net_ref", item.get("net"))),
                    layer_ref=_optional_text(item.get("layer_ref", item.get("layer"))),
                    raw_source_ref=f"artifact://pcb-drc/report/item/{index}",
                )
            )
        warnings = payload.get("warnings", [])
        if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
            return None
        ignored_checks = payload.get("ignored_checks", [])
        if not isinstance(ignored_checks, list) or any(not isinstance(item, dict) for item in ignored_checks):
            return None
        # KiCad reports configured exclusions separately from actual DRC
        # violations.  Preserve the raw report as evidence, but do not turn an
        # ignored rule into a warning: an exclusion is configuration metadata,
        # not a finding emitted by this DRC run.
        return tuple(violations), tuple(warnings)

    @staticmethod
    def _issues(violations, operation, report_ref):
        issues = []
        for index, item in enumerate(violations):
            severity = _severity(item.severity)
            rule = item.rule.strip() or "UNKNOWN"
            classification = _classify(rule, item.message, item.object_refs)
            component_ref = _object_value(item.object_refs, "component")
            footprint_ref = _object_value(item.object_refs, "footprint") or component_ref
            pad_ref = _object_value(item.object_refs, "pad")
            location = _object_location(item.object_refs)
            issues.append(
                DrcIssue.create(
                    issue_id="drc-issue-" + hashlib.sha256(f"{operation.operation_id}|{index}|{rule}|{item.message}|{item.object_refs}".encode()).hexdigest()[:32],
                    severity=severity,
                    rule_id=rule,
                    message=item.message,
                    component_ref=component_ref,
                    footprint_ref=footprint_ref,
                    pad_ref=pad_ref,
                    net_ref=item.net_ref,
                    location=location,
                    raw_evidence_ref=report_ref + f"/item/{index}",
                    pcb_artifact_fingerprint=operation.pcb_content_fingerprint,
                    classification=classification,
                )
            )
        return tuple(issues)


def _severity(value: object) -> PCBDrcIssueSeverity:
    normalized = str(value).strip().upper()
    return PCBDrcIssueSeverity(normalized if normalized in {item.value for item in PCBDrcIssueSeverity} else "ERROR")


def _optional_text(value: object) -> str | None:
    if value is None or not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _object_refs(item: dict[str, object]) -> tuple[str, ...]:
    refs = [str(value) for value in item.get("object_refs", ())] if isinstance(item.get("object_refs", ()), list) else []
    for key in ("component_ref", "footprint", "pad", "net", "layer"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            refs.append(f"{key.removesuffix('_ref')}:{value.strip()}" if key.endswith("_ref") else f"{key}:{value.strip()}")
    nested = item.get("items", ())
    if isinstance(nested, list):
        for child in nested:
            if not isinstance(child, dict):
                continue
            uuid = child.get("uuid")
            if isinstance(uuid, str) and uuid.strip():
                refs.append(f"uuid:{uuid.strip()}")
            for key in ("component", "footprint", "pad", "net", "layer"):
                value = child.get(key)
                if isinstance(value, str) and value.strip():
                    refs.append(f"{key}:{value.strip()}")
            pos = child.get("pos", child.get("position"))
            if isinstance(pos, dict) and isinstance(pos.get("x"), (int, float)) and isinstance(pos.get("y"), (int, float)):
                refs.append(f"location:{pos['x']},{pos['y']}")
    return tuple(dict.fromkeys(refs))


def _object_value(refs: tuple[str, ...], kind: str) -> str | None:
    prefix = kind + ":"
    return next((value.removeprefix(prefix) for value in refs if value.startswith(prefix) and value.removeprefix(prefix).strip()), None)


def _object_location(refs: tuple[str, ...]) -> tuple[float, float] | None:
    value = next((item.removeprefix("location:") for item in refs if item.startswith("location:")), None)
    if value is None:
        return None
    try:
        x, y = value.split(",", 1)
        return float(x), float(y)
    except (TypeError, ValueError):
        return None


def _classify(rule: str, message: str, refs: tuple[str, ...]) -> PCBDrcIssueClassification:
    evidence = f"{rule} {message} {' '.join(refs)}".casefold()
    if any(token in evidence for token in ("lib_footprint", "footprint mismatch", "pad mismatch", "pad_to")):
        return PCBDrcIssueClassification.FOOTPRINT_OR_PAD_MAPPING_DEFECT
    if any(token in evidence for token in ("malformed", "invalid board", "parse", "syntax")):
        return PCBDrcIssueClassification.PCB_ARTIFACT_RENDERING_DEFECT
    if any(token in evidence for token in ("clearance", "courtyard", "silkscreen", "board edge", "hole", "overlap", "unconnected")):
        return PCBDrcIssueClassification.PCB_PHYSICAL_DESIGN_DEFECT
    if any(token in evidence for token in ("rule", "constraint", "width", "length")):
        return PCBDrcIssueClassification.DESIGN_RULE_OR_CONSTRAINT_DEFECT
    return PCBDrcIssueClassification.UNCLASSIFIED


def _safe_summary(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace")
    text = re.sub(r"(?:[A-Za-z]:[\\/][^\s]*|\\\\[^\s]*|file://[^\s]*)", "[redacted path]", text)
    text = re.sub(r"(?i)(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret)\s*[:=]\s*[^\s|]+", "[redacted diagnostic]", text)
    return " | ".join(line.strip()[:240] for line in text.splitlines() if line.strip())[:2048]


__all__ = ("KiCadCliPCBDrCAdapter", "KiCadCliPCBParseAdapter", "PCBWriteAdapter", "PcbDrcCommandBuilder", "PcbParseCommandBuilder", "discover_kicad_footprint_root", "discover_kicad_pcb_drc_command", "discover_kicad_pcb_parse_command")
