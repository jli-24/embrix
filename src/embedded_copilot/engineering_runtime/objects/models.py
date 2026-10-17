"""Engineering Object contracts for the Runtime MVP."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
from typing import Any, Literal

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from embedded_copilot.engineering_runtime.common import (
    ZERO_FINGERPRINT,
    RuntimeContract,
    assert_project_ref,
    canonical_fingerprint,
    normalize_text,
    validate_fingerprint,
    validate_identifier,
    validate_revision,
    validate_text_tuple,
    validate_utc_datetime,
)


class ObjectType(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    HARDWARE_ARCHITECTURE = "HARDWARE_ARCHITECTURE"
    BUDGET_LEDGER = "BUDGET_LEDGER"
    CIRCUIT_INTENT = "CIRCUIT_INTENT"
    SCHEMATIC_DESIGN = "SCHEMATIC_DESIGN"
    EVIDENCE = "EVIDENCE"


class LifecycleStatus(str, Enum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    BLOCKED_EVIDENCE = "BLOCKED_EVIDENCE"
    WAIT_INPUT = "WAIT_INPUT"


class EvidenceQualificationStatus(str, Enum):
    """Independent qualification state for evidence objects.

    Lifecycle answers whether the runtime projection is readable/current.  This
    state answers whether the referenced evidence is trustworthy for a
    validation relation or qualified context.
    """

    UNVERIFIED = "UNVERIFIED"
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


class Provenance(RuntimeContract):
    source: str
    source_ref: str
    created_by: str
    created_at: datetime
    source_fingerprint: str | None = None
    derived_from: tuple[str, ...] = ()

    _source = field_validator("source", "source_ref", "created_by")(
        lambda v: normalize_text(v, field="provenance")
    )
    _derived_from = field_validator("derived_from")(
        lambda v: validate_text_tuple(v, field="derived_from")
    )
    _created_at = field_validator("created_at")(validate_utc_datetime)
    _source_fingerprint = field_validator("source_fingerprint")(
        lambda v: (
            None if v is None else validate_fingerprint(v, field="source_fingerprint")
        )
    )

    @classmethod
    def create(
        cls,
        *,
        source: str,
        source_ref: str,
        created_by: str,
        created_at: datetime | None = None,
        source_fingerprint: str | None = None,
        derived_from: tuple[str, ...] = (),
    ) -> Provenance:
        return cls(
            source=source,
            source_ref=source_ref,
            created_by=created_by,
            created_at=created_at or datetime.now(UTC),
            source_fingerprint=source_fingerprint,
            derived_from=derived_from,
        )


class EngineeringObjectBase(RuntimeContract):
    object_id: str
    project_id: str
    object_type: ObjectType
    revision: str = "r1"
    status: LifecycleStatus = LifecycleStatus.DRAFT
    provenance: Provenance
    dependency_fingerprint: str = ZERO_FINGERPRINT
    fingerprint: str = ZERO_FINGERPRINT

    _object_id = field_validator("object_id")(
        lambda v: validate_identifier(v, field="object_id")
    )
    _project_id = field_validator("project_id")(
        lambda v: validate_identifier(v, field="project_id")
    )
    _revision = field_validator("revision")(validate_revision)
    _dependency_fingerprint = field_validator("dependency_fingerprint")(
        lambda v: validate_fingerprint(v, field="dependency_fingerprint")
    )
    _fingerprint = field_validator("fingerprint")(validate_fingerprint)

    @model_validator(mode="after")
    def validate_identity_and_fingerprint(self) -> EngineeringObjectBase:
        assert_project_ref(self.project_id, self.provenance.source_ref)
        if self.fingerprint != ZERO_FINGERPRINT:
            expected = canonical_fingerprint(self, exclude={"fingerprint", "status"})
            if self.fingerprint != expected:
                raise ValueError("fingerprint does not match the object content")
        return self

    @property
    def revision_id(self) -> str:
        return f"{self.object_id}@{self.revision}"

    @classmethod
    def create(cls, **values: Any) -> EngineeringObjectBase:
        values.setdefault("revision", "r1")
        values.setdefault("status", LifecycleStatus.DRAFT)
        values["fingerprint"] = ZERO_FINGERPRINT
        draft = cls(**values)
        return draft.model_copy(
            update={
                "fingerprint": canonical_fingerprint(
                    draft, exclude={"fingerprint", "status"}
                )
            }
        )


class RequirementObject(EngineeringObjectBase):
    object_type: Literal[ObjectType.REQUIREMENT] = ObjectType.REQUIREMENT
    description: str
    source: str
    acceptance_criteria: tuple[str, ...]
    verification_method: str

    _description = field_validator("description", "source", "verification_method")(
        lambda v: normalize_text(v, field="requirement_field")
    )
    _acceptance_criteria = field_validator("acceptance_criteria")(
        lambda v: validate_text_tuple(v, field="acceptance_criteria")
    )

    @property
    def requirement_id(self) -> str:
        return self.object_id


class HardwareArchitectureObject(EngineeringObjectBase):
    object_type: Literal[ObjectType.HARDWARE_ARCHITECTURE] = (
        ObjectType.HARDWARE_ARCHITECTURE
    )
    mcu: str
    peripherals: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()
    power_domains: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    _mcu = field_validator("mcu")(lambda v: normalize_text(v, field="mcu"))
    _tuple_fields = field_validator(
        "peripherals", "interfaces", "power_domains", "constraints"
    )(lambda v: validate_text_tuple(v, field="hardware_field"))

    @property
    def hardware_architecture_id(self) -> str:
        return self.object_id


class BudgetLedgerObject(EngineeringObjectBase):
    object_type: Literal[ObjectType.BUDGET_LEDGER] = ObjectType.BUDGET_LEDGER
    entries: tuple[str, ...] = ()

    _entries = field_validator("entries")(
        lambda v: validate_text_tuple(v, field="entries")
    )


CalculationScalar = StrictStr | StrictInt | StrictFloat | StrictBool
CalculationValues = tuple[tuple[str, CalculationScalar], ...]


def _validate_reference_tuple(values: object, *, field: str) -> tuple[str, ...]:
    return tuple(
        validate_identifier(value, field=field)
        for value in validate_text_tuple(values, field=field)
    )


def _validate_scalar(value: object, *, field: str) -> CalculationScalar:
    if type(value) not in {str, int, float, bool}:
        raise ValueError(f"{field} must be a scalar")
    if type(value) is float and not isfinite(value):
        raise ValueError(f"{field} must be finite")
    if type(value) is str:
        return normalize_text(value, field=field)
    return value  # type: ignore[return-value]


def _validate_calculation_values(value: object, *, field: str) -> CalculationValues:
    if isinstance(value, Mapping):
        raw_items = tuple(value.items())
    elif type(value) in {tuple, list}:
        raw_items = tuple(value)
    else:
        raise ValueError(f"{field} must be a mapping or sequence of name/value pairs")

    items: list[tuple[str, CalculationScalar]] = []
    for item in raw_items:
        if isinstance(item, str):
            if "=" not in item:
                raise ValueError(f"{field} string entries must use name=value form")
            name, raw_value = item.split("=", 1)
        elif type(item) in {tuple, list} and len(item) == 2:
            name, raw_value = item
        else:
            raise ValueError(f"{field} entries must be name/value pairs")
        normalized_name = normalize_text(name, field=f"{field}_name")
        normalized_value = _validate_scalar(raw_value, field=f"{field}_value")
        items.append((normalized_name, normalized_value))

    names = [name for name, _ in items]
    if len(names) != len(set(names)):
        raise ValueError(f"{field} contains duplicate names")
    return tuple(sorted(items, key=lambda item: item[0]))


class _CircuitIntentContract(RuntimeContract):
    """Private base for immutable, content-fingerprinted intent sub-contracts."""

    fingerprint: str = ZERO_FINGERPRINT

    _fingerprint = field_validator("fingerprint")(validate_fingerprint)

    @model_validator(mode="after")
    def validate_fingerprint(self) -> _CircuitIntentContract:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == ZERO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("fingerprint does not match the contract content")
        return self


class CircuitBlock(_CircuitIntentContract):
    block_id: str
    function: str
    requirement_refs: tuple[str, ...] = ()
    architecture_refs: tuple[str, ...] = ()
    topology_ref: str | None = None
    decision_refs: tuple[str, ...] = ()
    calculation_refs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    assumption_refs: tuple[str, ...] = ()
    risk_refs: tuple[str, ...] = ()
    schematic_binding_refs: tuple[str, ...] = ()
    status: str = "DRAFT"

    _block_id = field_validator("block_id")(
        lambda value: validate_identifier(value, field="block_id")
    )
    _function = field_validator("function")(
        lambda value: normalize_text(value, field="block_function")
    )
    _topology_ref = field_validator("topology_ref")(
        lambda value: None
        if value is None
        else validate_identifier(value, field="topology_ref")
    )
    _refs = field_validator(
        "requirement_refs",
        "architecture_refs",
        "decision_refs",
        "calculation_refs",
        "verification_refs",
        "assumption_refs",
        "risk_refs",
        "schematic_binding_refs",
    )(lambda value, info: _validate_reference_tuple(value, field=info.field_name))
    _status = field_validator("status")(
        lambda value: normalize_text(value, field="block_status")
    )


class CircuitDecision(_CircuitIntentContract):
    decision_id: str
    block_ref: str
    decision_type: str
    selected_option: str
    alternatives: tuple[str, ...] = ()
    rationale: str
    tradeoffs: tuple[str, ...] = ()
    calculation_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    risk_refs: tuple[str, ...] = ()
    status: str = "DRAFT"

    _ids = field_validator("decision_id", "block_ref")(
        lambda value, info: validate_identifier(value, field=info.field_name)
    )
    _text_fields = field_validator(
        "decision_type", "selected_option", "rationale", "status"
    )(lambda value, info: normalize_text(value, field=info.field_name))
    _option_lists = field_validator("alternatives", "tradeoffs")(
        lambda value, info: validate_text_tuple(value, field=info.field_name)
    )
    _refs = field_validator("calculation_refs", "evidence_refs", "risk_refs")(
        lambda value, info: _validate_reference_tuple(value, field=info.field_name)
    )


class CalculationInputSource(RuntimeContract):
    """Candidate provenance for one named calculation input, not verified evidence."""

    input_name: str
    value: CalculationScalar
    unit: str
    source_kind: Literal["CATALOG_REQUIREMENT", "HARDWARE_TRUTH", "ENGINEERING_EVIDENCE"]
    source_id: str
    source_fingerprint: str
    content_fingerprint: str
    locator: str
    condition: str
    component_instance_id: str | None = Field(default=None, exclude_if=lambda value: value is None)

    _component_id = field_validator("component_instance_id")(lambda value: None if value is None else validate_identifier(value, field="calculation_component_instance_id"))
    _name = field_validator("input_name")(
        lambda value: normalize_text(value, field="calculation_input_name")
    )
    _value = field_validator("value")(
        lambda value: _validate_scalar(value, field="calculation_input_value")
    )
    _source_id = field_validator("source_id")(
        lambda value: validate_identifier(value, field="calculation_source_id")
    )
    _source_fp = field_validator("source_fingerprint", "content_fingerprint")(
        lambda value, info: validate_fingerprint(value, field=f"calculation_{info.field_name}")
    )
    _text = field_validator("unit", "locator", "condition")(
        lambda value, info: normalize_text(value, field=f"calculation_{info.field_name}")
    )


class CalculationRecord(_CircuitIntentContract):
    calculation_id: str
    owner_ref: str
    component_instance_id: str | None = Field(default=None, exclude_if=lambda value: value is None)

    _component_id = field_validator("component_instance_id")(lambda value: None if value is None else validate_identifier(value, field="calculation_component_instance_id"))
    equation: str
    inputs: CalculationValues = ()
    input_sources: tuple[CalculationInputSource, ...] = Field(
        default=(), exclude_if=lambda values: not values,
    )
    outputs: CalculationValues = ()
    units: tuple[str, ...] = ()
    corner: str = "NOMINAL"
    margin: CalculationScalar | None = None
    acceptance_limit: CalculationScalar | None = None
    tool_reference: str | None = None
    model_reference: str | None = None
    evidence_refs: tuple[str, ...] = ()
    status: str = "PLANNED"

    _ids = field_validator("calculation_id", "owner_ref")(
        lambda value, info: validate_identifier(value, field=info.field_name)
    )
    _equation = field_validator("equation")(
        lambda value: normalize_text(value, field="equation")
    )
    _values = field_validator("inputs", "outputs", mode="before")(
        lambda value, info: _validate_calculation_values(value, field=info.field_name)
    )
    @field_validator("input_sources", mode="before")
    @classmethod
    def _input_sources(cls, value: object) -> tuple[CalculationInputSource, ...]:
        if type(value) not in {tuple, list}:
            raise ValueError("calculation input sources must be a sequence")
        return tuple(sorted(
            (CalculationInputSource.model_validate(item) for item in value),
            key=lambda item: item.input_name,
        ))
    _units = field_validator("units")(
        lambda value: validate_text_tuple(value, field="units")
    )
    _corner = field_validator("corner")(
        lambda value: normalize_text(value, field="corner")
    )
    _scalars = field_validator("margin", "acceptance_limit")(
        lambda value, info: None
        if value is None
        else _validate_scalar(value, field=info.field_name)
    )
    _references = field_validator("tool_reference", "model_reference")(
        lambda value, info: None
        if value is None
        else normalize_text(value, field=info.field_name)
    )
    _evidence_refs = field_validator("evidence_refs")(
        lambda value: _validate_reference_tuple(value, field="evidence_refs")
    )
    _status = field_validator("status")(
        lambda value: normalize_text(value, field="calculation_status")
    )

    @model_validator(mode="after")
    def _input_source_binding(self) -> CalculationRecord:
        if self.input_sources:
            inputs = dict(self.inputs)
            if (len(self.input_sources) != len(inputs)
                    or {item.input_name for item in self.input_sources} != inputs.keys()
                    or any(type(item.value) is not type(inputs[item.input_name])
                           or item.value != inputs[item.input_name]
                           or item.condition != self.corner
                           for item in self.input_sources)):
                raise ValueError("calculation input source binding is invalid")
        return self


class VerificationRequirement(_CircuitIntentContract):
    verification_id: str
    owner_ref: str
    claim: str
    verification_type: str
    method: str
    acceptance_limit: CalculationScalar | None = None
    required_evidence_refs: tuple[str, ...] = ()
    produced_evidence_refs: tuple[str, ...] = ()
    status: str = "PLANNED"
    currentness: str = "CURRENT"

    _ids = field_validator("verification_id", "owner_ref")(
        lambda value, info: validate_identifier(value, field=info.field_name)
    )
    _text_fields = field_validator("claim", "verification_type", "method")(
        lambda value, info: normalize_text(value, field=info.field_name)
    )
    _acceptance_limit = field_validator("acceptance_limit")(
        lambda value: None
        if value is None
        else _validate_scalar(value, field="acceptance_limit")
    )
    _refs = field_validator("required_evidence_refs", "produced_evidence_refs")(
        lambda value, info: _validate_reference_tuple(value, field=info.field_name)
    )
    _state = field_validator("status", "currentness")(
        lambda value, info: normalize_text(value, field=info.field_name)
    )


class CircuitIntentObject(EngineeringObjectBase):
    object_type: Literal[ObjectType.CIRCUIT_INTENT] = ObjectType.CIRCUIT_INTENT
    function: str
    topology: tuple[str, ...]
    parameters: tuple[str, ...] = ()
    calculation: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    blocks: tuple[CircuitBlock, ...] = ()
    decisions: tuple[CircuitDecision, ...] = ()
    calculations: tuple[CalculationRecord, ...] = ()
    verification_requirements: tuple[VerificationRequirement, ...] = ()
    legacy_verification_requirements: tuple[str, ...] = ()

    _function = field_validator("function")(
        lambda v: normalize_text(v, field="function")
    )
    @model_validator(mode="before")
    @classmethod
    def _legacy_verification_input(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        raw = values.get("verification_requirements", ())
        if type(raw) in {tuple, list} and all(isinstance(item, str) for item in raw):
            values = dict(values)
            values["verification_requirements"] = ()
            values["legacy_verification_requirements"] = tuple(raw)
        return values

    _tuple_fields = field_validator(
        "topology",
        "parameters",
        "calculation",
        "assumptions",
        "risks",
    )(lambda v: validate_text_tuple(v, field="circuit_field"))
    _legacy_verification_requirements = field_validator(
        "legacy_verification_requirements"
    )(lambda value: validate_text_tuple(value, field="legacy_verification_requirements"))

    @field_validator(
        "blocks",
        "decisions",
        "calculations",
        "verification_requirements",
        mode="before",
    )
    @classmethod
    def _structured_fields(cls, value: object, info) -> tuple[object, ...]:
        contract_types = {
            "blocks": CircuitBlock,
            "decisions": CircuitDecision,
            "calculations": CalculationRecord,
            "verification_requirements": VerificationRequirement,
        }
        if type(value) not in {tuple, list}:
            raise TypeError(f"{info.field_name} must be an immutable sequence")
        contract_type = contract_types[info.field_name]
        return tuple(
            item
            if isinstance(item, contract_type)
            else contract_type.model_validate(item, strict=False)
            for item in value
        )

    @property
    def circuit_intent_id(self) -> str:
        return self.object_id


def _validate_sorted_unique_identifiers(
    values: object, *, field: str
) -> tuple[str, ...]:
    """Normalize binding references while keeping their fingerprint stable."""

    if type(values) not in {tuple, list}:
        raise TypeError(f"{field} must be an immutable sequence")
    refs = tuple(validate_identifier(value, field=field) for value in values)
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} contains duplicate references")
    return tuple(sorted(refs))


class CircuitSchematicBinding(RuntimeContract):
    """Immutable, schematic-owned binding for one CircuitIntent block."""

    binding_id: str
    circuit_intent_id: str
    circuit_intent_revision: str
    circuit_intent_fingerprint: str
    circuit_block_id: str
    component_instance_ids: tuple[str, ...] = ()
    pin_binding_ids: tuple[str, ...] = ()
    net_ids: tuple[str, ...] = ()
    power_domain_ids: tuple[str, ...] = ()
    fingerprint: str = ZERO_FINGERPRINT

    _ids = field_validator(
        "binding_id", "circuit_intent_id", "circuit_block_id"
    )(lambda value, info: validate_identifier(value, field=info.field_name))
    _revision = field_validator("circuit_intent_revision")(validate_revision)
    _intent_fingerprint = field_validator("circuit_intent_fingerprint")(
        lambda value: validate_fingerprint(value, field="circuit_intent_fingerprint")
    )
    _fingerprint = field_validator("fingerprint")(validate_fingerprint)
    _refs = field_validator(
        "component_instance_ids",
        "pin_binding_ids",
        "net_ids",
        "power_domain_ids",
        mode="before",
    )(lambda value, info: _validate_sorted_unique_identifiers(value, field=info.field_name))

    @model_validator(mode="after")
    def validate_binding_fingerprint(self) -> CircuitSchematicBinding:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == ZERO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("schematic binding fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: Any) -> CircuitSchematicBinding:
        values["fingerprint"] = ZERO_FINGERPRINT
        return cls(**values)


class SchematicDesignObject(EngineeringObjectBase):
    object_type: Literal[ObjectType.SCHEMATIC_DESIGN] = ObjectType.SCHEMATIC_DESIGN
    symbols: tuple[str, ...]
    pins: tuple[str, ...]
    nets: tuple[str, ...]
    circuit_bindings: tuple[CircuitSchematicBinding, ...] = ()
    erc_result_evidence_id: str | None = None
    artifact_ref: str | None = None
    artifact_fingerprint: str | None = None

    _tuple_fields = field_validator("symbols", "pins", "nets")(
        lambda v: validate_text_tuple(v, field="schematic_field")
    )
    @field_validator("circuit_bindings", mode="before")
    @classmethod
    def _bindings(cls, value: object) -> tuple[CircuitSchematicBinding, ...]:
        if type(value) not in {tuple, list}:
            raise TypeError("circuit_bindings must be an immutable sequence")
        return tuple(
            item
            if isinstance(item, CircuitSchematicBinding)
            else CircuitSchematicBinding.model_validate(item, strict=False)
            for item in value
        )
    _erc_ref = field_validator("erc_result_evidence_id")(
        lambda v: (
            None
            if v is None
            else validate_identifier(v, field="erc_result_evidence_id")
        )
    )
    _artifact_ref = field_validator("artifact_ref")(
        lambda v: None if v is None else validate_identifier(v, field="artifact_ref")
    )
    _artifact_fingerprint = field_validator("artifact_fingerprint")(
        lambda v: None
        if v is None
        else validate_fingerprint(v, field="artifact_fingerprint")
    )

    @model_validator(mode="after")
    def validate_artifact_identity(self) -> SchematicDesignObject:
        if (self.artifact_ref is None) != (self.artifact_fingerprint is None):
            raise ValueError("schematic artifact identity is incomplete")
        binding_ids = tuple(item.binding_id for item in self.circuit_bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("schematic circuit binding ids must be unique")
        symbol_components = {item.split(":", 1)[0] for item in self.symbols}
        pin_ids = set(self.pins)
        net_ids = set(self.nets)
        for binding in self.circuit_bindings:
            if binding.circuit_intent_id.startswith("project-") and ":" in binding.circuit_intent_id:
                project_prefix = binding.circuit_intent_id.split(":", 1)[0]
                if project_prefix != self.project_id:
                    raise ValueError("schematic binding belongs to another project")
            if not set(binding.component_instance_ids) <= symbol_components:
                raise ValueError("schematic binding component reference is invalid")
            if not set(binding.pin_binding_ids) <= pin_ids:
                raise ValueError("schematic binding pin reference is invalid")
            if not set(binding.net_ids) <= net_ids:
                raise ValueError("schematic binding net reference is invalid")
        return self

    @property
    def schematic_design_id(self) -> str:
        return self.object_id


class EvidenceObject(EngineeringObjectBase):
    """Runtime evidence binding; canonical evidence remains in Evidence Layer.

    ``canonical_evidence_ref`` points to the existing Embrix Evidence/Receipt
    record.  This object is only the project-scoped runtime reference used for
    context resolution and lifecycle checks.
    """

    object_type: Literal[ObjectType.EVIDENCE] = ObjectType.EVIDENCE
    source: str
    tool: str
    result: str
    timestamp: datetime
    scope: str
    canonical_evidence_ref: str
    qualification_status: EvidenceQualificationStatus = EvidenceQualificationStatus.UNVERIFIED
    subject_object_id: str | None = None
    subject_revision: str | None = None
    subject_fingerprint: str | None = None
    subject_artifact_ref: str | None = None
    subject_artifact_fingerprint: str | None = None

    _text_fields = field_validator(
        "source", "tool", "result", "scope", "canonical_evidence_ref"
    )(lambda v: normalize_text(v, field="evidence_field"))
    _timestamp = field_validator("timestamp")(validate_utc_datetime)
    _subject_object_id = field_validator("subject_object_id")(
        lambda v: None
        if v is None
        else validate_identifier(v, field="subject_object_id")
    )
    _subject_revision = field_validator("subject_revision")(
        lambda v: None if v is None else validate_revision(v)
    )
    _subject_fingerprint = field_validator("subject_fingerprint")(
        lambda v: None
        if v is None
        else validate_fingerprint(v, field="subject_fingerprint")
    )
    _subject_artifact_ref = field_validator("subject_artifact_ref")(
        lambda v: None
        if v is None
        else validate_identifier(v, field="subject_artifact_ref")
    )
    _subject_artifact_fingerprint = field_validator("subject_artifact_fingerprint")(
        lambda v: None
        if v is None
        else validate_fingerprint(v, field="subject_artifact_fingerprint")
    )

    @model_validator(mode="after")
    def validate_subject_binding(self) -> EvidenceObject:
        subject_fields = (
            self.subject_object_id,
            self.subject_revision,
            self.subject_fingerprint,
        )
        if any(value is not None for value in subject_fields) and any(
            value is None for value in subject_fields
        ):
            raise ValueError("evidence subject binding is incomplete")
        if (self.subject_artifact_ref is None) != (
            self.subject_artifact_fingerprint is None
        ):
            raise ValueError("evidence subject artifact binding is incomplete")
        return self

    @property
    def evidence_id(self) -> str:
        return self.object_id


EngineeringObject = (
    RequirementObject
    | HardwareArchitectureObject
    | BudgetLedgerObject
    | CircuitIntentObject
    | SchematicDesignObject
    | EvidenceObject
)
