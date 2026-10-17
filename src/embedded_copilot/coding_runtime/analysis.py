from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass

from tree_sitter import Language, Parser
import tree_sitter_c
import tree_sitter_cpp

from embedded_copilot.coding_runtime.contracts.models import (
    BuildAnalysisRequest,
    BuildAnalysisResponse,
    BuildIssue,
    BuildSystem,
    ChangeCandidate,
    ChangeReview,
    CodeDependency,
    CodeFileInput,
    CodeFileSummary,
    CodeLanguage,
    CodeSymbol,
    DiffReviewRequest,
    FrozenCodeContextSnapshot,
    HardwareAccess,
    HardwareSoftwareConflictCandidate,
    HardwareSoftwareFusionRequest,
    HardwareSoftwareFusionResponse,
    ParseIssue,
    ProjectAnalysisRequest,
    ProjectAnalysisResponse,
    ProjectSummary,
    ProjectType,
    SymbolKind,
    snapshot_fingerprint,
)

_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
_MACRO = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)", re.MULTILINE)
_STRUCT = re.compile(r"\bstruct\s+([A-Za-z_]\w*)")
_ENUM = re.compile(r"\benum\s+([A-Za-z_]\w*)")
_CLASS = re.compile(r"\bclass\s+([A-Za-z_]\w*)")
_FUNCTION = re.compile(r"([A-Za-z_]\w*)\s*\(")
_VARIABLE = re.compile(
    r"\b(?:const\s+)?(?:volatile\s+)?(?:unsigned\s+|signed\s+)?"
    r"(?:char|short|int|long|float|double|bool|uint(?:8|16|32|64)_t|"
    r"int(?:8|16|32|64)_t|[A-Za-z_]\w*)\s+\**([A-Za-z_]\w*)"
    r"\s*(?:=|;|\[)"
)
_GPIO_WRITE = re.compile(
    r"\bHAL_GPIO_WritePin\s*\(\s*GPIO([A-Z])\s*,\s*GPIO_PIN_(\d{1,2})\b"
)
_DIAGNOSTIC = re.compile(
    r"(?P<file>[^\s:]+(?:[/\\][^\s:]+)*):(?P<line>\d+)(?::\d+)?:\s*(?P<kind>error|warning):\s*(?P<detail>.+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedFile:
    path: str
    language: CodeLanguage
    symbols: tuple[CodeSymbol, ...]
    dependencies: tuple[CodeDependency, ...]
    hardware_accesses: tuple[HardwareAccess, ...]
    issue: ParseIssue | None


@dataclass(frozen=True)
class FirmwareParsedSymbol:
    """Internal Tree-sitter result for project-scoped firmware indexing."""

    name: str
    symbol_type: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class FirmwareParsedCallEdge:
    caller: str
    callee: str
    line: int


@dataclass(frozen=True)
class FirmwareParsedFile:
    path: str
    symbols: tuple[FirmwareParsedSymbol, ...]
    calls: tuple[FirmwareParsedCallEdge, ...]
    issue: ParseIssue | None


def _language_for(path: str) -> CodeLanguage:
    filename = path.rsplit("/", 1)[-1]
    suffix = "." + filename.rsplit(".", 1)[1].casefold() if "." in filename else ""
    if suffix in {".c", ".h", ".ino", ".s"}:
        return CodeLanguage.C
    if suffix in {".cc", ".cpp", ".cxx", ".hpp", ".hxx"}:
        return CodeLanguage.CPP
    if suffix == ".py":
        return CodeLanguage.PYTHON
    return CodeLanguage.UNKNOWN


def _line_of(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


class TreeSitterCodeParser:
    def __init__(self) -> None:
        self._c = Language(tree_sitter_c.language())
        self._cpp = Language(tree_sitter_cpp.language())

    def parse(self, path: str, content: str) -> _ParsedFile:
        language = _language_for(path)
        if language is CodeLanguage.PYTHON:
            return self._parse_python(path, content)
        if language in {CodeLanguage.C, CodeLanguage.CPP}:
            return self._parse_c_family(path, content, language)
        return _ParsedFile(path, language, (), (), (), None)

    def parse_firmware(self, path: str, content: str) -> FirmwareParsedFile:
        """Parse the small C/C++ subset needed by FirmwareIndex.

        This is deliberately source-structure extraction, not C semantic
        analysis: direct calls, declarations and preprocessor references only.
        """
        language = _language_for(path)
        if language not in {CodeLanguage.C, CodeLanguage.CPP}:
            return FirmwareParsedFile(path, (), (), None)
        parser = Parser(self._cpp if language is CodeLanguage.CPP else self._c)
        tree = parser.parse(content.encode("utf-8"))
        error_node = next((node for node in _walk(tree.root_node) if node.type == "ERROR"), None)
        if error_node is not None:
            return FirmwareParsedFile(
                path,
                (),
                (),
                ParseIssue(file=path, line=error_node.start_point[0] + 1, message="syntax error"),
            )

        symbols: list[FirmwareParsedSymbol] = []
        calls: list[FirmwareParsedCallEdge] = []
        for match in _INCLUDE.finditer(content):
            symbols.append(FirmwareParsedSymbol(match.group(1), "include", _line_of(content, match.start(1)), _line_of(content, match.start(1))))
        for match in _MACRO.finditer(content):
            symbols.append(FirmwareParsedSymbol(match.group(1), "macro", _line_of(content, match.start(1)), _line_of(content, match.start(1))))
        for match in _VARIABLE.finditer(content):
            symbols.append(FirmwareParsedSymbol(match.group(1), "variable", _line_of(content, match.start(1)), _line_of(content, match.start(1))))

        for node in _walk(tree.root_node):
            source = content[node.start_byte:node.end_byte]
            line_start = node.start_point[0] + 1
            line_end = node.end_point[0] + 1
            if node.type == "struct_specifier":
                match = _STRUCT.search(source)
                if match:
                    symbols.append(FirmwareParsedSymbol(match.group(1), "struct", line_start, line_end))
            elif node.type == "enum_specifier":
                match = _ENUM.search(source)
                if match:
                    symbols.append(FirmwareParsedSymbol(match.group(1), "enum", line_start, line_end))
            elif node.type == "function_definition":
                names = _FUNCTION.findall(source)
                if not names:
                    continue
                caller = names[0]
                symbols.append(FirmwareParsedSymbol(caller, "function", line_start, line_end))
                for child in _walk(node):
                    if child.type != "call_expression":
                        continue
                    function = child.child_by_field_name("function")
                    if function is None or function.type != "identifier":
                        continue
                    callee = content[function.start_byte:function.end_byte]
                    calls.append(FirmwareParsedCallEdge(caller, callee, child.start_point[0] + 1))
            elif node.type == "declaration" and any(
                child.type == "function_declarator" for child in _walk(node)
            ):
                names = _FUNCTION.findall(source)
                if names:
                    symbols.append(
                        FirmwareParsedSymbol(names[0], "function", line_start, line_end)
                    )

        unique_symbols = {
            (item.name, item.symbol_type, item.line_start, item.line_end): item
            for item in symbols
        }
        unique_calls = {
            (item.caller, item.callee, item.line): item
            for item in calls
        }
        return FirmwareParsedFile(
            path,
            tuple(unique_symbols[key] for key in sorted(unique_symbols, key=lambda value: (value[2], value[1], value[0]))),
            tuple(unique_calls[key] for key in sorted(unique_calls)),
            None,
        )

    def _parse_c_family(
        self, path: str, content: str, language: CodeLanguage
    ) -> _ParsedFile:
        parser = Parser(self._cpp if language is CodeLanguage.CPP else self._c)
        tree = parser.parse(content.encode("utf-8"))
        error_node = next(
            (node for node in _walk(tree.root_node) if node.type == "ERROR"), None
        )
        if error_node is not None:
            return _ParsedFile(
                path,
                language,
                (),
                (),
                (),
                ParseIssue(
                    file=path,
                    line=error_node.start_point[0] + 1,
                    message="syntax error",
                ),
            )

        symbols: list[CodeSymbol] = []
        dependencies: list[CodeDependency] = []
        for match in _INCLUDE.finditer(content):
            name = match.group(1)
            line = _line_of(content, match.start(1))
            symbols.append(
                CodeSymbol(
                    file=path,
                    line=line,
                    kind=SymbolKind.INCLUDE,
                    name=name.replace("/", "_"),
                )
            )
            dependencies.append(
                CodeDependency(file=path, line=line, name=name, kind="include")
            )
        for match in _MACRO.finditer(content):
            symbols.append(
                CodeSymbol(
                    file=path,
                    line=_line_of(content, match.start(1)),
                    kind=SymbolKind.MACRO,
                    name=match.group(1),
                )
            )
        for node in _walk(tree.root_node):
            source = content[node.start_byte : node.end_byte]
            if node.type == "struct_specifier":
                match = _STRUCT.search(source)
                if match:
                    symbols.append(
                        CodeSymbol(
                            file=path,
                            line=node.start_point[0] + 1,
                            kind=SymbolKind.STRUCT,
                            name=match.group(1),
                        )
                    )
            elif node.type in {"class_specifier", "class_definition"}:
                match = _CLASS.search(source)
                if match:
                    symbols.append(
                        CodeSymbol(
                            file=path,
                            line=node.start_point[0] + 1,
                            kind=SymbolKind.CLASS,
                            name=match.group(1),
                        )
                    )
            elif node.type == "function_definition":
                names = _FUNCTION.findall(source)
                if names:
                    symbols.append(
                        CodeSymbol(
                            file=path,
                            line=node.start_point[0] + 1,
                            kind=SymbolKind.FUNCTION,
                            name=names[0],
                        )
                    )
        hardware = tuple(
            HardwareAccess(
                resource=f"P{match.group(1)}{match.group(2)}",
                operation="digital_write",
                line=_line_of(content, match.start()),
            )
            for match in _GPIO_WRITE.finditer(content)
        )
        return _ParsedFile(
            path, language, tuple(symbols), tuple(dependencies), hardware, None
        )

    def _parse_python(self, path: str, content: str) -> _ParsedFile:
        try:
            tree = ast.parse(content)
        except SyntaxError as error:
            return _ParsedFile(
                path,
                CodeLanguage.PYTHON,
                (),
                (),
                (),
                ParseIssue(file=path, line=error.lineno or 1, message="syntax error"),
            )
        symbols: list[CodeSymbol] = []
        dependencies: list[CodeDependency] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    CodeSymbol(
                        file=path,
                        line=node.lineno,
                        kind=SymbolKind.FUNCTION,
                        name=node.name,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                symbols.append(
                    CodeSymbol(
                        file=path,
                        line=node.lineno,
                        kind=SymbolKind.CLASS,
                        name=node.name,
                    )
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    dependencies.append(
                        CodeDependency(
                            file=path, line=node.lineno, name=alias.name, kind="import"
                        )
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                dependencies.append(
                    CodeDependency(
                        file=path, line=node.lineno, name=node.module, kind="import"
                    )
                )
        return _ParsedFile(
            path, CodeLanguage.PYTHON, tuple(symbols), tuple(dependencies), (), None
        )


class DeterministicProjectAnalyzer:
    def analyze(self, files: tuple[CodeFileInput, ...]) -> ProjectSummary:
        paths = tuple(item.path for item in files)
        folded = tuple(path.casefold() for path in paths)
        contents = {item.path.casefold(): item.content for item in files}
        cmake = contents.get("cmakelists.txt", "").casefold()
        stm32 = (
            any(path.startswith("core/") for path in folded)
            and any(path.startswith("drivers/") for path in folded)
            and any(
                path.endswith(".ioc")
                or "/startup" in path
                and path.endswith(".s")
                or path.endswith(".ld")
                for path in folded
            )
        )
        esp_idf = "project.cmake" in cmake and (
            any(path.startswith("main/") for path in folded)
            or any(path.startswith("components/") for path in folded)
        )
        arduino = any(path.endswith(".ino") for path in folded) or (
            any(path.startswith("src/") for path in folded)
            and any(path.startswith("libraries/") for path in folded)
        )
        zephyr = "prj.conf" in folded and "zephyr" in cmake
        recognized = tuple(
            name
            for name, active in (
                (ProjectType.STM32CUBEMX, stm32),
                (ProjectType.ESP_IDF, esp_idf),
                (ProjectType.ARDUINO, arduino),
                (ProjectType.ZEPHYR, zephyr),
            )
            if active
        )
        project_type = recognized[0] if len(recognized) == 1 else ProjectType.GENERIC
        frameworks: list[str] = []
        if project_type is ProjectType.STM32CUBEMX:
            frameworks.append("STM32")
        elif project_type is ProjectType.ESP_IDF:
            frameworks.append("ESP_IDF")
        elif project_type is ProjectType.ARDUINO:
            frameworks.append("ARDUINO")
        elif project_type is ProjectType.ZEPHYR:
            frameworks.append("ZEPHYR")
        if any(
            "freertos.h" in item.content.casefold()
            or "/freertos/" in item.path.casefold()
            for item in files
        ):
            frameworks.append("FREERTOS")
        if any(path.endswith(".ino") for path in folded):
            build_system = BuildSystem.ARDUINO
        elif "platformio.ini" in folded:
            build_system = BuildSystem.PLATFORMIO
        elif "west.yml" in folded:
            build_system = BuildSystem.WEST
        elif "cmakelists.txt" in folded:
            build_system = BuildSystem.CMAKE
        elif "makefile" in folded:
            build_system = BuildSystem.MAKE
        else:
            build_system = BuildSystem.UNKNOWN
        return ProjectSummary(
            project_type=project_type,
            frameworks=tuple(frameworks),
            build_system=build_system,
            marker_candidates=tuple(item.value for item in recognized),
        )


class DeterministicBuildAnalyzer:
    def analyze(self, request: BuildAnalysisRequest) -> BuildAnalysisResponse:
        issues: list[BuildIssue] = []
        for line in request.log.splitlines():
            match = _DIAGNOSTIC.search(line)
            if match and match.group("kind").casefold() == "error":
                filename = match.group("file").replace("\\", "/")
                if ":" in filename or filename.startswith("/"):
                    filename = filename.rsplit("/", 1)[-1]
                evidence = "observed: " + " ".join(line.split())[:512]
                issues.append(
                    BuildIssue(
                        error_type="COMPILER_ERROR",
                        file=filename,
                        line=int(match.group("line")),
                        evidence=evidence,
                        suggestion="Review the observed diagnostic against the declared source and build configuration.",
                    )
                )
            elif "undefined reference to" in line.casefold():
                issues.append(
                    BuildIssue(
                        error_type="LINKER_ERROR",
                        evidence="observed: " + " ".join(line.split())[:512],
                        suggestion="Review the observed diagnostic against the declared source and build configuration.",
                    )
                )
        return BuildAnalysisResponse(issues=tuple(issues))


class DeterministicDiffAnalyzer:
    def analyze(self, request: DiffReviewRequest) -> ChangeReview:
        additions = tuple(
            line[1:]
            for line in request.diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = tuple(
            line[1:]
            for line in request.diff.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        candidates: list[ChangeCandidate] = []
        if any("(" in line and ";" in line for line in additions) and any(
            "(" in line and ";" in line for line in removed
        ):
            candidates.append(
                ChangeCandidate(
                    category="API_CHANGE",
                    description="Public declaration change is an unverified compatibility candidate.",
                )
            )
        joined = "\n".join(additions)
        if "HAL_GPIO_" in joined or "GPIO_PIN_" in joined:
            candidates.append(
                ChangeCandidate(
                    category="MCU_RESOURCE",
                    description="MCU resource usage is an unverified review candidate.",
                )
            )
        if re.search(r"\bHAL_(SPI|I2C|UART|TIM|ADC)_", joined):
            candidates.append(
                ChangeCandidate(
                    category="PERIPHERAL_CONFIGURATION",
                    description="Peripheral configuration is an unverified review candidate.",
                )
            )
        if re.search(r"\b(strcpy|sprintf|malloc|free)\b", joined):
            candidates.append(
                ChangeCandidate(
                    category="POTENTIAL_BUG",
                    description="Potential defect pattern is an unverified review candidate.",
                )
            )
        return ChangeReview(candidates=tuple(candidates))


class DeterministicFusionAnalyzer:
    def analyze(
        self, request: HardwareSoftwareFusionRequest
    ) -> HardwareSoftwareFusionResponse:
        accessed = {
            access.resource
            for file in request.snapshot.files
            for access in file.hardware_accesses
        }
        candidates = tuple(
            HardwareSoftwareConflictCandidate(
                pin=item.pin,
                candidate_function=item.function,
                reference_id=item.reference_id,
                description="Hardware/software relationship candidate requires engineer verification.",
            )
            for item in sorted(
                request.pin_candidates,
                key=lambda value: (value.pin, value.function, value.reference_id),
            )
            if item.pin in accessed
        )
        return HardwareSoftwareFusionResponse(conflict_candidates=candidates)


class DeterministicCodingPort:
    def __init__(
        self,
        parser: TreeSitterCodeParser,
        project_analyzer: DeterministicProjectAnalyzer,
        build_analyzer: DeterministicBuildAnalyzer,
        diff_analyzer: DeterministicDiffAnalyzer,
        fusion_analyzer: DeterministicFusionAnalyzer,
    ) -> None:
        self._parser = parser
        self._project_analyzer = project_analyzer
        self._build_analyzer = build_analyzer
        self._diff_analyzer = diff_analyzer
        self._fusion_analyzer = fusion_analyzer

    def analyze_project(
        self, request: ProjectAnalysisRequest
    ) -> ProjectAnalysisResponse:
        parsed = tuple(
            self._parser.parse(item.path, item.content) for item in request.files
        )
        summary = self._project_analyzer.analyze(request.files)
        files = tuple(
            sorted(
                (
                    CodeFileSummary(
                        path=item.path,
                        language=item.language,
                        content_sha256=hashlib.sha256(
                            source.content.encode("utf-8")
                        ).hexdigest(),
                        line_count=source.content.count("\n")
                        + (1 if source.content else 0),
                        hardware_accesses=item.hardware_accesses,
                    )
                    for source, item in zip(request.files, parsed, strict=True)
                ),
                key=lambda item: item.path.casefold(),
            )
        )
        symbols = tuple(
            sorted(
                (symbol for item in parsed for symbol in item.symbols),
                key=lambda item: (
                    item.file.casefold(),
                    item.line,
                    item.kind.value,
                    item.name,
                ),
            )
        )
        dependencies = tuple(
            sorted(
                (dependency for item in parsed for dependency in item.dependencies),
                key=lambda item: (
                    item.file.casefold(),
                    item.line,
                    item.kind,
                    item.name,
                ),
            )
        )
        language_set = {
            item.language
            for item in parsed
            if item.language is not CodeLanguage.UNKNOWN
        }
        language = (
            next(iter(language_set))
            if len(language_set) == 1
            else CodeLanguage.MIXED if language_set else CodeLanguage.UNKNOWN
        )
        fingerprint = snapshot_fingerprint(
            schema_version="1.0",
            context_id=request.context_id,
            project_type=summary.project_type,
            language=language,
            frameworks=summary.frameworks,
            build_system=summary.build_system,
            files=files,
            symbols=symbols,
            dependencies=dependencies,
        )
        snapshot = FrozenCodeContextSnapshot(
            schema_version="1.0",
            snapshot_fingerprint=fingerprint,
            context_id=request.context_id,
            project_type=summary.project_type,
            language=language,
            frameworks=summary.frameworks,
            build_system=summary.build_system,
            files=files,
            symbols=symbols,
            dependencies=dependencies,
        )
        return ProjectAnalysisResponse(
            snapshot=snapshot,
            project_summary=summary,
            parse_issues=tuple(item.issue for item in parsed if item.issue is not None),
        )

    def analyze_build(self, request: BuildAnalysisRequest) -> BuildAnalysisResponse:
        return self._build_analyzer.analyze(request)

    def review_diff(self, request: DiffReviewRequest) -> ChangeReview:
        return self._diff_analyzer.analyze(request)

    def analyze_hardware_software(
        self, request: HardwareSoftwareFusionRequest
    ) -> HardwareSoftwareFusionResponse:
        return self._fusion_analyzer.analyze(request)
