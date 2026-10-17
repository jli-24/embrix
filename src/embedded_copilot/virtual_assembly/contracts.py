from __future__ import annotations

import copy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    ZERO,
    Contract,
    Fingerprinted,
    canonical,
    fp,
    ident,
    text,
    tuple_values,
)


class AssemblyContract(Fingerprinted):
    """Strict, immutable boundary for derived virtual-assembly state."""

    @classmethod
    def create(cls, **values: object):
        material = copy.deepcopy(values)
        material.setdefault("fingerprint", ZERO)
        return cls.model_validate(material)


class AssemblyStatus(StrEnum):
    INCOMPLETE = "INCOMPLETE"
    READY_FOR_SIMULATION = "READY_FOR_SIMULATION"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class BindingStatus(StrEnum):
    BOUND = "BOUND"
    PARTIAL = "PARTIAL"
    UNBOUND = "UNBOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class GateStatus(StrEnum):
    PASS = "PASS"
    INCOMPLETE = "INCOMPLETE"
    CONFLICT = "CONFLICT"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class PinPadStatus(StrEnum):
    PASS = "PASS"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FootprintClass(StrEnum):
    ELECTRICAL_COMPONENT = "ELECTRICAL_COMPONENT"
    MECHANICAL = "MECHANICAL"
    TEST_POINT = "TEST_POINT"
    MOUNTING_HOLE = "MOUNTING_HOLE"
    FIDUCIAL = "FIDUCIAL"
    DNP = "DNP"
    UNKNOWN = "UNKNOWN"


class ModelType(StrEnum):
    SPICE = "SPICE"
    RENODE = "RENODE"
    WOKWI = "WOKWI"
    BEHAVIORAL = "BEHAVIORAL"
    IDEAL = "IDEAL"
    NONE = "NONE"
    UNAVAILABLE = "UNAVAILABLE"


class ModelAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"


class GapSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    NON_BLOCKING = "NON_BLOCKING"


class AssemblyEvidence(AssemblyContract):
    evidence_id: str
    source_kind: Literal[
        "PARSER", "ARTIFACT_STORE", "DETERMINISTIC_BINDING", "MODEL_CATALOG", "TOOL_OBSERVATION"
    ]
    source_id: str
    claim: str
    references: tuple[str, ...] = ()

    _ids = field_validator("evidence_id", "source_id", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _claim = field_validator("claim", mode="before")(
        lambda value: text(value, "claim", 2048)
    )
    _references = field_validator("references", mode="before")(
        lambda value: tuple(ident(item, "evidence_reference") for item in tuple_values(value, "references"))
    )


class MechanicalModelBinding(AssemblyContract):
    component_reference: str
    status: ModelAvailability
    model_reference: str | None = None
    model_id: str | None = None
    model_type: ModelType = ModelType.NONE
    version: str | None = None
    provenance: str
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    evidence_refs: tuple[str, ...] = ()

    _component = field_validator("component_reference", mode="before")(
        lambda value: ident(value, "component_reference")
    )
    _model_text = field_validator("model_reference", "model_id", "version", mode="before")(
        lambda value, info: None if value is None else text(value, info.field_name, 512)
    )
    _provenance = field_validator("provenance", mode="before")(
        lambda value: text(value, "provenance", 512)
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: tuple(ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs"))
    )


class ElectricalModelBinding(AssemblyContract):
    component_reference: str
    model_type: ModelType
    availability: ModelAvailability
    model_id: str | None = None
    version: str | None = None
    source: str
    provenance: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = ()

    _component = field_validator("component_reference", mode="before")(
        lambda value: ident(value, "component_reference")
    )
    _model_text = field_validator("model_id", "version", mode="before")(
        lambda value, info: None if value is None else text(value, info.field_name, 512)
    )
    _source = field_validator("source", "provenance", mode="before")(
        lambda value, info: text(value, info.field_name, 512)
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: tuple(ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs"))
    )


class ComponentBinding(AssemblyContract):
    reference: str
    component_id: str
    value: str
    part_number: str | None
    role: str
    symbol_library_id: str | None
    symbol_reference: str | None
    footprint_library_id: str | None
    footprint_reference: str | None
    pcb_instance_reference: str | None
    footprint_class: FootprintClass = FootprintClass.ELECTRICAL_COMPONENT
    population_status: Literal["POPULATED", "NOT_POPULATED"] = "POPULATED"
    binding_status: BindingStatus
    binding_evidence: tuple[str, ...]
    mechanical_model_status: ModelAvailability
    mechanical_model_reference: str | None = None
    electrical_model: ModelType
    simulation_capability: ModelType
    simulation_gap_ids: tuple[str, ...] = ()

    _reference = field_validator("reference", mode="before")(
        lambda value: ident(value, "reference")
    )
    _ids = field_validator("component_id", mode="before")(
        lambda value: ident(value, "component_id")
    )
    _text = field_validator("value", "role", mode="before")(
        lambda value, info: text(value, info.field_name, 512)
    )
    _optional_text = field_validator(
        "part_number", "symbol_library_id", "symbol_reference", "footprint_library_id", "footprint_reference",
        "pcb_instance_reference", "mechanical_model_reference", mode="before"
    )(
        lambda value, info: None if value is None else text(value, info.field_name, 512)
    )
    _evidence = field_validator("binding_evidence", "simulation_gap_ids", mode="before")(
        lambda value, info: tuple(ident(item, info.field_name.removesuffix("s")) for item in tuple_values(value, info.field_name))
    )


class PinPadBinding(AssemblyContract):
    component_reference: str
    logical_pin: str
    symbol_pin_number: str | None
    symbol_pin_name: str | None
    schematic_net: str | None
    footprint_pad_number: str | None
    pcb_net: str | None
    status: PinPadStatus
    reason: str
    evidence_refs: tuple[str, ...] = ()

    _component = field_validator("component_reference", mode="before")(
        lambda value: ident(value, "component_reference")
    )
    _logical = field_validator("logical_pin", mode="before")(
        lambda value: text(value, "logical_pin", 128)
    )
    _optional = field_validator(
        "symbol_pin_number", "symbol_pin_name", "schematic_net", "footprint_pad_number", "pcb_net", mode="before"
    )(
        lambda value, info: None if value is None else text(value, info.field_name, 256)
    )
    _reason = field_validator("reason", mode="before")(
        lambda value: text(value, "reason", 1024)
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: tuple(ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs"))
    )


class PowerDomainBinding(AssemblyContract):
    domain_id: str
    net_names: tuple[str, ...]
    component_references: tuple[str, ...]
    supply_pin_references: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    _id = field_validator("domain_id", mode="before")(
        lambda value: ident(value, "domain_id")
    )
    _nets = field_validator("net_names", mode="before")(
        lambda value: tuple(text(item, "net_name", 128) for item in tuple_values(value, "net_names"))
    )
    _components = field_validator("component_references", "supply_pin_references", mode="before")(
        lambda value, info: tuple(text(item, info.field_name[:-1], 128) for item in tuple_values(value, info.field_name))
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: tuple(ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs"))
    )


class VirtualAssemblyInterface(AssemblyContract):
    interface_id: str
    name: str
    interface_type: str
    logical_signals: tuple[str, ...]
    net_names: tuple[str, ...]
    description: str
    evidence_refs: tuple[str, ...] = ()

    _ids = field_validator("interface_id", mode="before")(
        lambda value: ident(value, "interface_id")
    )
    _text = field_validator("name", "interface_type", "description", mode="before")(
        lambda value, info: text(value, info.field_name, 512)
    )
    _signals = field_validator("logical_signals", "net_names", mode="before")(
        lambda value, info: tuple(text(item, info.field_name[:-1], 128) for item in tuple_values(value, info.field_name))
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: tuple(ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs"))
    )


class SimulationGap(AssemblyContract):
    gap_id: str
    component_reference: str
    required_behavior: str
    missing_model: str
    impact: str
    severity: GapSeverity
    blocking: bool
    recommended_resolution: str
    status: Literal["OPEN", "ACKNOWLEDGED"] = "OPEN"
    evidence_refs: tuple[str, ...] = ()

    _ids = field_validator("gap_id", "component_reference", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _text = field_validator(
        "required_behavior", "missing_model", "impact", "recommended_resolution", mode="before"
    )(
        lambda value, info: text(value, info.field_name, 1024)
    )
    _refs = field_validator("evidence_refs", mode="before")(
        lambda value: tuple(ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs"))
    )

    @model_validator(mode="after")
    def verify_severity(self) -> SimulationGap:
        if self.blocking != (self.severity is GapSeverity.BLOCKING):
            raise ValueError("blocking flag must match gap severity")
        return self


class AssemblyCoverage(AssemblyContract):
    total_bom_components: int = Field(ge=0)
    bound_components: int = Field(ge=0)
    unbound_components: int = Field(ge=0)
    total_pcb_footprints: int = Field(ge=0)
    bound_footprints: int = Field(ge=0)
    electrical_footprints: int = Field(ge=0)
    mechanical_footprints: int = Field(ge=0)
    dnp_footprints: int = Field(ge=0)
    unknown_footprints: int = Field(ge=0)
    component_binding_coverage: float = Field(ge=0.0, le=100.0)
    footprint_binding_coverage: float = Field(ge=0.0, le=100.0)
    total_physical_components: int = Field(ge=0)
    components_with_3d_model: int = Field(ge=0)
    missing_3d_models: int = Field(ge=0)
    mechanical_model_coverage: float = Field(ge=0.0, le=100.0)
    electrical_model_total: int = Field(ge=0)
    electrical_model_available: int = Field(ge=0)
    electrical_model_missing: int = Field(ge=0)
    electrical_model_coverage: float = Field(ge=0.0, le=100.0)
    firmware_execution_model_total_required: int = Field(ge=0)
    firmware_execution_model_available: int = Field(ge=0)
    firmware_execution_model_missing: int = Field(ge=0)
    firmware_execution_model_coverage: float = Field(ge=0.0, le=100.0)
    simulation_model_coverage: float = Field(ge=0.0, le=100.0)

    @model_validator(mode="after")
    def verify_counts(self) -> AssemblyCoverage:
        if self.bound_components + self.unbound_components > self.total_bom_components:
            raise ValueError("component coverage counts are inconsistent")
        if self.bound_footprints > self.total_pcb_footprints:
            raise ValueError("footprint coverage counts are inconsistent")
        return self


class VirtualBoardAssembly(Contract):
    assembly_id: str
    project_id: str
    conversation_id: str | None
    design_truth_id: str
    design_truth_fingerprint: str
    hardware_design_id: str | None = None
    schematic_artifact_id: str
    schematic_sha256: str
    pcb_artifact_id: str
    pcb_sha256: str
    bom_artifact_id: str
    bom_sha256: str
    firmware_specification_id: str | None = None
    firmware_specification_fingerprint: str | None = None
    build_configuration_id: str | None = None
    build_configuration_fingerprint: str | None = None
    elf_artifact_id: str | None = None
    elf_sha256: str | None = None
    bin_artifact_id: str | None = None
    bin_sha256: str | None = None
    hw_fw_consistency_result_id: str | None = None
    components: tuple[ComponentBinding, ...] = ()
    nets: tuple[PowerDomainBinding, ...] = ()
    power_domains: tuple[PowerDomainBinding, ...] = ()
    interfaces: tuple[VirtualAssemblyInterface, ...] = ()
    component_bindings: tuple[ComponentBinding, ...] = ()
    pin_pad_bindings: tuple[PinPadBinding, ...] = ()
    mechanical_model_bindings: tuple[MechanicalModelBinding, ...] = ()
    electrical_model_bindings: tuple[ElectricalModelBinding, ...] = ()
    simulation_gaps: tuple[SimulationGap, ...] = ()
    evidence: tuple[AssemblyEvidence, ...] = ()
    coverage: AssemblyCoverage | None = None
    design_truth_binding: GateStatus = GateStatus.UNKNOWN
    schematic_binding: GateStatus = GateStatus.UNKNOWN
    pcb_binding: GateStatus = GateStatus.UNKNOWN
    bom_binding: GateStatus = GateStatus.UNKNOWN
    component_binding: GateStatus = GateStatus.UNKNOWN
    footprint_binding: GateStatus = GateStatus.UNKNOWN
    pin_pad_mapping: GateStatus = GateStatus.UNKNOWN
    firmware_binding: GateStatus = GateStatus.UNKNOWN
    hw_fw_consistency_binding: GateStatus = GateStatus.UNKNOWN
    artifact_integrity: GateStatus = GateStatus.UNKNOWN
    assembly_status: AssemblyStatus = AssemblyStatus.INCOMPLETE
    stale_inputs: tuple[str, ...] = ()
    blocking_simulation_gaps: int = Field(default=0, ge=0)
    non_blocking_simulation_gaps: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    assembly_fingerprint: str = ZERO

    @classmethod
    def create(cls, **values: object):
        material = copy.deepcopy(values)
        material.setdefault("assembly_fingerprint", ZERO)
        return cls.model_validate(material)

    _ids = field_validator(
        "assembly_id", "project_id", "design_truth_id", "schematic_artifact_id", "pcb_artifact_id", "bom_artifact_id", mode="before"
    )(
        lambda value, info: ident(value, info.field_name)
    )
    _optional_ids = field_validator(
        "conversation_id", "hardware_design_id", "firmware_specification_id", "build_configuration_id", "elf_artifact_id", "bin_artifact_id", "hw_fw_consistency_result_id", mode="before"
    )(
        lambda value, info: None if value is None else ident(value, info.field_name)
    )
    _fps = field_validator(
        "design_truth_fingerprint", "schematic_sha256", "pcb_sha256", "bom_sha256", "firmware_specification_fingerprint", "build_configuration_fingerprint", "elf_sha256", "bin_sha256", mode="before"
    )(
        lambda value, info: None if value is None else fp(value, info.field_name)
    )
    _stale = field_validator("stale_inputs", mode="before")(
        lambda value: tuple(text(item, "stale_input", 512) for item in tuple_values(value, "stale_inputs"))
    )
    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _utc(cls, value: object) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("assembly timestamps must be valid") from exc
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("assembly timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("assembly_fingerprint", mode="before")
    @classmethod
    def _assembly_fp(cls, value: object) -> str:
        return fp(value, "assembly_fingerprint")

    @model_validator(mode="after")
    def verify_assembly(self) -> VirtualBoardAssembly:
        if self.components and self.component_bindings and self.components != self.component_bindings:
            raise ValueError("components and component_bindings differ")
        if self.blocking_simulation_gaps != sum(item.blocking for item in self.simulation_gaps):
            raise ValueError("blocking simulation gap count differs")
        if self.non_blocking_simulation_gaps != len(self.simulation_gaps) - self.blocking_simulation_gaps:
            raise ValueError("non-blocking simulation gap count differs")
        expected = canonical(
            self,
            exclude={"assembly_fingerprint", "created_at", "updated_at"},
        )
        if self.assembly_fingerprint == ZERO:
            object.__setattr__(self, "assembly_fingerprint", expected)
        elif self.assembly_fingerprint != expected:
            raise ValueError("assembly_fingerprint mismatch")
        return self

    @property
    def ready_for_simulation(self) -> bool:
        return self.assembly_status is AssemblyStatus.READY_FOR_SIMULATION


__all__ = (
    "AssemblyContract",
    "AssemblyCoverage",
    "AssemblyEvidence",
    "AssemblyStatus",
    "BindingStatus",
    "ComponentBinding",
    "ElectricalModelBinding",
    "FootprintClass",
    "GapSeverity",
    "GateStatus",
    "MechanicalModelBinding",
    "ModelAvailability",
    "ModelType",
    "PinPadBinding",
    "PinPadStatus",
    "PowerDomainBinding",
    "SimulationGap",
    "VirtualAssemblyInterface",
    "VirtualBoardAssembly",
)
