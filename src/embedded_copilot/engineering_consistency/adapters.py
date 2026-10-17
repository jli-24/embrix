from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Protocol

import tree_sitter_c
from tree_sitter import Language, Parser

from embedded_copilot.engineering_copilot import (
    FirmwareInterfaceRole,
    FirmwareModule,
    FirmwareModuleType,
    FirmwareSpecification,
    HardwareFirmwareBinding,
    ImplementationSpecification,
    PlanProvenance,
    SimulationEvidenceRequirement,
    SimulationEvidenceType,
    SimulationModel,
    SimulationScenario,
    SimulationScenarioType,
    SimulationSpecification,
    SimulationTargetType,
    VerificationCase,
    VerificationCaseType,
    VerificationEvidenceRequirement,
    VerificationEvidenceType,
    VerificationIntent,
    VerificationSpecification,
)
from embedded_copilot.engineering_copilot.contracts import (
    _firmware_revision,
    _implementation_revision,
    _simulation_revision,
    _verification_revision,
)
from embedded_copilot.engineering_pcb import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBSourceFormat,
    content_fingerprint,
)
from embedded_copilot.engineering_pcb.adapters.bom import CSVPCBParserAdapter
from embedded_copilot.engineering_pcb.adapters.kicad import (
    KiCadBoardParserAdapter,
    KiCadSchematicParserAdapter,
)
from embedded_copilot.execution_worker.adapters import (
    SafeProcessRunner,
    probe_trusted_tool_version,
    run_trusted_tool,
)
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration
from embedded_copilot.firmware_engineering.models import canonical_fingerprint
from embedded_copilot.input.models import AttachmentType, UserAttachment
from embedded_copilot.pcb.parser import KiCadPCBParser, RootedPCBSourceResolver

from .contracts import (
    ArtifactLineage,
    BoardConnectionObservation,
    BOMObservation,
    BuildSnapshot,
    ConsistencyInput,
    ELFInspection,
    ELFSectionObservation,
    FirmwareFacts,
    FirmwarePinObservation,
    HardwareArtifactSnapshot,
    HardwareFirmwareTruth,
    HardwarePinTruth,
    LinkerMemoryRegion,
    LogicLevel,
    PullStrategy,
    ToolCommandEvidence,
    TruthDirection,
)


class ConsistencyReadPort(Protocol):
    def read(self, relative_path: str) -> bytes: ...


class RootedConsistencyReader:
    """Read-only allow-listed project reader used by the production adapter."""

    def __init__(self, root: Path, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        resolved = Path(root).resolve(strict=True)
        if not resolved.is_dir() or resolved.is_symlink():
            raise ValueError("consistency project root is invalid")
        self._root = resolved
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        return self._root

    def read(self, relative_path: str) -> bytes:
        normalized = relative_path.replace("\\", "/")
        raw_candidate = self._root / Path(normalized)
        if raw_candidate.is_symlink():
            raise FileNotFoundError("consistency artifact is unavailable")
        candidate = raw_candidate.resolve(strict=True)
        if not candidate.is_relative_to(self._root) or not candidate.is_file():
            raise FileNotFoundError("consistency artifact is unavailable")
        size = candidate.stat().st_size
        if size <= 0 or size > self._max_bytes:
            raise ValueError("consistency artifact size is invalid")
        return candidate.read_bytes()


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _stable_fp(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(encoded)


def _artifact(
    project_id: str,
    artifact_id: str,
    artifact_type: PCBArtifactType,
    source_format: PCBSourceFormat,
    relative_path: str,
    content: bytes,
) -> PCBArtifactReference:
    return PCBArtifactReference.create(
        project_id=project_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        source_format=source_format,
        source_reference=f"artifact://{project_id}/{artifact_id}",
        filename=Path(relative_path).name,
        byte_size=len(content),
        content_fingerprint=content_fingerprint(content),
        captured_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


def _parse_size(value: str) -> int:
    match = re.fullmatch(r"(0x[0-9a-fA-F]+|[0-9]+)([KMG]?)", value.strip())
    if match is None:
        raise ValueError("linker memory size is invalid")
    number = int(match.group(1), 0)
    multiplier = {"": 1, "K": 1024, "M": 1024 * 1024, "G": 1024 * 1024 * 1024}[
        match.group(2)
    ]
    return number * multiplier


def parse_linker_memory_regions(content: bytes) -> tuple[LinkerMemoryRegion, ...]:
    text = content.decode("utf-8")
    regions: list[LinkerMemoryRegion] = []
    for name in ("FLASH", "RAM"):
        match = re.search(
            rf"\b{name}\s*\([^)]*\)\s*:\s*ORIGIN\s*=\s*(0x[0-9a-fA-F]+)\s*,\s*LENGTH\s*=\s*([0-9]+(?:[KMG])?)",
            text,
            re.IGNORECASE,
        )
        if match is None:
            continue
        regions.append(
            LinkerMemoryRegion.create(
                name=name,
                origin=int(match.group(1), 16),
                length=_parse_size(match.group(2)),
            )
        )
    return tuple(regions)


class ELFInspectionAdapter:
    """Inspect a real ELF through the existing SafeProcessRunner boundary."""

    _TOOLS: ClassVar[dict[str, str]] = {
        "readelf": "arm-none-eabi-readelf.exe",
        "objdump": "arm-none-eabi-objdump.exe",
        "size": "arm-none-eabi-size.exe",
    }

    def __init__(
        self,
        toolchain_bin: Path,
        *,
        runner: object | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._toolchain_bin = Path(toolchain_bin).resolve(strict=True)
        if not self._toolchain_bin.is_dir():
            raise ValueError("ELF toolchain directory is invalid")
        self._runner = runner or SafeProcessRunner()
        self._timeout_seconds = timeout_seconds

    def inspect(
        self, elf_path: Path, *, binary_fingerprint: str | None = None
    ) -> ELFInspection:
        elf = Path(elf_path).resolve(strict=True)
        if not elf.is_file() or elf.suffix.casefold() != ".elf":
            raise ValueError("ELF path is invalid")
        outputs: dict[str, str] = {}
        commands: list[ToolCommandEvidence] = []
        for key, executable_name, arguments in (
            ("header", "readelf", ("-hW",)),
            ("program", "readelf", ("-lW",)),
            ("sections", "readelf", ("-SW",)),
            ("symbols", "readelf", ("-sW",)),
            ("objdump", "objdump", ("-h",)),
            ("vector", "objdump", ("-s", "-j", ".isr_vector")),
            ("size", "size", ()),
        ):
            executable = self._toolchain_bin / self._TOOLS[executable_name]
            if not executable.is_file():
                raise FileNotFoundError(f"ELF tool is unavailable: {executable_name}")
            argv = (str(executable), *arguments, str(elf))
            result = run_trusted_tool(
                self._runner,
                argv,
                cwd=elf.parent,
                timeout_seconds=self._timeout_seconds,
            )
            code, stdout, stderr = result
            clean_stdout = stdout.decode("utf-8", errors="replace").replace(
                str(elf), "<ELF>"
            )
            clean_stderr = stderr.decode("utf-8", errors="replace").replace(
                str(elf), "<ELF>"
            )
            if code != 0 and key != "vector":
                raise RuntimeError(f"ELF inspection command failed: {key}")
            outputs[key] = clean_stdout
            commands.append(
                ToolCommandEvidence.create(
                    tool=executable_name,
                    arguments=arguments,
                    exit_code=code,
                    stdout=clean_stdout,
                    stderr=clean_stderr,
                )
            )
        header = outputs["header"]
        machine = self._line_value(header, "Machine:")
        elf_class = self._line_value(header, "Class:")
        flags = self._line_value(header, "Flags:")
        entry = int(self._line_value(header, "Entry point address"), 16)
        sections = self._sections(outputs["sections"])
        flash_end, ram_end = self._load_ends(outputs["program"])
        text_bytes, data_bytes, bss_bytes = self._size_values(outputs["size"])
        vector_address, initial_stack = self._vector_values(outputs.get("vector", ""))
        reset_handler = self._symbol_address(outputs.get("symbols", ""), "Reset_Handler")
        return ELFInspection.create(
            artifact_fingerprint=_sha256(elf.read_bytes()),
            binary_fingerprint=binary_fingerprint,
            elf_class=elf_class,
            machine=machine,
            flags=flags,
            entry_point=entry,
            sections=sections,
            flash_load_end=flash_end,
            ram_load_end=ram_end,
            text_bytes=text_bytes,
            data_bytes=data_bytes,
            bss_bytes=bss_bytes,
            vector_table_present=vector_address is not None and initial_stack is not None,
            vector_table_address=vector_address,
            vector_table_initial_stack=initial_stack,
            reset_handler_present=reset_handler is not None,
            reset_handler_address=reset_handler,
            commands=tuple(commands),
        )

    @staticmethod
    def _line_value(text: str, prefix: str) -> str:
        for line in text.splitlines():
            if line.strip().startswith(prefix):
                return line.split(":", 1)[1].strip()
        raise ValueError(f"ELF output field is missing: {prefix}")

    @staticmethod
    def _sections(output: str) -> tuple[ELFSectionObservation, ...]:
        values: list[ELFSectionObservation] = []
        for line in output.splitlines():
            match = re.match(
                r"^\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)",
                line,
            )
            if match is None:
                continue
            address = int(match.group(2), 16)
            values.append(
                ELFSectionObservation.create(
                    name=match.group(1),
                    address=address,
                    load_address=address,
                    size=int(match.group(4), 16),
                )
            )
        if not values:
            raise ValueError("ELF sections are unavailable")
        return tuple(values)

    @staticmethod
    def _load_ends(output: str) -> tuple[int, int]:
        flash_end = 0
        ram_end = 0
        for line in output.splitlines():
            match = re.match(
                r"^\s*LOAD\s+0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)",
                line,
            )
            if match is None:
                continue
            virtual = int(match.group(1), 16)
            physical = int(match.group(2), 16)
            file_size = int(match.group(3), 16)
            memory_size = int(match.group(4), 16)
            if virtual >= 0x20000000:
                ram_end = max(ram_end, virtual + memory_size)
            # The initialized-data load address is physical FLASH even when
            # its virtual address is in RAM, so every LOAD contributes its
            # physical file span to the FLASH bound.
            if physical < 0x20000000:
                flash_end = max(flash_end, physical + file_size)
        if not flash_end or not ram_end:
            raise ValueError("ELF load spans are unavailable")
        return flash_end, ram_end

    @staticmethod
    def _size_values(output: str) -> tuple[int, int, int]:
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 4 and all(part.isdigit() for part in parts[:3]):
                return int(parts[0]), int(parts[1]), int(parts[2])
        raise ValueError("ELF size output is unavailable")

    @staticmethod
    def _vector_values(output: str) -> tuple[int | None, int | None]:
        """Read the first two little-endian words from a real .isr_vector dump."""

        words: list[int] = []
        address: int | None = None
        in_section = False
        for line in output.splitlines():
            if "Contents of section .isr_vector:" in line:
                in_section = True
                continue
            if not in_section:
                continue
            match = re.match(r"^\s*([0-9a-fA-F]+)\s+((?:[0-9a-fA-F]{8}\s+)+)", line)
            if match is None:
                continue
            if address is None:
                address = int(match.group(1), 16)
            values = match.group(2).split()
            for value in values:
                raw = bytes.fromhex(value)
                words.append(int.from_bytes(raw, byteorder="little"))
                if len(words) == 2:
                    return address, words[0] if words[0] else None
        return address, None

    @staticmethod
    def _symbol_address(output: str, symbol: str) -> int | None:
        for line in output.splitlines():
            if not re.search(rf"\b{re.escape(symbol)}$", line.strip()):
                continue
            match = re.match(r"^\s*\d+:\s+([0-9a-fA-F]+)\s+", line)
            if match is not None:
                return int(match.group(1), 16)
        return None


class FirmwareSourceFactExtractor:
    """Use the existing C parser's AST shape to extract GPIO facts."""

    def __init__(self) -> None:
        self._language = Language(tree_sitter_c.language())

    @staticmethod
    def _walk(node):
        yield node
        for child in node.children:
            yield from FirmwareSourceFactExtractor._walk(child)

    def extract(
        self,
        project_id: str,
        files: Mapping[str, str],
        *,
        target: HardwareFirmwareTruth,
        source_fingerprints: tuple[str, ...],
        specification_pin_observations: tuple[FirmwarePinObservation, ...] = (),
    ) -> FirmwareFacts:
        observations: dict[str, FirmwarePinObservation] = {}
        output_levels: dict[str, set[LogicLevel]] = {}
        issues: list[str] = []
        for path, content in sorted(files.items()):
            parser = Parser(self._language)
            tree = parser.parse(content.encode("utf-8"))
            if any(node.type == "ERROR" for node in self._walk(tree.root_node)):
                issues.append(f"parse issue at {path}")
                continue
            macros: dict[str, int] = {}
            for node in self._walk(tree.root_node):
                if node.type != "preproc_def":
                    continue
                identifier = next(
                    (child for child in node.children if child.type == "identifier"),
                    None,
                )
                argument = next(
                    (child for child in node.children if child.type == "preproc_arg"),
                    None,
                )
                if identifier is None or argument is None:
                    continue
                match = re.search(
                    r"<<\s*(\d+)", content[argument.start_byte : argument.end_byte]
                )
                if match:
                    macros[content[identifier.start_byte : identifier.end_byte]] = int(
                        match.group(1)
                    )
            for node in self._walk(tree.root_node):
                if node.type != "assignment_expression":
                    continue
                source = content[node.start_byte : node.end_byte]
                field = next(
                    (
                        child
                        for child in node.children
                        if child.type == "field_expression"
                    ),
                    None,
                )
                if field is None:
                    continue
                field_text = content[field.start_byte : field.end_byte]
                register = re.fullmatch(r"GPIO([A-Z])->(BSRR|BRR|CRL|CRH)", field_text)
                if register is None:
                    continue
                port, register_name = register.groups()
                if register_name in {"BSRR", "BRR"}:
                    macro = next(
                        (
                            name
                            for name in macros
                            if re.search(rf"\b{re.escape(name)}\b", source)
                        ),
                        None,
                    )
                    if macro is None:
                        continue
                    signal = f"P{port}{macros[macro]}"
                    output_levels.setdefault(signal, set()).add(
                        LogicLevel.HIGH if register_name == "BSRR" else LogicLevel.LOW
                    )
                    observations[signal] = FirmwarePinObservation.create(
                        signal=signal,
                        gpio_port=f"GPIO{port}",
                        gpio_number=macros[macro],
                        direction=TruthDirection.OUTPUT,
                        active_level=LogicLevel.HIGH
                        if LogicLevel.HIGH in output_levels[signal]
                        else LogicLevel.LOW,
                        pull=PullStrategy.UNKNOWN,
                        source_file=path,
                        source_line=node.start_point[0] + 1,
                        evidence=f"AST assignment {field_text} uses {macro}",
                    )
            for node in self._walk(tree.root_node):
                if node.type != "if_statement":
                    continue
                source = content[node.start_byte : node.end_byte]
                match = re.search(
                    r"GPIO([A-Z])->IDR\s*&\s*([A-Za-z_]\w*)\s*\)\s*==\s*0u?",
                    source,
                )
                if match is None or match.group(2) not in macros:
                    continue
                port = match.group(1)
                number = macros[match.group(2)]
                signal = f"P{port}{number}"
                observations[signal] = FirmwarePinObservation.create(
                    signal=signal,
                    gpio_port=f"GPIO{port}",
                    gpio_number=number,
                    direction=TruthDirection.INPUT,
                    active_level=LogicLevel.LOW,
                    pull=PullStrategy.UNKNOWN,
                    source_file=path,
                    source_line=node.start_point[0] + 1,
                    evidence=f"AST IDR comparison {match.group(2)} == 0",
                )
        for signal, levels in output_levels.items():
            previous = observations[signal]
            observations[signal] = FirmwarePinObservation.create(
                signal=previous.signal,
                gpio_port=previous.gpio_port,
                gpio_number=previous.gpio_number,
                direction=previous.direction,
                active_level=LogicLevel.HIGH
                if LogicLevel.HIGH in levels
                else LogicLevel.LOW,
                pull=previous.pull,
                source_file=previous.source_file,
                source_line=previous.source_line,
                evidence=previous.evidence
                + "; AST output levels="
                + ",".join(sorted(level.value for level in levels)),
            )
        return FirmwareFacts.create(
            project_id=project_id,
            conversation_id=None,
            target_platform=target.target_platform,
            mcu_family=target.mcu_family,
            mcu_exact_part=target.mcu_exact_part,
            architecture=target.architecture,
            firmware_framework=target.firmware_framework,
            build_system=target.build_system,
            toolchain_family=target.toolchain_family,
            design_truth_fingerprint=target.design_truth_fingerprint,
            source_fingerprints=source_fingerprints,
            source_files=tuple(sorted(files)),
            parser_identity="tree-sitter-c:gpio-facts:v1",
            pin_observations=tuple(
                sorted(observations.values(), key=lambda item: item.signal)
            ),
            specification_pin_observations=specification_pin_observations,
            parse_issues=tuple(sorted(issues)),
        )


class CanonicalProjectConsistencyAdapter:
    """Bind the real canonical project into the deterministic service input."""

    _SOURCE_PATHS = (
        "CMakeLists.txt",
        "cmake/arm-none-eabi.cmake",
        "linker/STM32F103C8Tx_FLASH.ld",
        "generated/main.c",
        "generated/minimal_mcu_control_core.c",
        "generated/minimal_mcu_control_core.h",
        "vendor/st/STM32F1xx/Source/Templates/gcc/startup_stm32f103xb.s",
        "vendor/st/STM32F1xx/Source/Templates/system_stm32f1xx.c",
    )

    def __init__(
        self,
        project_root: Path,
        *,
        project_id: str,
        toolchain_bin: Path,
        conversation_id: str | None = None,
        firmware_specification: FirmwareSpecification | None = None,
        simulation_specification: SimulationSpecification | None = None,
        verification_specification: VerificationSpecification | None = None,
        implementation_specification: ImplementationSpecification | None = None,
        build_configuration: FirmwareBuildConfiguration | None = None,
        design_truth_id: str | None = None,
        design_truth_fingerprint: str | None = None,
        elf_inspector: ELFInspectionAdapter | None = None,
    ) -> None:
        self._reader = RootedConsistencyReader(project_root)
        self._project_id = project_id
        self._conversation_id = conversation_id or "canonical-golden-minimal-mcu"
        self._firmware_specification = firmware_specification
        self._simulation_specification = simulation_specification
        self._verification_specification = verification_specification
        self._implementation_specification = implementation_specification
        self._build_configuration = build_configuration
        self._design_truth_id = design_truth_id
        self._design_truth_fingerprint = design_truth_fingerprint
        self._toolchain_bin = Path(toolchain_bin).resolve(strict=True)
        if not self._toolchain_bin.is_dir() or self._toolchain_bin.is_symlink():
            raise ValueError("consistency toolchain directory is invalid")
        self._elf_inspector = elf_inspector or ELFInspectionAdapter(self._toolchain_bin)

    def collect(self) -> ConsistencyInput:
        facts_bytes = self._reader.read("docs/canonical-golden-facts.json")
        facts = json.loads(facts_bytes.decode("utf-8"))
        schematic_bytes = self._reader.read("hardware/main.kicad_sch")
        pcb_bytes = self._reader.read("hardware/main.kicad_pcb")
        bom_bytes = self._reader.read("hardware/Minimal_MCU_Control_Core_BOM.csv")
        schematic_ref = _artifact(
            self._project_id,
            "schematic",
            PCBArtifactType.SCHEMATIC,
            PCBSourceFormat.KICAD_SCH,
            "hardware/main.kicad_sch",
            schematic_bytes,
        )
        pcb_ref = _artifact(
            self._project_id,
            "pcb",
            PCBArtifactType.BOARD,
            PCBSourceFormat.KICAD_PCB,
            "hardware/main.kicad_pcb",
            pcb_bytes,
        )
        bom_ref = _artifact(
            self._project_id,
            "bom",
            PCBArtifactType.BOM,
            PCBSourceFormat.CSV_BOM,
            "hardware/Minimal_MCU_Control_Core_BOM.csv",
            bom_bytes,
        )
        schematic = KiCadSchematicParserAdapter({"schematic": schematic_bytes}).parse(
            schematic_ref
        )
        board_attachment = UserAttachment(
            id="pcb",
            filename="main.kicad_pcb",
            media_type=AttachmentType.EDA,
            content_type="application/x-kicad-pcb",
            size_bytes=len(pcb_bytes),
            metadata={"category": "eda", "format": "kicad_pcb"},
        )
        board = KiCadBoardParserAdapter(
            KiCadPCBParser(
                RootedPCBSourceResolver(
                    self._reader.root, {"pcb": "hardware/main.kicad_pcb"}
                )
            ),
            {"pcb": board_attachment},
        ).parse(pcb_ref)
        bom = CSVPCBParserAdapter({"bom": bom_bytes}).parse(bom_ref)
        target = self._truth(facts, schematic_ref, pcb_ref, bom_ref)
        source_contents: dict[str, str] = {}
        source_lineage: list[ArtifactLineage] = []
        for relative_path in self._SOURCE_PATHS:
            content = self._reader.read(relative_path)
            source_lineage.append(
                ArtifactLineage.create(
                    artifact_id="source-"
                    + hashlib.sha256(relative_path.encode()).hexdigest()[:32],
                    artifact_type="SOURCE"
                    if not relative_path.startswith("linker/")
                    else "LINKER",
                    relative_path=relative_path,
                    content_fingerprint=_sha256(content),
                    size_bytes=len(content),
                )
            )
            if relative_path.endswith((".c", ".h")) and relative_path.startswith(
                "generated/"
            ):
                source_contents[relative_path] = content.decode("utf-8")
        linker_fingerprint = next(
            item.content_fingerprint
            for item in source_lineage
            if item.relative_path == "linker/STM32F103C8Tx_FLASH.ld"
        )
        toolchain_version = self._toolchain_version()
        if self._firmware_specification is None:
            (
                firmware_spec,
                simulation_spec,
                verification_spec,
                implementation_spec,
                build_configuration,
            ) = self._canonical_contracts(
                target,
                source_lineage=tuple(source_lineage),
                toolchain_version=toolchain_version,
                linker_fingerprint=linker_fingerprint,
            )
        else:
            firmware_spec = self._firmware_specification
            simulation_spec = self._simulation_specification
            verification_spec = self._verification_specification
            implementation_spec = self._implementation_specification
            build_configuration = self._build_configuration
            if implementation_spec is None or build_configuration is None:
                raise ValueError("catalog firmware contract overrides are incomplete")
        specification_pin_observations = tuple(
            FirmwarePinObservation.create(
                signal=item.signal,
                gpio_port=item.gpio_port or "GPIO_UNKNOWN",
                gpio_number=item.gpio_number or 0,
                direction=item.direction,
                active_level=item.active_level,
                pull=item.pull,
                source_file=f"FirmwareSpecification:{binding.binding_id}",
                source_line=1,
                evidence=f"explicit canonical FirmwareSpecification binding {binding.binding_id}",
            )
            for binding in firmware_spec.bindings
            for item in target.pins
            if item.signal in {"PA8", "PB0"}
            and item.signal in binding.signal_refs
        )
        firmware = FirmwareSourceFactExtractor().extract(
            self._project_id,
            source_contents,
            target=target,
            source_fingerprints=tuple(
                item.content_fingerprint for item in source_lineage
            ),
            specification_pin_observations=specification_pin_observations,
        )
        firmware = FirmwareFacts.create(
            **{
                **firmware.model_dump(mode="python"),
                "conversation_id": firmware_spec.conversation_id,
                "firmware_specification_id": firmware_spec.firmware_spec_id,
                "firmware_specification_fingerprint": firmware_spec.fingerprint,
                "implementation_id": implementation_spec.implementation_id,
                "implementation_fingerprint": implementation_spec.fingerprint,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        elf_path = self._reader.root / "build/Minimal_MCU_Control_Core.elf"
        elf_content = self._reader.read("build/Minimal_MCU_Control_Core.elf")
        bin_content = self._reader.read("build/Minimal_MCU_Control_Core.bin")
        map_content = self._reader.read("build/Minimal_MCU_Control_Core.map")
        elf_lineage = ArtifactLineage.create(
            artifact_id="artifact-elf",
            artifact_type="ELF",
            relative_path="build/Minimal_MCU_Control_Core.elf",
            content_fingerprint=_sha256(elf_content),
            size_bytes=len(elf_content),
        )
        bin_lineage = ArtifactLineage.create(
            artifact_id="artifact-bin",
            artifact_type="BIN",
            relative_path="build/Minimal_MCU_Control_Core.bin",
            content_fingerprint=_sha256(bin_content),
            size_bytes=len(bin_content),
        )
        map_lineage = ArtifactLineage.create(
            artifact_id="artifact-map",
            artifact_type="MAP",
            relative_path="build/Minimal_MCU_Control_Core.map",
            content_fingerprint=_sha256(map_content),
            size_bytes=len(map_content),
        )
        inspection = self._elf_inspector.inspect(
            elf_path, binary_fingerprint=bin_lineage.content_fingerprint
        )
        build_configuration_fingerprint = canonical_fingerprint(build_configuration)
        build_request_id, build_operation_id, build_receipt_id = self._build_state_identity()
        build = BuildSnapshot.create(
            build_result_status="BUILD_PASS",
            target_kind=target.mcu_exact_part,
            build_system=target.build_system,
            toolchain_family=target.toolchain_family,
            toolchain_version=toolchain_version,
            build_configuration_id=implementation_spec.build_configuration_id or "build-config-canonical-stm32f103c8t6",
            build_configuration_fingerprint=build_configuration_fingerprint,
            conversation_id=firmware_spec.conversation_id,
            firmware_specification_id=firmware_spec.firmware_spec_id,
            implementation_id=implementation_spec.implementation_id,
            build_request_id=build_request_id,
            build_operation_id=build_operation_id,
            build_receipt_id=build_receipt_id,
            linker_script_fingerprint=linker_fingerprint,
            build_configuration=build_configuration,
            firmware_spec_fingerprint=firmware_spec.fingerprint,
            implementation_fingerprint=implementation_spec.fingerprint,
            design_truth_fingerprint=target.design_truth_fingerprint,
            source_artifacts=tuple(source_lineage),
            elf=elf_lineage,
            binary=bin_lineage,
            map_file=map_lineage,
            linker_regions=parse_linker_memory_regions(
                self._reader.read("linker/STM32F103C8Tx_FLASH.ld")
            ),
            tool_observed=True,
        )
        hardware = HardwareArtifactSnapshot.create(
            schematic=ArtifactLineage.create(
                artifact_id="artifact-schematic",
                artifact_type="SCHEMATIC",
                relative_path="hardware/main.kicad_sch",
                content_fingerprint=schematic_ref.content_fingerprint,
                size_bytes=len(schematic_bytes),
            ),
            pcb=ArtifactLineage.create(
                artifact_id="artifact-pcb",
                artifact_type="PCB",
                relative_path="hardware/main.kicad_pcb",
                content_fingerprint=pcb_ref.content_fingerprint,
                size_bytes=len(pcb_bytes),
            ),
            bom=ArtifactLineage.create(
                artifact_id="artifact-bom",
                artifact_type="BOM",
                relative_path="hardware/Minimal_MCU_Control_Core_BOM.csv",
                content_fingerprint=bom_ref.content_fingerprint,
                size_bytes=len(bom_bytes),
            ),
            schematic_components=tuple(
                sorted(
                    (item.reference, item.value or "") for item in schematic.components
                )
            ),
            board_connections=tuple(
                BoardConnectionObservation.create(
                    component_reference=item.component_reference,
                    pin_number=item.pin_number,
                    net_name=item.net_id,
                )
                for item in board.connections
            ),
            bom_items=tuple(
                BOMObservation.create(
                    reference=item.references[0],
                    value=item.value,
                    symbol=item.symbol or "",
                    description=item.description or "",
                )
                for item in bom.items
            ),
        )
        request_fingerprint = _stable_fp(
            {
                "project_id": self._project_id,
                "truth": target.fingerprint,
                "firmware": firmware.fingerprint,
                "hardware": hardware.fingerprint,
                "build": build.fingerprint,
                "elf": inspection.fingerprint,
                "firmware_specification": firmware_spec.fingerprint,
                "implementation_specification": implementation_spec.fingerprint,
                "build_configuration": build_configuration_fingerprint,
            }
        )
        return ConsistencyInput.create(
            project_id=self._project_id,
            conversation_id=firmware_spec.conversation_id,
            truth=target,
            firmware=firmware,
            hardware=hardware,
            build=build,
            elf_inspection=inspection,
            firmware_specification=firmware_spec,
            simulation_specification=simulation_spec,
            verification_specification=verification_spec,
            implementation_specification=implementation_spec,
            build_configuration=build_configuration,
            request_fingerprint=request_fingerprint,
        )

    def _toolchain_version(self) -> str:
        compiler = self._toolchain_bin / "arm-none-eabi-gcc.exe"
        if not compiler.is_file():
            compiler = self._toolchain_bin / "arm-none-eabi-gcc"
        return probe_trusted_tool_version(compiler)

    def _build_state_identity(self) -> tuple[str | None, str | None, str | None]:
        state_root = self._reader.root / ".embedded-copilot-state/build"
        if not state_root.is_dir():
            return None, None, None
        candidates = tuple(
            item
            for item in state_root.iterdir()
            if item.is_dir() and (item / "build.log").is_file()
        )
        if not candidates:
            return None, None, None
        current = max(candidates, key=lambda item: item.stat().st_mtime_ns)
        log = (current / "build.log").read_bytes()
        digest = hashlib.sha256(log).hexdigest()[:32]
        operation_id = current.name
        return (
            "build-request-" + operation_id.removeprefix("firmware-build-"),
            operation_id,
            "build-receipt-" + digest,
        )

    def _canonical_contracts(
        self,
        target: HardwareFirmwareTruth,
        *,
        source_lineage: tuple[ArtifactLineage, ...],
        toolchain_version: str,
        linker_fingerprint: str,
    ) -> tuple[
        FirmwareSpecification,
        SimulationSpecification,
        VerificationSpecification,
        ImplementationSpecification,
        FirmwareBuildConfiguration,
    ]:
        """Create immutable projections from canonical facts, never from source guesses."""

        project_id = self._project_id
        conversation_id = self._conversation_id
        hardware_design_id = "hardware-design-canonical-stm32f103c8t6"
        workflow_id = "workflow-canonical-minimal-mcu"
        hardware_design_fingerprint = _stable_fp(
            {
                "kind": "canonical-hardware-design-projection",
                "truth": target.design_truth_fingerprint,
                "schematic": target.schematic_fingerprint,
                "pcb": target.pcb_fingerprint,
                "bom": target.bom_fingerprint,
            }
        )
        workflow_fingerprint = _stable_fp(
            {
                "kind": "canonical-firmware-workflow-projection",
                "source": tuple(item.content_fingerprint for item in source_lineage),
            }
        )
        firmware_spec_id = "firmware-spec-canonical-stm32f103c8t6"
        module_id = "module-canonical-control"
        led_binding = HardwareFirmwareBinding.create(
            binding_id="binding-led",
            hardware_interface_id="interface-led",
            firmware_module_id=module_id,
            interface_role=FirmwareInterfaceRole.OUTPUT,
            direction="OUTPUT",
            signal_refs=("PA8",),
            evidence_refs=("canonical-golden-facts",),
        )
        button_binding = HardwareFirmwareBinding.create(
            binding_id="binding-button",
            hardware_interface_id="interface-button",
            firmware_module_id=module_id,
            interface_role=FirmwareInterfaceRole.INPUT,
            direction="INPUT",
            signal_refs=("PB0",),
            evidence_refs=("canonical-golden-facts",),
        )
        module = FirmwareModule.create(
            module_id=module_id,
            firmware_spec_id=firmware_spec_id,
            name="Canonical LED and button control",
            module_type=FirmwareModuleType.APPLICATION,
            responsibility="Drive the canonical LED and sample the canonical user button.",
            hardware_bindings=(led_binding.binding_id, button_binding.binding_id),
            source_refs=("generated/minimal_mcu_control_core.c",),
        )
        firmware_spec = FirmwareSpecification.create(
            firmware_spec_id=firmware_spec_id,
            project_id=project_id,
            conversation_id=conversation_id,
            hardware_design_id=hardware_design_id,
            architecture_id="architecture-canonical-stm32f103c8t6",
            workflow_id=workflow_id,
            hardware_design_fingerprint=hardware_design_fingerprint,
            workflow_fingerprint=workflow_fingerprint,
            title="Canonical STM32F103C8T6 firmware specification",
            software_interfaces=("interface-led", "interface-button"),
            modules=(module,),
            bindings=(led_binding, button_binding),
            source_refs=("docs/canonical-golden-facts.json", "generated/minimal_mcu_control_core.c"),
            provenance=(
                PlanProvenance.create(
                    project_id=project_id,
                    source_type="HARDWARE_DESIGN_SPECIFICATION",
                    source_id=hardware_design_id,
                    source_fingerprint=hardware_design_fingerprint,
                ),
                PlanProvenance.create(
                    project_id=project_id,
                    source_type="ENGINEERING_WORKFLOW",
                    source_id=workflow_id,
                    source_fingerprint=workflow_fingerprint,
                ),
            ),
            design_revision=_firmware_revision(
                hardware_design_id,
                hardware_design_fingerprint,
                workflow_id,
                workflow_fingerprint,
            ),
            target_platform=target.target_platform,
            mcu_family=target.mcu_family,
            mcu_exact_part=target.mcu_exact_part,
            architecture=target.architecture,
            firmware_framework=target.firmware_framework.replace(" + ", "-"),
            build_system=target.build_system,
            toolchain_family=target.toolchain_family,
            design_truth_id=target.truth_id,
            design_truth_fingerprint=target.design_truth_fingerprint,
            source_layout=tuple(item.relative_path for item in source_lineage),
            peripheral_expectations=("LED-PA8", "BUTTON-PB0", "SWDIO-PA13", "SWCLK-PA14"),
        )
        simulation_id = "simulation-projection-canonical"
        verification_id = "verification-projection-canonical"
        model_id = "simulation-model-canonical-control"
        scenario_id = "simulation-scenario-canonical-button-led"
        acceptance_ref = "canonical-functional-button-led"
        model = SimulationModel.create(
            model_id=model_id,
            simulation_spec_id=simulation_id,
            name="Canonical LED and button control model",
            model_type=SimulationTargetType.FIRMWARE_MODULE,
            source_reference=module_id,
            interfaces=(led_binding.hardware_interface_id, button_binding.hardware_interface_id),
            evidence_refs=("canonical-golden-facts",),
        )
        scenario = SimulationScenario.create(
            scenario_id=scenario_id,
            simulation_spec_id=simulation_id,
            title="Canonical button to LED functional response",
            scenario_type=SimulationScenarioType.FUNCTIONAL_TEST,
            inputs=("button_released", "button_pressed", "button_released"),
            expected_outputs=("led_off", "led_on", "led_off"),
            related_models=(model_id,),
            acceptance_refs=(acceptance_ref,),
        )
        simulation_evidence = SimulationEvidenceRequirement.create(
            evidence_id="simulation-evidence-canonical-button-led",
            simulation_spec_id=simulation_id,
            scenario_id=scenario_id,
            evidence_type=SimulationEvidenceType.STATE_RESULT,
            description="Real Renode GPIO state observations for the canonical button/LED scenario.",
            acceptance_refs=(acceptance_ref,),
        )
        simulation = SimulationSpecification.create(
            simulation_spec_id=simulation_id,
            project_id=project_id,
            conversation_id=conversation_id,
            hardware_design_id=hardware_design_id,
            firmware_spec_id=firmware_spec.firmware_spec_id,
            workflow_id=workflow_id,
            hardware_design_fingerprint=hardware_design_fingerprint,
            firmware_spec_fingerprint=firmware_spec.fingerprint,
            workflow_fingerprint=workflow_fingerprint,
            title="Canonical verification lineage anchor",
            design_intent="FUNCTIONAL_VALIDATION",
            targets=(model_id,),
            models=(model,),
            scenarios=(scenario,),
            evidence_requirements=(simulation_evidence,),
            verification_refs=(verification_id,),
            source_refs=("docs/canonical-golden-facts.json", "generated/minimal_mcu_control_core.c"),
            design_revision=_simulation_revision(
                hardware_design_id,
                hardware_design_fingerprint,
                firmware_spec.firmware_spec_id,
                firmware_spec.fingerprint,
                workflow_id,
                workflow_fingerprint,
            ),
            provenance=(
                PlanProvenance.create(project_id=project_id, source_type="HARDWARE_DESIGN_SPECIFICATION", source_id=hardware_design_id, source_fingerprint=hardware_design_fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="FIRMWARE_SPECIFICATION", source_id=firmware_spec.firmware_spec_id, source_fingerprint=firmware_spec.fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="ENGINEERING_WORKFLOW", source_id=workflow_id, source_fingerprint=workflow_fingerprint),
            ),
        )
        verification_case = VerificationCase.create(
            case_id="verification-case-canonical-button-led",
            verification_id=verification_id,
            title="Canonical button to LED functional response",
            case_type=VerificationCaseType.FUNCTIONAL,
            verification_intent=VerificationIntent.FUNCTIONAL_VALIDATION,
            target=scenario_id,
            related_requirement_refs=(acceptance_ref,),
            related_design_refs=(scenario_id, model_id),
            method="REAL_RENODE_GPIO_OBSERVATION",
            expected_behavior=("button_released -> led_off", "button_pressed -> led_on", "button_released -> led_off"),
            evidence_refs=(simulation_evidence.evidence_id,),
        )
        verification_evidence = VerificationEvidenceRequirement.create(
            evidence_id="verification-evidence-canonical-button-led",
            verification_id=verification_id,
            case_id=verification_case.case_id,
            evidence_type=VerificationEvidenceType.SIMULATION_REPORT,
            description="Real Renode result and backend evidence for the canonical functional scenario.",
            source_reference=simulation_evidence.evidence_id,
            acceptance_refs=(acceptance_ref,),
        )
        verification = VerificationSpecification.create(
            verification_id=verification_id,
            project_id=project_id,
            conversation_id=conversation_id,
            hardware_design_id=hardware_design_id,
            firmware_spec_id=firmware_spec.firmware_spec_id,
            simulation_spec_id=simulation.simulation_spec_id,
            workflow_id=workflow_id,
            hardware_design_fingerprint=hardware_design_fingerprint,
            firmware_spec_fingerprint=firmware_spec.fingerprint,
            simulation_spec_fingerprint=simulation.fingerprint,
            workflow_fingerprint=workflow_fingerprint,
            design_revision=_verification_revision(
                hardware_design_id,
                hardware_design_fingerprint,
                firmware_spec.firmware_spec_id,
                firmware_spec.fingerprint,
                simulation.simulation_spec_id,
                simulation.fingerprint,
                workflow_id,
                workflow_fingerprint,
            ),
            cases=(verification_case,),
            evidence_requirements=(verification_evidence,),
            provenance=(
                PlanProvenance.create(project_id=project_id, source_type="HARDWARE_DESIGN_SPECIFICATION", source_id=hardware_design_id, source_fingerprint=hardware_design_fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="FIRMWARE_SPECIFICATION", source_id=firmware_spec.firmware_spec_id, source_fingerprint=firmware_spec.fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="SIMULATION_SPECIFICATION", source_id=simulation.simulation_spec_id, source_fingerprint=simulation.fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="ENGINEERING_WORKFLOW", source_id=workflow_id, source_fingerprint=workflow_fingerprint),
            ),
        )
        build_configuration = FirmwareBuildConfiguration(
            target_kind=target.mcu_exact_part,
            build_system=target.build_system,
            toolchain_id=target.toolchain_family,
            toolchain_version=toolchain_version,
            profile="CANONICAL",
            build_directory="build",
            expected_artifact_types=("ELF", "BIN", "MAP"),
            output_stem="Minimal_MCU_Control_Core",
            compiler_flags=("-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft"),
            linker_script="linker/STM32F103C8Tx_FLASH.ld",
            cmake_project="CMakeLists.txt",
            cmake_toolchain_file="cmake/arm-none-eabi.cmake",
            startup_source="vendor/st/STM32F1xx/Source/Templates/gcc/startup_stm32f103xb.s",
            system_source="vendor/st/STM32F1xx/Source/Templates/system_stm32f1xx.c",
        )
        implementation_id = "implementation-canonical-stm32f103c8t6"
        implementation = ImplementationSpecification.create(
            implementation_id=implementation_id,
            project_id=project_id,
            conversation_id=conversation_id,
            hardware_design_id=hardware_design_id,
            firmware_spec_id=firmware_spec.firmware_spec_id,
            simulation_spec_id=simulation.simulation_spec_id,
            verification_id=verification.verification_id,
            workflow_id=workflow_id,
            hardware_design_fingerprint=hardware_design_fingerprint,
            firmware_spec_fingerprint=firmware_spec.fingerprint,
            simulation_spec_fingerprint=simulation.fingerprint,
            verification_fingerprint=verification.fingerprint,
            workflow_fingerprint=workflow_fingerprint,
            design_revision=_implementation_revision(
                hardware_design_id,
                hardware_design_fingerprint,
                firmware_spec.firmware_spec_id,
                firmware_spec.fingerprint,
                simulation.simulation_spec_id,
                simulation.fingerprint,
                verification.verification_id,
                verification.fingerprint,
                workflow_id,
                workflow_fingerprint,
            ),
            provenance=(
                PlanProvenance.create(project_id=project_id, source_type="HARDWARE_DESIGN_SPECIFICATION", source_id=hardware_design_id, source_fingerprint=hardware_design_fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="FIRMWARE_SPECIFICATION", source_id=firmware_spec.firmware_spec_id, source_fingerprint=firmware_spec.fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="SIMULATION_SPECIFICATION", source_id=simulation.simulation_spec_id, source_fingerprint=simulation.fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="VERIFICATION_SPECIFICATION", source_id=verification.verification_id, source_fingerprint=verification.fingerprint),
                PlanProvenance.create(project_id=project_id, source_type="ENGINEERING_WORKFLOW", source_id=workflow_id, source_fingerprint=workflow_fingerprint),
            ),
            design_truth_id=target.truth_id,
            design_truth_fingerprint=target.design_truth_fingerprint,
            build_configuration_id="build-config-canonical-stm32f103c8t6",
            build_configuration_fingerprint=canonical_fingerprint(build_configuration),
        )
        return firmware_spec, simulation, verification, implementation, build_configuration

    def _truth(
        self,
        facts: dict[str, object],
        schematic: PCBArtifactReference,
        pcb: PCBArtifactReference,
        bom: PCBArtifactReference,
    ) -> HardwareFirmwareTruth:
        target = facts["target"]
        assert isinstance(target, dict)
        implemented = facts["implemented_pin_bindings"]
        assert isinstance(implemented, dict)
        pins = (
            HardwarePinTruth.create(
                signal="PA8",
                mcu_pin=str(implemented["PA8"]["pin"]),
                function="LED",
                gpio_port="GPIOA",
                gpio_number=8,
                direction=TruthDirection.OUTPUT,
                active_level=LogicLevel.HIGH,
                pull=PullStrategy.UNKNOWN,
                source_refs=(
                    "docs/canonical-golden-facts.json",
                    "generated/minimal_mcu_control_core.c",
                    "hardware/Minimal_MCU_Control_Core_BOM.csv",
                ),
            ),
            HardwarePinTruth.create(
                signal="PB0",
                mcu_pin=str(implemented["PB0"]["pin"]),
                function="USER_BUTTON",
                gpio_port="GPIOB",
                gpio_number=0,
                direction=TruthDirection.INPUT,
                active_level=LogicLevel.LOW,
                pull=PullStrategy.UNKNOWN,
                source_refs=(
                    "docs/canonical-golden-facts.json",
                    "generated/minimal_mcu_control_core.c",
                ),
            ),
            HardwarePinTruth.create(
                signal="BOOT0",
                mcu_pin=str(implemented["BOOT0"]["pin"]),
                function="BOOT_SELECT",
                pull=PullStrategy.DOWN,
                source_refs=(
                    "docs/canonical-golden-facts.json",
                    "hardware/Minimal_MCU_Control_Core_BOM.csv",
                ),
            ),
            HardwarePinTruth.create(
                signal="NRST",
                mcu_pin=str(implemented["NRST"]["pin"]),
                function="RESET",
                active_level=LogicLevel.LOW,
                pull=PullStrategy.UP,
                source_refs=(
                    "docs/canonical-golden-facts.json",
                    "hardware/Minimal_MCU_Control_Core_BOM.csv",
                ),
            ),
            HardwarePinTruth.create(
                signal="PA13-SWDIO",
                mcu_pin=str(implemented["PA13-SWDIO"]["pin"]),
                function="SWDIO",
                source_refs=("docs/canonical-golden-facts.json",),
            ),
            HardwarePinTruth.create(
                signal="PA14-SWCLK",
                mcu_pin=str(implemented["PA14-SWCLK"]["pin"]),
                function="SWCLK",
                source_refs=("docs/canonical-golden-facts.json",),
            ),
        )
        facts_fingerprint = _sha256(
            json.dumps(
                facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        return HardwareFirmwareTruth.create(
            truth_id=self._design_truth_id or "design-truth-canonical-stm32f103c8t6",
            project_id=self._project_id,
            conversation_id=self._conversation_id,
            design_truth_fingerprint=self._design_truth_fingerprint or facts_fingerprint,
            target_platform="STM32",
            mcu_family=str(target["mcu_family"]),
            mcu_exact_part=str(target["mcu_exact_part"]),
            architecture=str(target["architecture"]),
            firmware_framework=str(target["firmware_framework"]),
            build_system=str(target["build_system"]),
            toolchain_family=str(target["toolchain_family"]),
            schematic_fingerprint=schematic.content_fingerprint,
            pcb_fingerprint=pcb.content_fingerprint,
            bom_fingerprint=bom.content_fingerprint,
            pins=pins,
            source_fingerprints=(facts_fingerprint,),
            enrichment_provenance=(
                "canonical-golden-facts.v1",
                "BOM_DESCRIPTION",
                "firmware_ast_gpio_observation",
            ),
        )


__all__ = (
    "CanonicalProjectConsistencyAdapter",
    "ConsistencyReadPort",
    "ELFInspectionAdapter",
    "FirmwareSourceFactExtractor",
    "RootedConsistencyReader",
    "parse_linker_memory_regions",
)
