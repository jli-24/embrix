from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    finalize,
    fp,
    ident,
    text,
    tuple_values,
)
from embedded_copilot.engineering_copilot.contracts import (
    FirmwareSpecification,
    ImplementationSpecification,
    SimulationSpecification,
    VerificationSpecification,
)
from embedded_copilot.firmware_engineering.contracts import FirmwareBuildConfiguration


def _command_output(value: object, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 64 * 1024 or "\x00" in value:
        raise ValueError(f"{field} is invalid")
    if re.search(
        r"(?:api[_ -]?key|access[_ -]?token|password|credential|secret)\s*[:=]",
        value,
        re.IGNORECASE,
    ):
        raise ValueError(f"{field} contains sensitive data")
    if re.search(r"(?:[A-Za-z]:[\\/]|\\\\|file://)", value):
        raise ValueError(f"{field} contains a local path")
    if re.search(
        r"(?:^|[\s(])/(?:Users|home|root|tmp|var|workspace)(?:[/\s)]|$)",
        value,
        re.IGNORECASE,
    ):
        raise ValueError(f"{field} contains a local path")
    return value


def _component_pairs(value: object) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for item in tuple_values(value, "schematic_components"):
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("schematic component entry is invalid")
        pairs.append(
            (
                text(item[0], "component_reference", 128),
                text(item[1], "component_value", 1024),
            )
        )
    return tuple(pairs)


class ConsistencyCheckStatus(StrEnum):
    PASS = "PASS"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ConsistencyOverallStatus(StrEnum):
    CONSISTENT = "CONSISTENT"
    CONFLICT = "CONFLICT"
    INCOMPLETE = "INCOMPLETE"


class VerificationLevel(StrEnum):
    L3_ENGINEERING_VERIFIED = "L3_ENGINEERING_VERIFIED"
    UNVERIFIED = "UNVERIFIED"


class TruthDirection(StrEnum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    BIDIRECTIONAL = "BIDIRECTIONAL"
    UNKNOWN = "UNKNOWN"


class LogicLevel(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class PullStrategy(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class HardwarePinTruth(Fingerprinted):
    signal: str
    mcu_pin: str
    function: str
    gpio_port: str | None = None
    gpio_number: int | None = Field(default=None, ge=0, le=15)
    direction: TruthDirection = TruthDirection.UNKNOWN
    active_level: LogicLevel = LogicLevel.UNKNOWN
    pull: PullStrategy = PullStrategy.UNKNOWN
    source_refs: tuple[str, ...]

    _signal = field_validator("signal", "mcu_pin", "function", mode="before")(
        lambda value, info: text(value, info.field_name, 256)
    )
    _port = field_validator("gpio_port", mode="before")(
        lambda value: None if value is None else text(value, "gpio_port", 16)
    )
    _refs = field_validator("source_refs", mode="before")(
        lambda value: tuple(
            text(item, "source_ref", 512) for item in tuple_values(value, "source_refs")
        )
    )

    @classmethod
    def create(cls, **values: object) -> HardwarePinTruth:
        return finalize(cls, values)


class HardwareFirmwareTruth(Fingerprinted):
    """Approved DesignTruth plus deterministic source-backed semantic enrichment."""

    truth_id: str
    project_id: str
    conversation_id: str | None = None
    design_truth_fingerprint: str
    target_platform: str
    mcu_family: str
    mcu_exact_part: str
    architecture: str
    firmware_framework: str
    build_system: str
    toolchain_family: str
    schematic_fingerprint: str | None = None
    pcb_fingerprint: str | None = None
    bom_fingerprint: str | None = None
    pins: tuple[HardwarePinTruth, ...]
    source_fingerprints: tuple[str, ...]
    enrichment_provenance: tuple[str, ...]

    _ids = field_validator("truth_id", "project_id", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _conversation = field_validator("conversation_id", mode="before")(
        lambda value: None if value is None else ident(value, "conversation_id")
    )
    _fp = field_validator("design_truth_fingerprint", mode="before")(
        lambda value: fp(value, "design_truth_fingerprint")
    )
    _artifact_fps = field_validator(
        "schematic_fingerprint", "pcb_fingerprint", "bom_fingerprint", mode="before"
    )(lambda value, info: None if value is None else fp(value, info.field_name))
    _target = field_validator(
        "target_platform",
        "mcu_family",
        "mcu_exact_part",
        "architecture",
        "firmware_framework",
        "build_system",
        "toolchain_family",
        mode="before",
    )(lambda value, info: text(value, info.field_name, 256))
    _pins = field_validator("pins", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, HardwarePinTruth)
            else HardwarePinTruth.model_validate(item)
            for item in tuple_values(value, "pins")
        )
    )
    _fps = field_validator("source_fingerprints", mode="before")(
        lambda value: tuple(
            fp(item, "source_fingerprint")
            for item in tuple_values(value, "source_fingerprints")
        )
    )
    _prov = field_validator("enrichment_provenance", mode="before")(
        lambda value: tuple(
            text(item, "enrichment_provenance", 512)
            for item in tuple_values(value, "enrichment_provenance")
        )
    )

    @model_validator(mode="after")
    def _unique_pins(self) -> HardwareFirmwareTruth:
        signals = tuple(item.signal for item in self.pins)
        if len(signals) != len(set(signals)):
            raise ValueError("hardware truth signals must be unique")
        return self

    @classmethod
    def create(cls, **values: object) -> HardwareFirmwareTruth:
        return finalize(cls, values)


class FirmwarePinObservation(Fingerprinted):
    signal: str
    gpio_port: str
    gpio_number: int
    direction: TruthDirection
    active_level: LogicLevel
    pull: PullStrategy = PullStrategy.UNKNOWN
    source_file: str
    source_line: int = Field(ge=1)
    evidence: str

    _text_fields = field_validator(
        "signal", "gpio_port", "source_file", "evidence", mode="before"
    )(lambda value, info: text(value, info.field_name, 1024))

    @classmethod
    def create(cls, **values: object) -> FirmwarePinObservation:
        return finalize(cls, values)


class FirmwareFacts(Fingerprinted):
    project_id: str
    conversation_id: str | None = None
    firmware_specification_id: str | None = None
    firmware_specification_fingerprint: str | None = None
    implementation_id: str | None = None
    implementation_fingerprint: str | None = None
    target_platform: str
    mcu_family: str
    mcu_exact_part: str
    architecture: str
    firmware_framework: str
    build_system: str
    toolchain_family: str
    design_truth_fingerprint: str
    source_fingerprints: tuple[str, ...]
    source_files: tuple[str, ...]
    parser_identity: str
    pin_observations: tuple[FirmwarePinObservation, ...]
    # These bindings are projected from the canonical FirmwareSpecification;
    # source analysis must not be the only source of the LED/Button contract.
    specification_pin_observations: tuple[FirmwarePinObservation, ...] = ()
    parse_issues: tuple[str, ...] = ()

    _project = field_validator("project_id", mode="before")(
        lambda value: ident(value, "project_id")
    )
    _optional_ids = field_validator(
        "conversation_id", "firmware_specification_id", "implementation_id", mode="before"
    )(
        lambda value, info: None if value is None else ident(value, info.field_name)
    )
    _target = field_validator(
        "target_platform",
        "mcu_family",
        "mcu_exact_part",
        "architecture",
        "firmware_framework",
        "build_system",
        "toolchain_family",
        mode="before",
    )(lambda value, info: text(value, info.field_name, 256))
    _truth = field_validator("design_truth_fingerprint", mode="before")(
        lambda value: fp(value, "design_truth_fingerprint")
    )
    _optional_fps = field_validator(
        "firmware_specification_fingerprint", "implementation_fingerprint", mode="before"
    )(
        lambda value, info: None if value is None else fp(value, info.field_name)
    )
    _fps = field_validator("source_fingerprints", mode="before")(
        lambda value: tuple(
            fp(item, "source_fingerprint")
            for item in tuple_values(value, "source_fingerprints")
        )
    )
    _files = field_validator("source_files", mode="before")(
        lambda value: tuple(
            text(item, "source_file", 512)
            for item in tuple_values(value, "source_files")
        )
    )
    _parser = field_validator("parser_identity", mode="before")(
        lambda value: ident(value, "parser_identity")
    )
    _observations = field_validator("pin_observations", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, FirmwarePinObservation)
            else FirmwarePinObservation.model_validate(item)
            for item in tuple_values(value, "pin_observations")
        )
    )
    _specification_observations = field_validator(
        "specification_pin_observations", mode="before"
    )(
        lambda value: tuple(
            item
            if isinstance(item, FirmwarePinObservation)
            else FirmwarePinObservation.model_validate(item)
            for item in tuple_values(value, "specification_pin_observations")
        )
    )
    _issues = field_validator("parse_issues", mode="before")(
        lambda value: tuple(
            text(item, "parse_issue", 512)
            for item in tuple_values(value, "parse_issues")
        )
    )

    @classmethod
    def create(cls, **values: object) -> FirmwareFacts:
        return finalize(cls, values)


class ArtifactLineage(Fingerprinted):
    artifact_id: str
    artifact_type: Literal[
        "SCHEMATIC", "PCB", "BOM", "ELF", "BIN", "MAP", "SOURCE", "LINKER"
    ]
    relative_path: str
    content_fingerprint: str
    size_bytes: int = Field(ge=1, le=512 * 1024 * 1024)

    _id = field_validator("artifact_id", mode="before")(
        lambda value: ident(value, "artifact_id")
    )
    _path = field_validator("relative_path", mode="before")(
        lambda value: text(value, "relative_path", 1024)
    )
    _fp = field_validator("content_fingerprint", mode="before")(
        lambda value: fp(value, "content_fingerprint")
    )

    @classmethod
    def create(cls, **values: object) -> ArtifactLineage:
        return finalize(cls, values)


class BoardConnectionObservation(Fingerprinted):
    component_reference: str
    pin_number: str
    net_name: str

    _fields = field_validator(
        "component_reference", "pin_number", "net_name", mode="before"
    )(lambda value, info: text(value, info.field_name, 128))

    @classmethod
    def create(cls, **values: object) -> BoardConnectionObservation:
        return finalize(cls, values)


class BOMObservation(Fingerprinted):
    reference: str
    value: str
    symbol: str
    description: str

    _fields = field_validator(
        "reference", "value", "symbol", "description", mode="before"
    )(lambda value, info: text(value, info.field_name, 1024))

    @classmethod
    def create(cls, **values: object) -> BOMObservation:
        return finalize(cls, values)


class LinkerMemoryRegion(Fingerprinted):
    name: Literal["FLASH", "RAM"]
    origin: int = Field(ge=0)
    length: int = Field(gt=0)

    @classmethod
    def create(cls, **values: object) -> LinkerMemoryRegion:
        return finalize(cls, values)


class ELFSectionObservation(Fingerprinted):
    name: str
    address: int = Field(ge=0)
    load_address: int = Field(ge=0)
    size: int = Field(ge=0)

    _name = field_validator("name", mode="before")(
        lambda value: text(value, "section_name", 128)
    )

    @classmethod
    def create(cls, **values: object) -> ELFSectionObservation:
        return finalize(cls, values)


class ToolCommandEvidence(Fingerprinted):
    tool: str
    arguments: tuple[str, ...]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    tool_observed: bool = True

    _tool = field_validator("tool", mode="before")(lambda value: ident(value, "tool"))
    _args = field_validator("arguments", mode="before")(
        lambda value: tuple(
            text(item, "argument", 256) for item in tuple_values(value, "arguments")
        )
    )
    _output = field_validator("stdout", "stderr", mode="before")(
        lambda value, info: _command_output(value, info.field_name)
    )

    @classmethod
    def create(cls, **values: object) -> ToolCommandEvidence:
        return finalize(cls, values)


class ELFInspection(Fingerprinted):
    artifact_fingerprint: str
    binary_fingerprint: str | None = None
    elf_class: str
    machine: str
    flags: str
    entry_point: int
    sections: tuple[ELFSectionObservation, ...]
    flash_load_end: int
    ram_load_end: int
    text_bytes: int = Field(ge=0)
    data_bytes: int = Field(ge=0)
    bss_bytes: int = Field(ge=0)
    vector_table_present: bool = False
    vector_table_address: int | None = Field(default=None, ge=0)
    vector_table_initial_stack: int | None = Field(default=None, ge=0)
    reset_handler_present: bool = False
    reset_handler_address: int | None = Field(default=None, ge=0)
    commands: tuple[ToolCommandEvidence, ...]

    _artifact = field_validator("artifact_fingerprint", mode="before")(
        lambda value: fp(value, "artifact_fingerprint")
    )
    _binary = field_validator("binary_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "binary_fingerprint")
    )
    _text = field_validator("elf_class", "machine", "flags", mode="before")(
        lambda value, info: text(value, info.field_name, 256)
    )
    _sections = field_validator("sections", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, ELFSectionObservation)
            else ELFSectionObservation.model_validate(item)
            for item in tuple_values(value, "sections")
        )
    )
    _commands = field_validator("commands", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, ToolCommandEvidence)
            else ToolCommandEvidence.model_validate(item)
            for item in tuple_values(value, "commands")
        )
    )

    @classmethod
    def create(cls, **values: object) -> ELFInspection:
        return finalize(cls, values)


class HardwareArtifactSnapshot(Fingerprinted):
    schematic: ArtifactLineage
    pcb: ArtifactLineage
    bom: ArtifactLineage
    schematic_components: tuple[tuple[str, str], ...]
    board_connections: tuple[BoardConnectionObservation, ...]
    bom_items: tuple[BOMObservation, ...]

    _components = field_validator("schematic_components", mode="before")(
        _component_pairs
    )
    _connections = field_validator("board_connections", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, BoardConnectionObservation)
            else BoardConnectionObservation.model_validate(item)
            for item in tuple_values(value, "board_connections")
        )
    )
    _bom = field_validator("bom_items", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, BOMObservation)
            else BOMObservation.model_validate(item)
            for item in tuple_values(value, "bom_items")
        )
    )

    @classmethod
    def create(cls, **values: object) -> HardwareArtifactSnapshot:
        return finalize(cls, values)


class BuildSnapshot(Fingerprinted):
    build_result_status: Literal[
        "BUILD_PASS",
        "BUILD_FAIL",
        "BUILD_BLOCKED",
        "BUILD_TIMEOUT",
        "BUILD_UNAVAILABLE",
    ]
    target_kind: str
    build_system: str
    toolchain_family: str
    toolchain_version: str
    build_configuration_id: str
    build_configuration_fingerprint: str
    firmware_spec_fingerprint: str | None = None
    implementation_fingerprint: str | None = None
    design_truth_fingerprint: str | None = None
    source_artifacts: tuple[ArtifactLineage, ...]
    elf: ArtifactLineage
    binary: ArtifactLineage
    map_file: ArtifactLineage | None = None
    linker_regions: tuple[LinkerMemoryRegion, ...]
    tool_observed: bool
    conversation_id: str | None = None
    firmware_specification_id: str | None = None
    implementation_id: str | None = None
    build_request_id: str | None = None
    build_operation_id: str | None = None
    build_receipt_id: str | None = None
    linker_script_fingerprint: str | None = None
    build_configuration: FirmwareBuildConfiguration | None = None

    _ids = field_validator(
        "target_kind",
        "build_system",
        "toolchain_family",
        "toolchain_version",
        "build_configuration_id",
        mode="before",
    )(lambda value, info: text(value, info.field_name, 256))
    _fps = field_validator(
        "build_configuration_fingerprint",
        "firmware_spec_fingerprint",
        "implementation_fingerprint",
        "design_truth_fingerprint",
        mode="before",
    )(lambda value, info: None if value is None else fp(value, info.field_name))
    _optional_ids = field_validator(
        "conversation_id",
        "firmware_specification_id",
        "implementation_id",
        "build_request_id",
        "build_operation_id",
        "build_receipt_id",
        mode="before",
    )(
        lambda value, info: None if value is None else ident(value, info.field_name)
    )
    _linker_fp = field_validator("linker_script_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "linker_script_fingerprint")
    )
    _sources = field_validator("source_artifacts", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, ArtifactLineage)
            else ArtifactLineage.model_validate(item)
            for item in tuple_values(value, "source_artifacts")
        )
    )
    _regions = field_validator("linker_regions", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, LinkerMemoryRegion)
            else LinkerMemoryRegion.model_validate(item)
            for item in tuple_values(value, "linker_regions")
        )
    )

    @classmethod
    def create(cls, **values: object) -> BuildSnapshot:
        return finalize(cls, values)


class ConsistencyInput(Fingerprinted):
    project_id: str
    conversation_id: str | None = None
    truth: HardwareFirmwareTruth
    firmware: FirmwareFacts
    hardware: HardwareArtifactSnapshot
    build: BuildSnapshot
    elf_inspection: ELFInspection | None = None
    firmware_specification: FirmwareSpecification | None = None
    simulation_specification: SimulationSpecification | None = None
    verification_specification: VerificationSpecification | None = None
    implementation_specification: ImplementationSpecification | None = None
    build_configuration: FirmwareBuildConfiguration | None = None
    request_fingerprint: str | None = None

    _project = field_validator("project_id", mode="before")(
        lambda value: ident(value, "project_id")
    )
    _objects = field_validator(
        "truth", "firmware", "hardware", "build", "elf_inspection", mode="before"
    )(lambda value, info: None if value is None else value)
    _request = field_validator("request_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "request_fingerprint")
    )

    @model_validator(mode="after")
    def _project_bindings(self) -> ConsistencyInput:
        if (
            self.truth.project_id != self.project_id
            or self.firmware.project_id != self.project_id
        ):
            raise ValueError("consistency project identity mismatch")
        for contract in (
            self.firmware_specification,
            self.simulation_specification,
            self.verification_specification,
            self.implementation_specification,
        ):
            if contract is not None and contract.project_id != self.project_id:
                raise ValueError("consistency specification project identity mismatch")
        # Mismatched build configurations are deliberately retained as input
        # so the deterministic service can report CONFLICT instead of turning a
        # useful diagnostic into an input-validation exception.
        if self.request_fingerprint is None:
            object.__setattr__(self, "request_fingerprint", self._content_fingerprint())
        return self

    def _content_fingerprint(self) -> str:
        from embedded_copilot.engineering_copilot.common import canonical

        return canonical(self, exclude={"fingerprint", "request_fingerprint"})

    @classmethod
    def create(cls, **values: object) -> ConsistencyInput:
        return finalize(cls, values)


class ConsistencyCheck(Fingerprinted):
    check_id: str
    status: ConsistencyCheckStatus
    required: bool
    summary: str
    expected: str | None = None
    observed: str | None = None
    evidence_refs: tuple[str, ...] = ()

    _id = field_validator("check_id", mode="before")(
        lambda value: ident(value, "check_id")
    )
    _text = field_validator("summary", "expected", "observed", mode="before")(
        lambda value, info: (
            None if value is None else text(value, info.field_name, 4096)
        )
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: tuple(
            ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs")
        )
    )

    @classmethod
    def create(cls, **values: object) -> ConsistencyCheck:
        return finalize(cls, values)


class ConsistencyEvidence(Fingerprinted):
    project_id: str
    conversation_id: str | None = None
    design_truth_id: str | None = None
    firmware_specification_id: str | None = None
    implementation_specification_id: str | None = None
    build_configuration_id: str | None = None
    build_request_id: str | None = None
    build_operation_id: str | None = None
    build_receipt_id: str | None = None
    schematic_artifact_id: str | None = None
    pcb_artifact_id: str | None = None
    bom_artifact_id: str | None = None
    firmware_source_artifact_ids: tuple[str, ...] = ()
    elf_artifact_id: str | None = None
    bin_artifact_id: str | None = None
    map_artifact_id: str | None = None
    deterministic: bool
    tool_observed: bool
    design_truth_fingerprint: str
    schematic_fingerprint: str
    pcb_fingerprint: str
    bom_fingerprint: str
    firmware_fingerprints: tuple[str, ...]
    firmware_spec_fingerprint: str | None
    implementation_fingerprint: str | None
    build_configuration_fingerprint: str
    source_fingerprints: tuple[str, ...]
    elf_fingerprint: str
    bin_fingerprint: str
    map_fingerprint: str | None
    checks: tuple[ConsistencyCheck, ...]
    elf_inspection: ELFInspection | None
    lineage: tuple[ArtifactLineage, ...]
    artifact_integrity: ConsistencyCheckStatus = ConsistencyCheckStatus.UNKNOWN
    vector_table_status: ConsistencyCheckStatus = ConsistencyCheckStatus.UNKNOWN
    swd_mapping_status: ConsistencyCheckStatus = ConsistencyCheckStatus.UNKNOWN
    linker_script_fingerprint: str | None = None
    stale_inputs: tuple[str, ...] = ()

    _project = field_validator("project_id", mode="before")(
        lambda value: ident(value, "project_id")
    )
    _optional_ids = field_validator(
        "conversation_id",
        "design_truth_id",
        "firmware_specification_id",
        "implementation_specification_id",
        "build_configuration_id",
        "build_request_id",
        "build_operation_id",
        "build_receipt_id",
        "schematic_artifact_id",
        "pcb_artifact_id",
        "bom_artifact_id",
        "elf_artifact_id",
        "bin_artifact_id",
        "map_artifact_id",
        mode="before",
    )(
        lambda value, info: None if value is None else ident(value, info.field_name)
    )
    _fps = field_validator(
        "design_truth_fingerprint",
        "schematic_fingerprint",
        "pcb_fingerprint",
        "bom_fingerprint",
        "firmware_spec_fingerprint",
        "implementation_fingerprint",
        "build_configuration_fingerprint",
        "elf_fingerprint",
        "bin_fingerprint",
        "map_fingerprint",
        mode="before",
    )(lambda value, info: None if value is None else fp(value, info.field_name))
    _all_fps = field_validator(
        "firmware_fingerprints", "source_fingerprints", mode="before"
    )(
        lambda value: tuple(
            fp(item, "fingerprint") for item in tuple_values(value, "fingerprints")
        )
    )
    _artifact_ids = field_validator("firmware_source_artifact_ids", mode="before")(
        lambda value: tuple(
            ident(item, "firmware_source_artifact_id")
            for item in tuple_values(value, "firmware_source_artifact_ids")
        )
    )
    _linker_fingerprint = field_validator("linker_script_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "linker_script_fingerprint")
    )
    _stale = field_validator("stale_inputs", mode="before")(
        lambda value: tuple(text(item, "stale_input", 512) for item in tuple_values(value, "stale_inputs"))
    )
    _checks = field_validator("checks", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, ConsistencyCheck)
            else ConsistencyCheck.model_validate(item)
            for item in tuple_values(value, "checks")
        )
    )
    _lineage = field_validator("lineage", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, ArtifactLineage)
            else ArtifactLineage.model_validate(item)
            for item in tuple_values(value, "lineage")
        )
    )

    @classmethod
    def create(cls, **values: object) -> ConsistencyEvidence:
        return finalize(cls, values)


class HardwareFirmwareConsistencyResult(Fingerprinted):
    _fingerprint_exclude = frozenset({"computed_at", "created_at"})
    result_id: str
    project_id: str
    request_fingerprint: str
    overall_status: ConsistencyOverallStatus
    verification_level: VerificationLevel
    checks: tuple[ConsistencyCheck, ...]
    evidence: ConsistencyEvidence
    computed_at: datetime
    created_at: datetime | None = None
    conversation_id: str | None = None
    design_truth_id: str | None = None
    firmware_specification_id: str | None = None
    implementation_specification_id: str | None = None
    build_configuration_id: str | None = None
    schematic_artifact_id: str | None = None
    pcb_artifact_id: str | None = None
    bom_artifact_id: str | None = None
    firmware_source_artifact_ids: tuple[str, ...] = ()
    elf_artifact_id: str | None = None
    bin_artifact_id: str | None = None
    map_artifact_id: str | None = None
    build_request_id: str | None = None
    build_operation_id: str | None = None
    build_receipt_id: str | None = None
    design_truth_fingerprint: str | None = None
    schematic_fingerprint: str | None = None
    pcb_fingerprint: str | None = None
    bom_fingerprint: str | None = None
    firmware_specification_fingerprint: str | None = None
    implementation_fingerprint: str | None = None
    build_configuration_fingerprint: str | None = None
    firmware_source_fingerprints: tuple[str, ...] = ()
    elf_fingerprint: str | None = None
    bin_fingerprint: str | None = None
    linker_script_fingerprint: str | None = None
    artifact_integrity: ConsistencyCheckStatus = ConsistencyCheckStatus.UNKNOWN
    vector_table_status: ConsistencyCheckStatus = ConsistencyCheckStatus.UNKNOWN
    swd_mapping_status: ConsistencyCheckStatus = ConsistencyCheckStatus.UNKNOWN
    stale_inputs: tuple[str, ...] = ()
    l1_structural_valid: bool = False
    l2_rule_verified: bool = False
    l4_status: Literal["NOT_ACHIEVED"] = "NOT_ACHIEVED"
    l5_status: Literal["NOT_ACHIEVED"] = "NOT_ACHIEVED"
    fabrication_ready: bool = False

    _ids = field_validator("result_id", "project_id", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _request = field_validator("request_fingerprint", mode="before")(
        lambda value: fp(value, "request_fingerprint")
    )
    _optional_ids = field_validator(
        "conversation_id",
        "design_truth_id",
        "firmware_specification_id",
        "implementation_specification_id",
        "build_configuration_id",
        "schematic_artifact_id",
        "pcb_artifact_id",
        "bom_artifact_id",
        "elf_artifact_id",
        "bin_artifact_id",
        "map_artifact_id",
        "build_request_id",
        "build_operation_id",
        "build_receipt_id",
        mode="before",
    )(
        lambda value, info: None if value is None else ident(value, info.field_name)
    )
    _artifact_ids = field_validator("firmware_source_artifact_ids", mode="before")(
        lambda value: tuple(
            ident(item, "firmware_source_artifact_id")
            for item in tuple_values(value, "firmware_source_artifact_ids")
        )
    )
    _report_fps = field_validator(
        "design_truth_fingerprint",
        "schematic_fingerprint",
        "pcb_fingerprint",
        "bom_fingerprint",
        "firmware_specification_fingerprint",
        "implementation_fingerprint",
        "build_configuration_fingerprint",
        "elf_fingerprint",
        "bin_fingerprint",
        "linker_script_fingerprint",
        mode="before",
    )(
        lambda value, info: None if value is None else fp(value, info.field_name)
    )
    _report_source_fps = field_validator("firmware_source_fingerprints", mode="before")(
        lambda value: tuple(
            fp(item, "firmware_source_fingerprint")
            for item in tuple_values(value, "firmware_source_fingerprints")
        )
    )
    _report_statuses = field_validator(
        "artifact_integrity", "vector_table_status", "swd_mapping_status", mode="before"
    )(
        lambda value, info: value
        if isinstance(value, ConsistencyCheckStatus)
        else ConsistencyCheckStatus(value)
    )
    _stale = field_validator("stale_inputs", mode="before")(
        lambda value: tuple(text(item, "stale_input", 512) for item in tuple_values(value, "stale_inputs"))
    )
    _checks = field_validator("checks", mode="before")(
        lambda value: tuple(
            item
            if isinstance(item, ConsistencyCheck)
            else ConsistencyCheck.model_validate(item)
            for item in tuple_values(value, "checks")
        )
    )
    _evidence = field_validator("evidence", mode="before")(
        lambda value: (
            value
            if isinstance(value, ConsistencyEvidence)
            else ConsistencyEvidence.model_validate(value)
        )
    )

    @model_validator(mode="after")
    def _status_binding(self) -> HardwareFirmwareConsistencyResult:
        if self.project_id != self.evidence.project_id:
            raise ValueError("consistency evidence project identity mismatch")
        if self.checks != self.evidence.checks:
            raise ValueError("consistency checks and evidence checks differ")
        conflicts = any(
            item.required and item.status is ConsistencyCheckStatus.CONFLICT
            for item in self.checks
        )
        unknowns = any(
            item.required and item.status is ConsistencyCheckStatus.UNKNOWN
            for item in self.checks
        )
        if self.artifact_integrity is ConsistencyCheckStatus.CONFLICT:
            conflicts = True
        elif self.artifact_integrity is ConsistencyCheckStatus.UNKNOWN:
            unknowns = True
        expected = (
            ConsistencyOverallStatus.CONFLICT
            if conflicts
            else ConsistencyOverallStatus.INCOMPLETE
            if unknowns
            else ConsistencyOverallStatus.CONSISTENT
        )
        if self.overall_status is not expected:
            raise ValueError("consistency overall status mismatch")
        if (
            self.verification_level is VerificationLevel.L3_ENGINEERING_VERIFIED
            and self.overall_status is not ConsistencyOverallStatus.CONSISTENT
        ):
            raise ValueError("L3 verification requires a consistent result")
        if self.evidence.artifact_integrity is not self.artifact_integrity:
            raise ValueError("consistency artifact integrity differs from evidence")
        if self.vector_table_status is not self.evidence.vector_table_status:
            raise ValueError("consistency vector table status differs from evidence")
        if self.swd_mapping_status is not self.evidence.swd_mapping_status:
            raise ValueError("consistency SWD status differs from evidence")
        if self.stale_inputs != self.evidence.stale_inputs:
            raise ValueError("consistency stale inputs differ from evidence")
        if self.created_at is None:
            object.__setattr__(self, "created_at", self.computed_at)
        return self

    @classmethod
    def create(cls, **values: object) -> HardwareFirmwareConsistencyResult:
        return finalize(cls, values)


__all__ = (
    "ArtifactLineage",
    "BOMObservation",
    "BoardConnectionObservation",
    "BuildSnapshot",
    "ConsistencyCheck",
    "ConsistencyCheckStatus",
    "ConsistencyEvidence",
    "ConsistencyInput",
    "ConsistencyOverallStatus",
    "ELFInspection",
    "ELFSectionObservation",
    "FirmwareFacts",
    "FirmwarePinObservation",
    "HardwareArtifactSnapshot",
    "HardwareFirmwareConsistencyResult",
    "HardwareFirmwareTruth",
    "HardwarePinTruth",
    "LinkerMemoryRegion",
    "LogicLevel",
    "PullStrategy",
    "ToolCommandEvidence",
    "TruthDirection",
    "VerificationLevel",
)
