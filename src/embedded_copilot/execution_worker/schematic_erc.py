from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence

from embedded_copilot.engineering_copilot import (
    SchematicErcEngineeringStatus,
    SchematicErcIssue,
    SchematicErcIssueClassification,
    SchematicErcIssueSeverity,
    SchematicErcResult,
    SchematicErcStatus,
    SchematicErcViolation,
)

from .ports import AdapterResult
from .contracts import SchematicErcOperation
from .adapters import run_kicad_cli


class UnavailableSchematicSymbolLibraryValidator:
    """Fail-closed validator used when no trusted project resolver exists."""

    def __call__(self, *, tenant_id: str, project_id: str, target_id: str, bindings: tuple[str, ...]) -> None:
        raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")


class SchematicSymbolLibraryValidator:
    """Validate explicit library bindings against the current project libraries.

    This adapter only reads the trusted workspace and configured KiCad symbol
    libraries.  It never infers a library from a component name and never
    creates or modifies a library.
    """

    def __init__(self, workspace_resolver: Callable[..., object]) -> None:
        if not callable(workspace_resolver):
            raise TypeError("workspace resolver is required")
        self._workspace_resolver = workspace_resolver

    def __call__(self, *, tenant_id: str, project_id: str, target_id: str, bindings: tuple[str, ...]) -> None:
        self.resolve_definitions(
            tenant_id=tenant_id,
            project_id=project_id,
            target_id=target_id,
            bindings=bindings,
        )

    def resolve_definitions(
        self,
        *,
        tenant_id: str,
        project_id: str,
        target_id: str,
        bindings: tuple[str, ...],
    ) -> Mapping[str, str]:
        """Return exact symbol S-expressions from the bound KiCad libraries."""

        root_value = self._workspace_resolver(
            tenant_id=tenant_id,
            project_id=project_id,
            target_id=target_id,
        )
        root = root_value if isinstance(root_value, Path) else getattr(root_value, "root", None)
        if not isinstance(root, Path) or not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
        root = root.resolve(strict=True)
        table = root / "sym-lib-table"
        mappings = self._library_mappings(
            root,
            table,
            libraries=tuple(item.split(":", 1)[0] for item in bindings if isinstance(item, str) and ":" in item),
        )
        definitions: dict[str, str] = {}
        for binding in bindings:
            if not isinstance(binding, str) or binding.count(":") != 1:
                raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
            library, symbol = binding.split(":", 1)
            library_path = mappings.get(library)
            if library_path is None:
                raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
            try:
                if library_path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
                source = library_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE") from error
            definition = self._extract_symbol_definition(source, symbol)
            if definition is None:
                raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
            definitions[binding] = self._expand_symbol_definition(
                source,
                definition,
                binding=binding,
                symbol=symbol,
            )
        return definitions

    @staticmethod
    def _library_mappings(root: Path, table: Path, *, libraries: tuple[str, ...]) -> dict[str, Path]:
        mappings: dict[str, Path] = {}
        table_text = ""
        if table.is_file() and not table.is_symlink():
            if table.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
            table_text = table.read_text(encoding="utf-8")
        for match in re.finditer(
            r'\(lib\s+\(name\s+"(?P<name>[A-Za-z0-9_.-]+)"\).*?\(uri\s+"(?P<uri>[^"]+)"',
            table_text,
            flags=re.DOTALL,
        ):
            uri = match.group("uri")
            for variable in (
                "KICAD_SYMBOL_DIR",
                "KICAD6_SYMBOL_DIR",
                "KICAD7_SYMBOL_DIR",
                "KICAD8_SYMBOL_DIR",
                "KICAD9_SYMBOL_DIR",
                "KICAD10_SYMBOL_DIR",
            ):
                uri = uri.replace("${" + variable + "}", os.environ.get(variable, ""))
            candidate = Path(uri)
            if not candidate.is_absolute():
                candidate = root / candidate
            if candidate.exists() and candidate.is_file() and not candidate.is_symlink():
                mappings[match.group("name")] = candidate.resolve(strict=True)
        configured = (
            os.environ.get("KICAD_SYMBOL_DIR")
            or os.environ.get("KICAD8_SYMBOL_DIR")
            or os.environ.get("KICAD7_SYMBOL_DIR")
            or os.environ.get("KICAD6_SYMBOL_DIR")
            or os.environ.get("KICAD9_SYMBOL_DIR")
            or os.environ.get("KICAD10_SYMBOL_DIR")
        )
        if configured:
            symbol_root = Path(configured)
            for library in tuple(dict.fromkeys(libraries)):
                candidate = symbol_root / f"{library}.kicad_sym"
                if candidate.is_file() and not candidate.is_symlink():
                    mappings[library] = candidate.resolve(strict=True)
        return mappings

    @staticmethod
    def _contains_symbol(path: Path, binding: str, symbol: str) -> bool:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
        text = path.read_text(encoding="utf-8")
        escaped = re.escape(symbol)
        if path.suffix.casefold() == ".lib":
            return re.search(r"(?m)^DEF\s+" + escaped + r"(?:\s|$)", text) is not None
        return re.search(r'\(symbol\s+"(?:' + re.escape(binding) + r"|" + escaped + r')"(?:\s|\))', text) is not None

    @staticmethod
    def _extract_symbol_definition(source: str, symbol: str) -> str | None:
        """Extract one top-level KiCad symbol block without parsing arbitrary text."""

        if not isinstance(source, str) or not source.startswith("("):
            return None
        depth = 0
        in_quote = False
        escaped = False
        for index, character in enumerate(source):
            if in_quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_quote = False
                continue
            if character == '"':
                in_quote = True
                continue
            if character == ")":
                depth -= 1
                continue
            if character == "(":
                if depth == 1 and source.startswith('(symbol "', index):
                    header = re.match(r'\(symbol\s+"([^"\\]+)"', source[index:])
                    if header is not None and header.group(1) == symbol:
                        end = SchematicSymbolLibraryValidator._balanced_expression_end(source, index)
                        if end is None:
                            return None
                        return source[index:end]
                depth += 1
        return None

    @classmethod
    def _expand_symbol_definition(
        cls,
        source: str,
        definition: str,
        *,
        binding: str,
        symbol: str,
        seen: tuple[str, ...] = (),
    ) -> str:
        """Materialize one KiCad ``extends`` chain for a sheet symbol.

        KiCad library files may store a device as a small derived symbol with
        only an ``extends`` clause.  A standalone ``.kicad_sch`` must contain
        the resolved pin geometry and graphics; otherwise the CLI reports a
        library mismatch even though the short symbol name is valid.
        """

        extends = re.search(r'\(extends\s+"([^"\\]+)"\)', definition)
        if extends is None:
            return definition.replace(
                f'(symbol "{symbol}"',
                f'(symbol "{binding}"',
                1,
            )
        parent = extends.group(1)
        if parent in seen or len(seen) >= 8:
            raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
        parent_definition = cls._extract_symbol_definition(source, parent)
        if parent_definition is None:
            raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
        expanded = cls._expand_symbol_definition(
            source,
            parent_definition,
            binding=binding,
            symbol=parent,
            seen=(*seen, symbol),
        )
        expanded = expanded.replace(
            f'(symbol "{parent}"',
            f'(symbol "{binding}"',
            1,
        )
        for property_name, property_block in cls._top_level_named_expressions(definition, "property"):
            expanded = cls._replace_top_level_named_expression(
                expanded,
                "property",
                property_name,
                property_block,
            )
        # Child graphic/unit names are scoped to the derived symbol in a
        # standalone sheet library.  KiCad's resolver normally performs this
        # rename while materializing ``extends``; keep the emitted block
        # equivalent when we resolve it at the controlled adapter boundary.
        expanded = expanded.replace(f'"{parent}_', f'"{symbol}_')
        return expanded.replace(f'(extends "{parent}")', "", 1)

    @classmethod
    def _top_level_named_expressions(
        cls,
        source: str,
        keyword: str,
    ) -> tuple[tuple[str, str], ...]:
        result: list[tuple[str, str]] = []
        depth = 0
        in_quote = False
        escaped = False
        prefix = f"({keyword} \""
        for index, character in enumerate(source):
            if in_quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_quote = False
                continue
            if character == '"':
                in_quote = True
                continue
            if character == "(" and depth == 1 and source.startswith(prefix, index):
                header = re.match(re.escape(prefix) + r'([^"\\]+)"', source[index:])
                if header is not None:
                    end = cls._balanced_expression_end(source, index)
                    if end is None:
                        raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
                    result.append((header.group(1), source[index:end]))
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
        return tuple(result)

    @classmethod
    def _replace_top_level_named_expression(
        cls,
        source: str,
        keyword: str,
        name: str,
        replacement: str,
    ) -> str:
        prefix = f"({keyword} \""
        depth = 0
        in_quote = False
        escaped = False
        for index, character in enumerate(source):
            if in_quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_quote = False
                continue
            if character == '"':
                in_quote = True
                continue
            if character == "(" and depth == 1 and source.startswith(prefix + name + '"', index):
                end = cls._balanced_expression_end(source, index)
                if end is None:
                    raise ValueError("BLOCKED_KICAD_SYMBOL_UNAVAILABLE")
                return source[:index] + replacement + source[end:]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
        return source

    @staticmethod
    def _balanced_expression_end(source: str, start: int) -> int | None:
        depth = 0
        in_quote = False
        escaped = False
        for index in range(start, len(source)):
            character = source[index]
            if in_quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_quote = False
                continue
            if character == '"':
                in_quote = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    return index + 1
                if depth < 0:
                    return None
        return None


class SchematicErcAdapter:
    """Typed KiCad ERC seam. A real CLI adapter may be injected at this boundary."""

    def execute(self, operation: SchematicErcOperation, context=None) -> AdapterResult:
        raise NotImplementedError("KiCad ERC adapter is not configured")


class KiCadCliSchematicErcAdapter(SchematicErcAdapter):
    """Run a trusted KiCad CLI through the canonical Worker adapter boundary.

    The executable and workspace resolver are injected by production
    composition. User text never contributes command arguments; the adapter
    constructs a fixed argv and confines the target to the resolved workspace.
    """

    _COMMAND_IDENTITY = "kicad-cli sch erc"

    def __init__(
        self,
        *,
        executable: str,
        workspace_resolver: Callable[[SchematicErcOperation], Path],
        kicad_version: str = "UNKNOWN",
        timeout_seconds: float = 120.0,
        max_output_bytes: int = 256 * 1024,
        library_variables: Mapping[str, str] | None = None,
        runner: Callable[..., object] | None = None,
    ) -> None:
        if not isinstance(executable, str) or not executable:
            raise ValueError("KiCad executable is invalid")
        executable_name = Path(executable).name.casefold()
        if executable_name not in {"kicad-cli", "kicad-cli.exe"}:
            raise ValueError("KiCad executable is not trusted")
        # Production must receive the concrete executable discovered by the
        # composition layer.  A basename-only check would allow a caller to
        # redirect the real subprocess boundary to an arbitrary same-named
        # file.  Unit tests may still inject a runner, so they do not need a
        # host-installed KiCad binary.
        if runner is None:
            executable_path = Path(executable)
            if (
                not executable_path.is_absolute()
                or executable_path.is_symlink()
                or not executable_path.is_file()
            ):
                raise ValueError("KiCad executable path is not trusted")
        if not callable(workspace_resolver):
            raise TypeError("workspace resolver is required")
        if not isinstance(kicad_version, str) or not kicad_version or len(kicad_version) > 128 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+\-()/:]*", kicad_version) is None:
            raise ValueError("KiCad version is invalid")
        if type(timeout_seconds) not in {int, float} or not 1 <= timeout_seconds <= 600:
            raise ValueError("KiCad timeout is invalid")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 4 * 1024 * 1024:
            raise ValueError("KiCad output limit is invalid")
        if library_variables is None:
            library_variables = {}
        if not isinstance(library_variables, Mapping):
            raise TypeError("KiCad library variables are invalid")
        checked_library_variables: dict[str, str] = {}
        for key, value in library_variables.items():
            if (
                not isinstance(key, str)
                or re.fullmatch(r"KICAD[A-Z0-9_]*", key) is None
                or not isinstance(value, str)
                or not value
                or len(value) > 1024
                or "\x00" in value
            ):
                raise ValueError("KiCad library variable is invalid")
            checked_library_variables[key] = value
        self._executable = executable
        self._workspace_resolver = workspace_resolver
        self._kicad_version = kicad_version
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._library_variables = checked_library_variables
        self._runner = runner or run_kicad_cli

    def execute(self, operation: SchematicErcOperation, context=None) -> AdapterResult:
        checked = SchematicErcOperation.model_validate(
            operation.model_dump(mode="python") if isinstance(operation, SchematicErcOperation) else operation,
            strict=False,
        )
        started = datetime.now(UTC)
        try:
            root = self._workspace_resolver(checked)
            target = self._confined_target(root, checked.target_relative_path)
            if not target.is_file():
                return AdapterResult(
                    status="FAILED",
                    summary="KICAD_SCHEMATIC_UNAVAILABLE",
                    schematic_erc_result=self._result(
                        checked, started, SchematicErcStatus.ERC_BLOCKED, -2,
                        warnings=("Schematic target is unavailable.",),
                    ),
                )
            if target.stat().st_size > 16 * 1024 * 1024:
                return AdapterResult(
                    status="FAILED",
                    summary="KICAD_SCHEMATIC_TOO_LARGE",
                    schematic_erc_result=self._result(
                        checked, started, SchematicErcStatus.ERC_BLOCKED, -2,
                        warnings=("Schematic content exceeds the bounded ERC input size.",),
                    ),
                )
            content = target.read_bytes()
            content_fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
            if content_fingerprint != checked.schematic_artifact_fingerprint:
                return AdapterResult(
                    status="FAILED",
                    summary="KICAD_SCHEMATIC_WORKSPACE_DRIFT",
                    schematic_erc_result=self._result(
                        checked, started, SchematicErcStatus.ERC_BLOCKED, -2,
                        warnings=("Schematic content fingerprint does not match the approved receipt.",),
                    ),
                )
            with tempfile.TemporaryDirectory(prefix="embrix-kicad-erc-") as temp_dir:
                report_path = Path(temp_dir) / "erc.rpt"
                argv = (
                    self._executable,
                    "sch",
                    "erc",
                    checked.target_relative_path,
                    "--output",
                    str(report_path),
                    "--format",
                    "report",
                    "--severity-all",
                    "--exit-code-violations",
                )
                for key, value in sorted(self._library_variables.items()):
                    argv += ("--define-var", f"{key}={value}")
                runner_kwargs = {
                    "cwd": str(root.resolve(strict=True)),
                    "timeout_seconds": self._timeout_seconds,
                }
                if self._runner is run_kicad_cli:
                    runner_kwargs["environment"] = self._library_variables
                completed = self._runner(argv, **runner_kwargs)
                stdout = bytes(getattr(completed, "stdout", b"") or b"")[: self._max_output_bytes]
                stderr = bytes(getattr(completed, "stderr", b"") or b"")[: self._max_output_bytes]
                version = self._kicad_version
                if version == "UNKNOWN":
                    first_line = stdout.decode("utf-8", errors="ignore").splitlines()
                    if first_line and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+\-()/:]*", first_line[0].strip()):
                        version = first_line[0].strip()[:128]
                report = report_path.read_bytes()[: self._max_output_bytes] if report_path.is_file() else b""
                after_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
                if after_fingerprint != content_fingerprint:
                    return AdapterResult(
                        status="FAILED",
                        summary="KICAD_SCHEMATIC_WORKSPACE_DRIFT",
                        schematic_erc_result=self._result(
                            checked,
                            started,
                            SchematicErcStatus.ERC_BLOCKED,
                            -2,
                            kicad_version=version,
                            raw_evidence=report,
                            report_artifact=f"embedded-copilot/erc/{checked.operation_id}.rpt" if report else None,
                        ),
                    )
                parsed_violations = self._parse_violations(report)
                parsed_issues = self._parse_issues(
                    report,
                    operation_id=checked.operation_id,
                    artifact_fingerprint=content_fingerprint,
                )
                report_decodable = True
                if report:
                    try:
                        report.decode("utf-8")
                    except UnicodeDecodeError:
                        report_decodable = False
                # KiCad can include informational and warning blocks in the
                # report even when the configured ``--severity-error`` policy
                # has no blocking diagnostics.  Preserve only blocking
                # diagnostics in ``violations`` so ERC_PASS remains truthful;
                # non-blocking output is represented by the bounded warnings
                # field instead of being promoted to a failure.
                violations = tuple(item for item in parsed_violations if item.severity.casefold() in {"error", "violation"})
                non_blocking = tuple(
                    f"{item.severity}: {item.rule}: {item.message}"
                    for item in parsed_violations
                    if item.severity.casefold() not in {"error", "violation"}
                )
                if not report:
                    status = SchematicErcStatus.ERC_BLOCKED
                    summary = "KICAD_ERC_REPORT_UNAVAILABLE"
                elif not report_decodable:
                    status = SchematicErcStatus.ERC_BLOCKED
                    summary = "KICAD_ERC_REPORT_INVALID"
                elif violations:
                    status = SchematicErcStatus.ERC_FAIL
                    summary = "KICAD_ERC_VIOLATIONS"
                elif parsed_issues:
                    status = SchematicErcStatus.ERC_WARNINGS
                    summary = "KICAD_ERC_WARNINGS"
                elif int(getattr(completed, "returncode", -1)) == 0:
                    status = SchematicErcStatus.ERC_PASS
                    summary = "KICAD_ERC_PASS"
                else:
                    status = SchematicErcStatus.ERC_BLOCKED
                    summary = "KICAD_ERC_BLOCKED"
                generic_warning = ()
                if status is not SchematicErcStatus.ERC_PASS and not parsed_violations:
                    generic_warning = (
                        "KiCad ERC returned diagnostics without a trusted structured violation list."
                        if stderr or stdout or not report
                        else "KiCad ERC did not produce a passing result."
                    ,)
                warnings = non_blocking + generic_warning
                erc_result = self._result(
                    checked, started, status, int(getattr(completed, "returncode", -1)),
                    violations=violations,
                    issues=parsed_issues,
                    warnings=warnings,
                    report_artifact=f"embedded-copilot/erc/{checked.operation_id}.rpt" if report else None,
                    kicad_version=version,
                    raw_evidence=report,
                )
                evidence_references = (
                    (f"erc-evidence-{checked.operation_id}-{erc_result.fingerprint[7:]}",)
                    if report
                    and status
                    in {
                        SchematicErcStatus.ERC_PASS,
                        SchematicErcStatus.ERC_FAIL,
                        SchematicErcStatus.ERC_WARNINGS,
                    }
                    else ()
                )
                return AdapterResult(
                    status="SUCCEEDED" if status in {SchematicErcStatus.ERC_PASS, SchematicErcStatus.ERC_FAIL, SchematicErcStatus.ERC_WARNINGS} else "FAILED",
                    summary=summary,
                    evidence_references=evidence_references,
                    schematic_erc_result=erc_result,
                )
        except TimeoutError:
            return AdapterResult(
                status="FAILED",
                summary="KICAD_ERC_TIMEOUT",
                schematic_erc_result=self._result(
                    checked, started, SchematicErcStatus.ERC_BLOCKED, -2,
                    warnings=("KiCad ERC timed out.",),
                ),
            )
        except (PermissionError, ValueError):
            return AdapterResult(
                status="FAILED",
                summary="KICAD_ERC_BLOCKED",
                schematic_erc_result=self._result(
                    checked, started, SchematicErcStatus.ERC_BLOCKED, -2,
                    warnings=("The schematic target or workspace binding was rejected.",),
                ),
            )
        except (FileNotFoundError, OSError):
            return AdapterResult(
                status="UNAVAILABLE",
                summary="KICAD_ERC_UNAVAILABLE",
                schematic_erc_result=self._result(
                    checked, started, SchematicErcStatus.ERC_UNAVAILABLE, -1,
                    warnings=("KiCad CLI is unavailable in this environment.",),
                ),
            )

    @staticmethod
    def _confined_target(root: Path, relative_path: str) -> Path:
        if not isinstance(root, Path) or not root.exists() or not root.is_dir() or root.is_symlink():
            raise ValueError("workspace root is invalid")
        resolved_root = root.resolve(strict=True)
        candidate = resolved_root.joinpath(*relative_path.split("/"))
        if candidate.is_symlink() or any(part.is_symlink() for part in (resolved_root, *candidate.parents)):
            raise ValueError("schematic target contains a symlink")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(resolved_root)
        except ValueError as error:
            raise ValueError("schematic target escapes workspace") from error
        return resolved

    @staticmethod
    def _parse_violations(report: bytes) -> tuple[SchematicErcViolation, ...]:
        try:
            text = report.decode("utf-8")
        except UnicodeDecodeError:
            return ()
        result: list[SchematicErcViolation] = []
        # KiCad's report format is intentionally parsed as a structured
        # diagnostic block rather than inferred from arbitrary prose.  Each
        # block starts with ``[rule]: message`` and has an explicit severity
        # marker on the following line.  The location line is retained only
        # as a bounded source reference; it is not interpreted as an object
        # identity.
        pending_rule: str | None = None
        pending_message: str | None = None
        pending_line = 0
        for index, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^\s*\[(error|warning|info)\]\s+rule=([A-Za-z0-9_.:-]+)\s+message=(.+?)\s*$", line, re.IGNORECASE)
            if match is not None:
                result.append(
                    SchematicErcViolation.create(
                        severity=match.group(1).upper(),
                        rule=match.group(2),
                        message=match.group(3),
                        object_refs=(),
                        raw_source_ref=f"report:line-{index}",
                    )
                )
                continue
            ki_rule = re.match(r"^\s*\[(?P<rule>[^\]]{1,128})\]:\s*(?P<message>\S.*)$", line)
            if ki_rule is not None:
                pending_rule = ki_rule.group("rule").strip()
                pending_message = ki_rule.group("message").strip()
                pending_line = index
                continue
            severity = re.match(r"^\s*;\s*(?P<severity>error|warning|info)\s*$", line, re.IGNORECASE)
            if severity is not None and pending_rule is not None and pending_message is not None:
                result.append(
                    SchematicErcViolation.create(
                        severity=severity.group("severity").upper(),
                        rule=pending_rule,
                        message=pending_message,
                        object_refs=(),
                        raw_source_ref=f"report:line-{pending_line}",
                    )
                )
                pending_rule = None
                pending_message = None
                pending_line = 0
        return tuple(result)

    @classmethod
    def _parse_issues(
        cls,
        report: bytes,
        *,
        operation_id: str,
        artifact_fingerprint: str,
    ) -> tuple[SchematicErcIssue, ...]:
        """Parse only explicit KiCad report blocks into normalized issues."""

        try:
            text_value = report.decode("utf-8")
        except UnicodeDecodeError:
            return ()
        issues: list[SchematicErcIssue] = []
        pending_rule: str | None = None
        pending_message: str | None = None
        pending_severity: SchematicErcIssueSeverity | None = None
        pending_line = 0
        pending_location: str | None = None
        pending_component: str | None = None
        pending_pin: str | None = None

        def flush() -> None:
            nonlocal pending_rule, pending_message, pending_severity
            nonlocal pending_line, pending_location, pending_component, pending_pin
            if pending_rule is None or pending_message is None or pending_severity is None:
                return
            raw_ref = f"report:line-{pending_line}"
            issue_id = "erc-issue-" + hashlib.sha256(
                f"{operation_id}|{artifact_fingerprint}|{pending_line}|{pending_rule}|{pending_message}".encode("utf-8")
            ).hexdigest()[:32]
            rule_lower = pending_rule.casefold()
            if pending_severity is not SchematicErcIssueSeverity.ERROR:
                classification = (
                    SchematicErcIssueClassification.LIBRARY_OR_EDA_MAPPING_DEFECT
                    if "lib" in rule_lower or "symbol" in rule_lower
                    else SchematicErcIssueClassification.NON_BLOCKING_WARNING
                )
            elif any(marker in rule_lower for marker in ("format", "render", "serializer")):
                classification = SchematicErcIssueClassification.ARTIFACT_RENDERING_DEFECT
            elif any(
                marker in rule_lower
                for marker in (
                    "pin_not_connected",
                    "pin_not_driven",
                    "no_driver",
                    "dangling",
                    "unconnected",
                )
            ):
                classification = SchematicErcIssueClassification.PLAN_OR_TRUTH_DEFECT
            else:
                classification = SchematicErcIssueClassification.UNCLASSIFIED
            issues.append(
                SchematicErcIssue.create(
                    issue_id=issue_id,
                    severity=pending_severity,
                    rule_id=pending_rule,
                    message=pending_message,
                    component_ref=pending_component,
                    pin_ref=pending_pin,
                    location=pending_location,
                    raw_evidence_ref=raw_ref,
                    artifact_fingerprint=artifact_fingerprint,
                    classification=classification,
                )
            )
            pending_rule = None
            pending_message = None
            pending_severity = None
            pending_line = 0
            pending_location = None
            pending_component = None
            pending_pin = None

        for index, line in enumerate(text_value.splitlines(), start=1):
            match = re.match(r"^\s*\[(?P<rule>[^\]]{1,256})\]:\s*(?P<message>\S.*)$", line)
            if match is not None:
                flush()
                pending_rule = match.group("rule").strip()
                pending_message = match.group("message").strip()
                pending_severity = None
                pending_line = index
                pending_location = None
                pending_component = None
                pending_pin = None
                continue
            location = re.match(r"^\s*@\((?P<location>[^)]{1,256})\):\s*(?P<object>.+)$", line)
            if location is not None and pending_rule is not None:
                pending_location = location.group("location").strip()
                object_text = location.group("object").strip()
                object_match = re.match(r"Symbol\s+(?P<component>[A-Za-z0-9_.-]+)(?:\s+Pin\s+(?P<pin>[A-Za-z0-9_.-]+))?", object_text)
                if object_match is not None:
                    pending_component = object_match.group("component")
                    pin = object_match.group("pin")
                    pending_pin = None if pin is None else f"{pending_component}.{pin}"
                continue
            severity = re.match(r"^\s*;\s*(?P<severity>error|warning|info)\s*$", line, re.IGNORECASE)
            if severity is None or pending_rule is None or pending_message is None:
                continue
            pending_severity = SchematicErcIssueSeverity(severity.group("severity").upper())
        flush()
        return tuple(issues)

    @staticmethod
    def _result(
        operation: SchematicErcOperation,
        started: datetime,
        status: SchematicErcStatus,
        exit_status: int,
        *,
        kicad_version: str = "UNKNOWN",
        violations: tuple[SchematicErcViolation, ...] = (),
        issues: tuple[SchematicErcIssue, ...] = (),
        warnings: tuple[str, ...] = (),
        report_artifact: str | None = None,
        raw_evidence: bytes = b"",
    ) -> SchematicErcResult:
        finished = datetime.now(UTC)
        raw_text = raw_evidence.decode("utf-8", errors="replace").strip()
        engineering_status = {
            SchematicErcStatus.ERC_PASS: SchematicErcEngineeringStatus.PASS,
            SchematicErcStatus.ERC_WARNINGS: SchematicErcEngineeringStatus.WARNINGS_REMAIN,
            SchematicErcStatus.ERC_FAIL: SchematicErcEngineeringStatus.BLOCKED,
            SchematicErcStatus.ERC_BLOCKED: SchematicErcEngineeringStatus.BLOCKED,
            SchematicErcStatus.ERC_UNAVAILABLE: SchematicErcEngineeringStatus.BLOCKED,
        }[status]
        return SchematicErcResult.create(
            operation_id=operation.operation_id,
            schematic_artifact_ref=operation.target_relative_path,
            schematic_content_fingerprint=operation.schematic_artifact_fingerprint,
            kicad_version=kicad_version,
            exit_status=exit_status,
            violations=violations,
            issues=issues,
            warnings=warnings,
            report_artifact=report_artifact,
            raw_evidence=raw_text,
            raw_evidence_fingerprint=("sha256:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()) if raw_text else None,
            command_identity="kicad-cli sch erc",
            engineering_status=engineering_status,
            started_at=started,
            finished_at=finished,
            receipt_ref=operation.source_operation_receipt_fingerprint,
            status=status,
        )


class UnavailableSchematicErcAdapter(SchematicErcAdapter):
    def execute(self, operation: SchematicErcOperation, context=None) -> AdapterResult:
        checked = SchematicErcOperation.model_validate(operation.model_dump(mode="python"), strict=False)
        now = datetime.now(UTC)
        result = SchematicErcResult.create(
            operation_id=checked.operation_id,
            schematic_artifact_ref=checked.schematic_artifact_ref,
            kicad_version="UNAVAILABLE",
            exit_status=-1,
            violations=(),
            warnings=("KiCad CLI is unavailable in this environment.",),
            report_artifact=None,
            started_at=now,
            finished_at=now,
            receipt_ref=checked.source_operation_receipt_fingerprint,
            status=SchematicErcStatus.ERC_UNAVAILABLE,
        )
        return AdapterResult(status="UNAVAILABLE", summary="KICAD_ERC_UNAVAILABLE", schematic_erc_result=result)


__all__ = ("KiCadCliSchematicErcAdapter", "SchematicErcAdapter", "UnavailableSchematicErcAdapter")
