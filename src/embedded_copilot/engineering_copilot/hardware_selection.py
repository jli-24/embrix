from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.component_recommendation import (
    ComponentElectricalFact,
    ComponentInterfaceFact,
    ComponentPinFact,
    ComponentPowerFact,
    ComponentSourcePort,
    ComponentSourceRecord,
    SupportingComponentRequirement,
    validate_component_source,
)

from .common import validate_copy
from .contracts import (
    ArchitectureComponent,
    ArchitectureDesign,
    ArchitectureDesignStatus,
    DesignComponent,
    DesignEvidenceBinding,
    DesignPin,
    DesignSource,
    DesignTruth,
    DesignTruthOrigin,
    EngineeringWorkflow,
    PlanProvenance,
    TruthLevel,
)

if TYPE_CHECKING:
    from embedded_copilot.engineering_requirements.models import RequirementContext

_ZERO = "sha256:" + "0" * 64
# ST DS5319 Rev 20, Figure 8 and Table 5. The exact LQFP48 package has
# 48 pads, including VBAT; a partial pin list cannot support a design claim.
_STM32F103C8T6_LQFP48_PIN_NAMES = (
    "VBAT", "PC13-TAMPER-RTC", "PC14-OSC32_IN", "PC15-OSC32_OUT",
    "PD0-OSC_IN", "PD1-OSC_OUT", "NRST", "VSSA", "VDDA", "PA0-WKUP",
    "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB0", "PB1",
    "PB2", "PB10", "PB11", "VSS_1", "VDD_1", "PB12", "PB13",
    "PB14", "PB15", "PA8", "PA9", "PA10", "PA11", "PA12", "PA13",
    "VSS_2", "VDD_2", "PA14", "PA15", "PB3", "PB4", "PB5", "PB6",
    "PB7", "BOOT0", "PB8", "PB9", "VSS_3", "VDD_3",
)
_STM32F103C8T6_LQFP48_POWER_PINS = {
    "1": ("VBAT", "consumer"),
    "8": ("VSSA", "ground"),
    "9": ("VDDA", "consumer"),
    "23": ("VSS_1", "ground"),
    "24": ("VDD_1", "consumer"),
    "35": ("VSS_2", "ground"),
    "36": ("VDD_2", "consumer"),
    "47": ("VSS_3", "ground"),
    "48": ("VDD_3", "consumer"),
}


def _text(value: object, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = value.strip()
    if not checked or len(checked) > maximum or any(char in checked for char in ("\x00", "\r", "\n")):
        raise ValueError(f"{field} is invalid")
    return checked


def _id(value: object, field: str) -> str:
    return _text(value, field, 256)


def _projection_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _fp(value: object, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError(f"{field} is invalid")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    result = tuple(_text(item, field, 2048) for item in value)
    if len(result) != len({item.casefold() for item in result}):
        raise ValueError(f"{field} must be unique")
    return result


def _known(value: object) -> bool:
    return isinstance(value, str) and value.strip().casefold() not in {
        "",
        "unknown",
        "tbd",
        "n/a",
        "na",
        "none",
        "-",
    }


def _fingerprint(value: BaseModel, *, exclude: set[str]) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class HardwareSelectionContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class HardwareSelectionStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


class HardwareSelectionDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SupportingFor(HardwareSelectionContract):
    parent_slot_id: str
    parent_source_fingerprint: str
    requirement_ordinal: int = Field(ge=0, le=999)
    requirement_fingerprint: str
    expectation_ordinal: int = Field(ge=0, le=999)

    _parent = field_validator("parent_slot_id", mode="before")(
        lambda value: _id(value, "parent_slot_id")
    )
    _fps = field_validator("parent_source_fingerprint", "requirement_fingerprint", mode="before")(
        lambda value, info: _fp(value, info.field_name)
    )


class ComponentRequirement(HardwareSelectionContract):
    slot_id: str
    architecture_component_id: str
    role: str
    category: str
    functional_requirements: tuple[str, ...]
    electrical_requirements: tuple[str, ...] = ()
    interface_requirements: tuple[str, ...] = ()
    supply_constraints: tuple[str, ...] = ()
    package_constraints: tuple[str, ...] = ()
    environmental_constraints: tuple[str, ...] = ()
    cost_constraints: tuple[str, ...] = ()
    availability_constraints: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = (
        "DATASHEET",
        "SYMBOL",
        "FOOTPRINT",
        "PIN_FACTS",
        "ELECTRICAL_FACTS",
    )
    supporting_for: SupportingFor | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("slot_id", "architecture_component_id", "role", "category", mode="before")
    @classmethod
    def validate_identity(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator(
        "functional_requirements", "electrical_requirements", "interface_requirements",
        "supply_constraints", "package_constraints", "environmental_constraints",
        "cost_constraints", "availability_constraints", "required_evidence", mode="before"
    )
    @classmethod
    def validate_texts(cls, value: object, info) -> tuple[str, ...]:
        return _strings(value, info.field_name)


class HardwareSelectionEvaluation(HardwareSelectionContract):
    slot_id: str
    source: ComponentSourceRecord
    selectable: bool
    requirement_matches: tuple[str, ...]
    rejection_reasons: tuple[str, ...] = ()
    selection_reason: str

    @field_validator("slot_id", mode="before")
    @classmethod
    def validate_slot(cls, value: object) -> str:
        return _id(value, "slot_id")

    @field_validator("requirement_matches", "rejection_reasons", mode="before")
    @classmethod
    def validate_reasons(cls, value: object, info) -> tuple[str, ...]:
        return _strings(value, info.field_name)

    @field_validator("selection_reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return _text(value, "selection_reason", 4096)


class SelectedHardwareComponent(HardwareSelectionContract):
    slot_id: str
    architecture_component_id: str
    role: str
    quantity: int = Field(default=1, ge=1, le=1000)
    source: ComponentSourceRecord
    supporting_for: SupportingFor | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("slot_id", "architecture_component_id", "role", mode="before")
    @classmethod
    def validate_selected_identity(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @property
    def manufacturer(self) -> str:
        return self.source.manufacturer

    @property
    def mpn(self) -> str:
        return self.source.mpn

    @property
    def symbol(self) -> str | None:
        return self.source.symbol

    @property
    def footprint(self) -> str | None:
        return self.source.footprint


class HardwareSelectionProposal(HardwareSelectionContract):
    proposal_id: str
    project_id: str
    conversation_id: str
    architecture_id: str
    architecture_fingerprint: str
    requirements_fingerprint: str
    requirements: tuple[ComponentRequirement, ...]
    evaluations: tuple[HardwareSelectionEvaluation, ...]
    selected_components: tuple[SelectedHardwareComponent, ...]
    status: HardwareSelectionStatus = HardwareSelectionStatus.REVIEW
    fingerprint: str

    @field_validator("proposal_id", "project_id", "conversation_id", "architecture_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("architecture_fingerprint", "requirements_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> HardwareSelectionStatus:
        return value if isinstance(value, HardwareSelectionStatus) else HardwareSelectionStatus(value)

    @classmethod
    def create(cls, **values: object) -> HardwareSelectionProposal:
        material = dict(values)
        material["fingerprint"] = _ZERO
        provisional = cls.model_construct(**material)
        material["fingerprint"] = _fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify(self) -> HardwareSelectionProposal:
        if self.fingerprint != _fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("hardware selection proposal fingerprint mismatch")
        slots = tuple(item.slot_id for item in self.requirements)
        if not slots or len(slots) != len(set(slots)):
            raise ValueError("hardware selection requirements are invalid")
        selected_slots = tuple(item.slot_id for item in self.selected_components)
        if len(selected_slots) != len(set(selected_slots)) or set(selected_slots) != set(slots):
            raise ValueError("hardware selection is incomplete")
        requirements_by_slot = {item.slot_id: item for item in self.requirements}
        selected_by_slot = {item.slot_id: item for item in self.selected_components}
        for slot_id, requirement in requirements_by_slot.items():
            selected = selected_by_slot[slot_id]
            if selected.supporting_for != requirement.supporting_for:
                raise ValueError("supporting selection slot binding mismatch")
            support = requirement.supporting_for
            if support is None:
                continue
            parent = selected_by_slot.get(support.parent_slot_id)
            if (
                parent is None or parent.supporting_for is not None
                or parent.source.fingerprint != support.parent_source_fingerprint
                or support.requirement_ordinal >= len(parent.source.supporting_requirements)
            ):
                raise ValueError("supporting selection parent source mismatch")
            parent_requirement = parent.source.supporting_requirements[support.requirement_ordinal]
            if (
                support.requirement_fingerprint != _fingerprint(parent_requirement, exclude=set())
                or support.expectation_ordinal >= len(parent_requirement.connection_expectations)
                or requirement.role != parent_requirement.role
                or selected.role != requirement.role
                or selected.quantity != 1
                or requirement.role not in selected.source.supported_roles
            ):
                raise ValueError("supporting selection requirement or role mismatch")
        return self


class HardwareBOMItem(HardwareSelectionContract):
    item_id: str
    slot_id: str
    role: str
    manufacturer: str
    mpn: str
    quantity: int = Field(ge=1, le=1000)
    component_source_id: str
    symbol: str
    footprint: str
    evidence_references: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    approval_status: HardwareSelectionStatus = HardwareSelectionStatus.APPROVED
    supporting_for: SupportingFor | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator(
        "item_id", "slot_id", "role", "manufacturer", "mpn", "component_source_id", "symbol", "footprint", mode="before"
    )
    @classmethod
    def validate_item_text(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("evidence_references", mode="before")
    @classmethod
    def validate_evidence_references(cls, value: object) -> tuple[str, ...]:
        return _strings(value, "evidence_references")

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def validate_evidence_fingerprints(cls, value: object) -> tuple[str, ...]:
        values = _strings(value, "evidence_fingerprints")
        return tuple(_fp(item, "evidence_fingerprint") for item in values)


class HardwareBOM(HardwareSelectionContract):
    bom_id: str
    project_id: str
    conversation_id: str
    architecture_id: str
    architecture_fingerprint: str
    requirements_fingerprint: str
    revision: str
    items: tuple[HardwareBOMItem, ...]
    fingerprint: str

    @field_validator("bom_id", "project_id", "conversation_id", "architecture_id", "revision", mode="before")
    @classmethod
    def validate_bom_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("architecture_fingerprint", "requirements_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_bom_fps(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @classmethod
    def create(cls, **values: object) -> HardwareBOM:
        material = dict(values)
        material["fingerprint"] = _ZERO
        provisional = cls.model_construct(**material)
        material["fingerprint"] = _fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify_bom(self) -> HardwareBOM:
        if self.fingerprint != _fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("hardware BOM fingerprint mismatch")
        if not self.items or len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("hardware BOM items are invalid")
        if any(item.approval_status is not HardwareSelectionStatus.APPROVED for item in self.items):
            raise ValueError("hardware BOM contains unapproved item")
        return self


class HardwareTruthComponent(HardwareSelectionContract):
    slot_id: str
    architecture_component_id: str
    role: str
    quantity: int = Field(ge=1, le=1000)
    manufacturer: str
    mpn: str
    symbol: str
    footprint: str
    source_id: str
    source_uri: str
    source_revision: str
    source_fingerprint: str
    pin_facts: tuple[ComponentPinFact, ...]
    electrical_facts: tuple[ComponentElectricalFact, ...]
    interface_facts: tuple[ComponentInterfaceFact, ...]
    power_facts: tuple[ComponentPowerFact, ...] = ()
    supporting_requirements: tuple[SupportingComponentRequirement, ...] = ()
    supporting_for: SupportingFor | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator(
        "slot_id", "architecture_component_id", "role", "manufacturer", "mpn", "symbol", "footprint",
        "source_id", "source_uri", "source_revision", mode="before"
    )
    @classmethod
    def validate_truth_text(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def validate_truth_fp(cls, value: object) -> str:
        return _fp(value, "source_fingerprint")

    @field_validator("pin_facts", "electrical_facts", "interface_facts", "power_facts", mode="before")
    @classmethod
    def validate_truth_facts(cls, value: object, info) -> tuple:
        if type(value) is list:
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError(f"{info.field_name} must be a tuple")
        return tuple(copy.deepcopy(value))


class HardwareTruth(HardwareSelectionContract):
    truth_id: str
    project_id: str
    conversation_id: str
    architecture_id: str
    architecture_fingerprint: str
    requirements_fingerprint: str
    bom_id: str
    bom_fingerprint: str
    approval_fingerprint: str
    components: tuple[HardwareTruthComponent, ...]
    fingerprint: str

    @field_validator("truth_id", "project_id", "conversation_id", "architecture_id", "bom_id", mode="before")
    @classmethod
    def validate_truth_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("architecture_fingerprint", "requirements_fingerprint", "bom_fingerprint", "approval_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_truth_fps(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @classmethod
    def create(cls, **values: object) -> HardwareTruth:
        material = dict(values)
        material["fingerprint"] = _ZERO
        provisional = cls.model_construct(**material)
        material["fingerprint"] = _fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify_truth(self) -> HardwareTruth:
        if self.fingerprint != _fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("hardware truth fingerprint mismatch")
        if not self.components or len({item.slot_id for item in self.components}) != len(self.components):
            raise ValueError("hardware truth components are invalid")
        return self


class HardwareSelectionApproval(HardwareSelectionContract):
    approval_id: str
    proposal_id: str
    project_id: str
    architecture_fingerprint: str
    selection_fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    bom_fingerprint: str
    decision: HardwareSelectionDecision
    reviewer: str
    decided_at: datetime
    fingerprint: str

    @field_validator("approval_id", "proposal_id", "project_id", "reviewer", mode="before")
    @classmethod
    def validate_approval_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("architecture_fingerprint", "selection_fingerprint", "bom_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_approval_fps(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def validate_approval_evidence(cls, value: object) -> tuple[str, ...]:
        return tuple(_fp(item, "evidence_fingerprint") for item in _strings(value, "evidence_fingerprints"))

    @field_validator("decision", mode="before")
    @classmethod
    def validate_decision(cls, value: object) -> HardwareSelectionDecision:
        return value if isinstance(value, HardwareSelectionDecision) else HardwareSelectionDecision(value)

    @field_validator("decided_at", mode="before")
    @classmethod
    def validate_decided_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError("decided_at is invalid") from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("decided_at is invalid")
        return value.astimezone(UTC)

    @classmethod
    def create(cls, **values: object) -> HardwareSelectionApproval:
        material = dict(values)
        material["fingerprint"] = _ZERO
        provisional = cls.model_construct(**material)
        material["fingerprint"] = _fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify_approval(self) -> HardwareSelectionApproval:
        if self.fingerprint != _fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("hardware selection approval fingerprint mismatch")
        return self


class HardwareSelectionState(HardwareSelectionContract):
    proposal: HardwareSelectionProposal
    status: HardwareSelectionStatus
    approval: HardwareSelectionApproval | None = None
    bom: HardwareBOM | None = None
    hardware_truth: HardwareTruth | None = None

    @model_validator(mode="after")
    def verify_state(self) -> HardwareSelectionState:
        if self.status is HardwareSelectionStatus.APPROVED:
            if self.approval is None or self.bom is None or self.hardware_truth is None:
                raise ValueError("approved hardware selection is incomplete")
            if self.proposal.status is not HardwareSelectionStatus.REVIEW:
                raise ValueError("approved hardware selection proposal state is invalid")
            if self.approval.decision is not HardwareSelectionDecision.APPROVED:
                raise ValueError("approved hardware selection decision is invalid")
            if (
                self.approval.proposal_id != self.proposal.proposal_id
                or self.approval.project_id != self.proposal.project_id
                or self.approval.architecture_fingerprint != self.proposal.architecture_fingerprint
                or self.approval.selection_fingerprint != self.proposal.fingerprint
                or self.approval.bom_fingerprint != self.bom.fingerprint
            ):
                raise ValueError("hardware selection approval binding mismatch")
            selected_evidence = tuple(dict.fromkeys(
                item.source.fingerprint for item in self.proposal.selected_components
            ))
            if self.approval.evidence_fingerprints != selected_evidence:
                raise ValueError("hardware selection evidence binding mismatch")
            if (
                self.bom.project_id != self.proposal.project_id
                or self.bom.conversation_id != self.proposal.conversation_id
                or self.bom.architecture_id != self.proposal.architecture_id
                or self.bom.architecture_fingerprint != self.proposal.architecture_fingerprint
                or self.bom.requirements_fingerprint != self.proposal.requirements_fingerprint
            ):
                raise ValueError("hardware selection BOM binding mismatch")
            if (
                self.hardware_truth.project_id != self.proposal.project_id
                or self.hardware_truth.conversation_id != self.proposal.conversation_id
                or self.hardware_truth.architecture_id != self.proposal.architecture_id
                or self.hardware_truth.architecture_fingerprint != self.proposal.architecture_fingerprint
                or self.hardware_truth.requirements_fingerprint != self.proposal.requirements_fingerprint
                or self.hardware_truth.approval_fingerprint != self.approval.fingerprint
            ):
                raise ValueError("hardware truth approval binding mismatch")
            if self.approval.bom_fingerprint != self.bom.fingerprint:
                raise ValueError("hardware selection approval BOM mismatch")
            if self.hardware_truth.bom_fingerprint != self.bom.fingerprint:
                raise ValueError("hardware truth BOM mismatch")
        elif self.status is HardwareSelectionStatus.REJECTED:
            if self.approval is None or self.approval.decision is not HardwareSelectionDecision.REJECTED:
                raise ValueError("rejected hardware selection decision is invalid")
            if self.bom is not None or self.hardware_truth is not None:
                raise ValueError("rejected hardware selection cannot contain approved outputs")
        else:
            if self.proposal.status is not HardwareSelectionStatus.REVIEW:
                raise ValueError("reviewable hardware selection proposal state is invalid")
            if self.approval is not None or self.bom is not None or self.hardware_truth is not None:
                raise ValueError("unapproved hardware selection cannot contain decision outputs")
        return self


def _role(component: ArchitectureComponent) -> str:
    value = f"{component.category} {component.name}".upper()
    for token, role in (
        ("CONTROLLER", "CONTROLLER"),
        ("MCU", "CONTROLLER"),
        ("POWER", "POWER"),
        ("SENSOR", "SENSOR"),
        ("ACTUATOR", "ACTUATOR"),
        ("MOTOR", "ACTUATOR"),
        ("COMMUNICATION", "COMMUNICATION"),
    ):
        if token in value:
            return role
    return "OTHER"


def _requirements(architecture: ArchitectureDesign) -> tuple[ComponentRequirement, ...]:
    hardware_ids = set(architecture.boundary.hardware_component_ids)
    components = tuple(
        item for item in architecture.system_components
        if item.component_id in hardware_ids and item.category.upper() != "SYSTEM"
    )
    result: list[ComponentRequirement] = []
    for component in components:
        role = _role(component)
        protocols = tuple(
            sorted({
                interface.protocol
                for interface in architecture.interfaces
                if component.component_id in {interface.source_component_id, interface.target_component_id}
                and interface.protocol.upper() not in {"UNKNOWN", ""}
            })
        )
        result.append(
            ComponentRequirement(
                slot_id=f"slot-{component.component_id}",
                architecture_component_id=component.component_id,
                role=role,
                category=component.category,
                functional_requirements=(component.responsibility,),
                interface_requirements=protocols,
            )
        )
    if not result:
        raise ValueError("approved architecture has no hardware selection slots")
    return tuple(result)


def _evaluate(requirement: ComponentRequirement, source: ComponentSourceRecord) -> HardwareSelectionEvaluation:
    reasons: list[str] = []
    matches = [f"role:{requirement.role}"]
    if requirement.supporting_for is not None and requirement.role not in source.supported_roles:
        reasons.append("supporting_role_mismatch")
    if not _known(source.manufacturer):
        reasons.append("manufacturer_missing")
    if not _known(source.mpn):
        reasons.append("mpn_missing")
    if not _known(source.source_uri) or not _known(source.source_revision):
        reasons.append("datasheet_evidence_missing")
    if not _known(source.symbol):
        reasons.append("symbol_mapping_missing")
    if not _known(source.footprint):
        reasons.append("footprint_mapping_missing")
    if not source.pin_facts:
        reasons.append("pin_facts_missing")
    elif any(not _known(pin.pad_number) for pin in source.pin_facts):
        reasons.append("pad_mapping_missing")
    pins_by_number = {pin.number.casefold(): pin for pin in source.pin_facts}
    for interface in source.interface_facts:
        for reference in interface.pin_names:
            pin_matches = tuple(
                pin for pin in source.pin_facts
                if reference.casefold() in {pin.number.casefold(), pin.name.casefold()}
            )
            if len(pin_matches) != 1:
                reasons.append("interface_pin_reference_ambiguous")
                break
            pin = pin_matches[0]
            if pin.interface_protocols and interface.protocol.casefold() not in {
                protocol.casefold() for protocol in pin.interface_protocols
            }:
                reasons.append(f"interface_pin_protocol_mismatch:{interface.protocol}")
                break
    if any(
        (pin := pins_by_number.get(number.casefold())) is None or not pin.is_power
        for fact in source.power_facts
        for number in fact.pin_numbers
    ):
        reasons.append("power_pin_reference_invalid")
    if requirement.role.casefold() in {"power", "power_regulator"} and not any(
        fact.role.casefold() == "source" and fact.nominal_voltage is not None
        for fact in source.power_facts
    ):
        reasons.append("required_power_source_missing")
    # ST DS5319 Rev 20, Figure 8 and Table 5: VBAT, VDDA/VSSA and
    # three VDD/VSS pairs are package pins; no separate VREF pin exists.
    if (
        source.mpn.strip().upper() == "STM32F103C8T6"
        and source.package.strip().upper() == "LQFP48"
    ):
        expected_numbers = {str(number) for number in range(1, 49)}
        if set(pins_by_number) != expected_numbers:
            reasons.append("package_pin_map_incomplete")
        for number, name in enumerate(_STM32F103C8T6_LQFP48_PIN_NAMES, 1):
            pin = pins_by_number.get(str(number))
            if pin is None:
                continue
            if pin.name.upper() != name:
                reasons.append(f"package_pin_name_mismatch:{number}")
            if pin.pad_number != str(number):
                reasons.append(f"package_pad_mismatch:{number}")
        for number, (name, role) in _STM32F103C8T6_LQFP48_POWER_PINS.items():
            pin = pins_by_number.get(number)
            matching_facts = tuple(
                fact for fact in source.power_facts if number in fact.pin_numbers
            )
            if (
                pin is None
                or pin.name.upper() != name
                or pin.pad_number != number
                or not pin.is_power
                or len(matching_facts) != 1
                or matching_facts[0].role.casefold() != role
            ):
                reasons.append(f"required_power_pin_missing:{number}")
            elif role == "consumer" and (
                matching_facts[0].minimum_voltage is None
                or matching_facts[0].maximum_voltage is None
            ):
                reasons.append(f"required_power_range_missing:{number}")
    # AMS1117-3.3 datasheet pp. 1, 2 and 4 are represented as source
    # completeness requirements only; no system power or thermal pass follows.
    if source.mpn.strip().upper() == "AMS1117-3.3":
        if source.package.strip().upper() != "SOT-223":
            reasons.append("ams1117_unsupported_package")
        manufacturer = source.manufacturer.strip().casefold().removesuffix(", inc.").strip()
        if manufacturer != "advanced monolithic systems":
            reasons.append("ams1117_manufacturer_mismatch")
        expected_pins = {"1": "GND", "2": "VOUT", "3": "VIN"}
        for number, name in expected_pins.items():
            pin = pins_by_number.get(number)
            if (
                pin is None
                or pin.name.strip().upper() != name
                or pin.pad_number != number
                or not pin.is_power
            ):
                reasons.append(f"ams1117_required_pin_missing:{name}")
        expected_power_facts = (
            ("3", {"input", "vin"}, "consumer", "VIN"),
            ("2", {"3v3"}, "source", "VOUT"),
            ("1", {"gnd"}, "ground", "GND"),
        )
        for number, domains, role, label in expected_power_facts:
            if not any(
                number in fact.pin_numbers
                and fact.domain.strip().casefold() in domains
                and fact.role.casefold() == role
                for fact in source.power_facts
            ):
                reasons.append(f"ams1117_power_fact_missing:{label}")
        if not any(
            item.role.casefold() == "output_stabilization"
            and bool(item.evidence_refs)
            for item in source.supporting_requirements
        ):
            reasons.append("ams1117_output_stabilization_evidence_missing")
    if not source.electrical_facts:
        reasons.append("electrical_facts_missing")
    protocols = {item.protocol.casefold() for item in source.interface_facts}
    # TI SLOS346 Rev O, section 7: SN65HVD230 D/R are CAN transceiver pins,
    # not a UART interface. Keep this exact-part rejection narrow.
    if source.mpn.strip().upper() in {"SN65HVD230", "SN65HVD230D"} and "uart" in protocols:
        reasons.append("unsupported_part_protocol:UART")
    for protocol in requirement.interface_requirements:
        if protocol.casefold() not in protocols:
            reasons.append(f"interface_evidence_missing:{protocol}")
    return HardwareSelectionEvaluation(
        slot_id=requirement.slot_id,
        source=source,
        selectable=not reasons,
        requirement_matches=tuple(matches),
        rejection_reasons=tuple(reasons),
        selection_reason=(
            "Selected deterministic evidence-backed catalog candidate."
            if not reasons
            else "Candidate rejected because required engineering evidence is incomplete."
        ),
    )


class HardwareSelectionService:
    """Create reviewable hardware selections from approved architecture and a read-only source."""

    __slots__ = ("_source",)

    def __init__(self, source: ComponentSourcePort) -> None:
        self._source = validate_component_source(source)

    def _proposal_sources_current(self, proposal: HardwareSelectionProposal) -> bool:
        requirements_by_slot = {item.slot_id: item for item in proposal.requirements}
        for selected in proposal.selected_components:
            requirement = requirements_by_slot[selected.slot_id]
            matches = tuple(
                ComponentSourceRecord.model_validate(
                    copy.deepcopy(item.model_dump(mode="python")), strict=False,
                )
                for item in self._source.search(
                    proposal.project_id, proposal.architecture_id, requirement,
                )
                if item.source_id == selected.source.source_id
                and item.fingerprint == selected.source.fingerprint
            )
            if len(matches) != 1 or not _evaluate(requirement, matches[0]).selectable:
                return False
        return True

    def selection_sources_current(self, state: HardwareSelectionState) -> bool:
        """Read the Source Port again; search errors remain distinguishable to the Owner."""
        try:
            checked = HardwareSelectionState.model_validate(
                copy.deepcopy(state.model_dump(mode="python")), strict=False,
            )
        except (AttributeError, TypeError, ValueError):
            return False
        return (
            checked.status is HardwareSelectionStatus.APPROVED
            and self._proposal_sources_current(checked.proposal)
        )

    def create_proposal(
        self,
        architecture: ArchitectureDesign,
        requirements: RequirementContext,
        *,
        workflow: EngineeringWorkflow,
        conversation_id: str,
        candidate_selections: tuple[tuple[str, str, str], ...] | None = None,
        parent_selections: tuple[tuple[str, str, str], ...] | None = None,
    ) -> HardwareSelectionProposal:
        checked_architecture = validate_copy(architecture, ArchitectureDesign)
        if checked_architecture.status is not ArchitectureDesignStatus.APPROVED:
            raise ValueError("hardware selection requires approved architecture")
        if (
            not isinstance(requirements, BaseModel)
            or requirements.__class__.__name__ != "RequirementContext"
            or requirements.__class__.__module__ != "embedded_copilot.engineering_requirements.models"
        ):
            raise TypeError("requirements must be RequirementContext")
        if type(workflow) is not EngineeringWorkflow:
            raise TypeError("workflow must be EngineeringWorkflow")
        if workflow.project_id != checked_architecture.project_id or workflow.architecture_id != checked_architecture.architecture_id:
            raise ValueError("hardware selection workflow binding mismatch")
        if requirements.project_id != checked_architecture.project_id or requirements.conversation_id != conversation_id:
            raise ValueError("hardware selection requirements binding mismatch")
        if workflow.conversation_id != conversation_id:
            raise ValueError("hardware selection workflow conversation mismatch")
        slots = list(_requirements(checked_architecture))
        if candidate_selections is not None and parent_selections is not None:
            raise ValueError("hardware parent and full candidate selections cannot be mixed")
        chosen: dict[str, tuple[str, str]] | None = None
        if candidate_selections is not None:
            if type(candidate_selections) is not tuple:
                raise TypeError("hardware model candidate must be a tuple")
            chosen = {}
            for item in candidate_selections:
                if type(item) is not tuple or len(item) != 3:
                    raise TypeError("hardware model candidate selection is invalid")
                slot_id, source_id, source_fingerprint = item
                slot_id = _id(slot_id, "slot_id")
                source_id = _id(source_id, "source_id")
                source_fingerprint = _fp(source_fingerprint, "source_fingerprint")
                if slot_id in chosen:
                    raise ValueError("hardware model candidate slot is repeated")
                chosen[slot_id] = (source_id, source_fingerprint)
        if parent_selections is not None:
            if type(parent_selections) is not tuple:
                raise TypeError("hardware parent selections must be a tuple")
            chosen = {}
            for item in parent_selections:
                if type(item) is not tuple or len(item) != 3:
                    raise TypeError("hardware parent selection is invalid")
                slot_id, source_id, source_fingerprint = item
                slot_id = _id(slot_id, "slot_id")
                source_id = _id(source_id, "source_id")
                source_fingerprint = _fp(source_fingerprint, "source_fingerprint")
                if slot_id in chosen:
                    raise ValueError("hardware parent slot is repeated")
                chosen[slot_id] = (source_id, source_fingerprint)
            if set(chosen) != {slot.slot_id for slot in slots}:
                raise ValueError("hardware parent slot binding mismatch")
        evaluations: list[HardwareSelectionEvaluation] = []
        selected: list[SelectedHardwareComponent] = []
        for slot in slots:
            raw_candidates = self._source.search(
                checked_architecture.project_id,
                checked_architecture.architecture_id,
                slot,
            )
            candidates = tuple(
                ComponentSourceRecord.model_validate(copy.deepcopy(item.model_dump(mode="python")), strict=False)
                for item in raw_candidates
            )
            slot_evaluations = tuple(_evaluate(slot, item) for item in candidates)
            if not slot_evaluations:
                raise ValueError(f"component evidence unavailable for {slot.role}")
            evaluations.extend(slot_evaluations)
            if chosen is None:
                selectable = next((item for item in slot_evaluations if item.selectable), None)
            else:
                if slot.slot_id not in chosen:
                    raise ValueError("hardware model candidate slot binding mismatch")
                source_id, source_fingerprint = chosen[slot.slot_id]
                matches = tuple(
                    item for item in slot_evaluations
                    if item.source.source_id == source_id
                    and item.source.fingerprint == source_fingerprint
                )
                if len(matches) != 1 or not matches[0].selectable:
                    raise ValueError("hardware model candidate is not current and selectable")
                selectable = matches[0]
            if selectable is None:
                reasons = ",".join(slot_evaluations[0].rejection_reasons)
                raise ValueError(f"component evidence insufficient for {slot.role}: {reasons}")
            selected.append(
                SelectedHardwareComponent(
                    slot_id=slot.slot_id,
                    architecture_component_id=slot.architecture_component_id,
                    role=slot.role,
                    source=selectable.source,
                    supporting_for=slot.supporting_for,
                )
            )
        root_choices_only = parent_selections is not None
        for parent in tuple(selected):
            for requirement_ordinal, requirement in enumerate(parent.source.supporting_requirements):
                if (
                    requirement.role not in {"I2C_PULLUPS", "DECOUPLING"}
                    or not requirement.connection_expectations
                    or requirement.quantity != len(requirement.connection_expectations)
                ):
                    continue
                digest = _fingerprint(requirement, exclude=set())
                for expectation_ordinal, _expectation in enumerate(requirement.connection_expectations):
                    support = SupportingFor(
                        parent_slot_id=parent.slot_id,
                        parent_source_fingerprint=parent.source.fingerprint,
                        requirement_ordinal=requirement_ordinal,
                        requirement_fingerprint=digest,
                        expectation_ordinal=expectation_ordinal,
                    )
                    slot = ComponentRequirement(
                        slot_id=_projection_id(
                            "support-slot", checked_architecture.project_id,
                            parent.slot_id, parent.source.fingerprint,
                            str(requirement_ordinal), digest, str(expectation_ordinal),
                        ),
                        architecture_component_id=parent.architecture_component_id,
                        role=requirement.role, category=requirement.role,
                        functional_requirements=(requirement.description,),
                        supporting_for=support,
                    )
                    raw_candidates = self._source.search(
                        checked_architecture.project_id, checked_architecture.architecture_id, slot,
                    )
                    candidates = tuple(
                        ComponentSourceRecord.model_validate(
                            copy.deepcopy(item.model_dump(mode="python")), strict=False,
                        )
                        for item in raw_candidates
                    )
                    slot_evaluations = tuple(_evaluate(slot, item) for item in candidates)
                    if not slot_evaluations:
                        raise ValueError(f"component evidence unavailable for {slot.role}")
                    evaluations.extend(slot_evaluations)
                    if chosen is None or root_choices_only:
                        selectable = next((item for item in slot_evaluations if item.selectable), None)
                    else:
                        if slot.slot_id not in chosen:
                            raise ValueError("hardware model candidate slot binding mismatch")
                        source_id, source_fingerprint = chosen[slot.slot_id]
                        matches = tuple(
                            item for item in slot_evaluations
                            if item.source.source_id == source_id
                            and item.source.fingerprint == source_fingerprint
                        )
                        if len(matches) != 1 or not matches[0].selectable:
                            raise ValueError("hardware model candidate is not current and selectable")
                        selectable = matches[0]
                    if selectable is None:
                        raise ValueError(f"component evidence insufficient for {slot.role}")
                    slots.append(slot)
                    selected.append(SelectedHardwareComponent(
                        slot_id=slot.slot_id,
                        architecture_component_id=slot.architecture_component_id,
                        role=slot.role, quantity=1,
                        source=selectable.source, supporting_for=support,
                    ))
        if candidate_selections is not None and set(chosen) != {slot.slot_id for slot in slots}:
            raise ValueError("hardware model candidate slot binding mismatch")
        return HardwareSelectionProposal.create(
            proposal_id=f"hardware-selection-{checked_architecture.project_id}-{checked_architecture.architecture_id}",
            project_id=checked_architecture.project_id,
            conversation_id=_id(conversation_id, "conversation_id"),
            architecture_id=checked_architecture.architecture_id,
            architecture_fingerprint=checked_architecture.fingerprint,
            requirements_fingerprint=requirements.fingerprint,
            requirements=tuple(slots),
            evaluations=tuple(evaluations),
            selected_components=tuple(selected),
            status=HardwareSelectionStatus.REVIEW,
        )

    def approve(self, proposal: HardwareSelectionProposal, *, reviewer: str, decided_at: datetime) -> HardwareSelectionState:
        return self.decide(proposal, HardwareSelectionDecision.APPROVED, reviewer=reviewer, decided_at=decided_at)

    def decide(
        self,
        proposal: HardwareSelectionProposal,
        decision: HardwareSelectionDecision,
        *,
        reviewer: str,
        decided_at: datetime,
    ) -> HardwareSelectionState:
        checked = HardwareSelectionProposal.model_validate(copy.deepcopy(proposal.model_dump(mode="python")), strict=False)
        target = decision if isinstance(decision, HardwareSelectionDecision) else HardwareSelectionDecision(decision)
        if checked.status is not HardwareSelectionStatus.REVIEW:
            raise ValueError("hardware selection proposal is not reviewable")
        if target is HardwareSelectionDecision.REJECTED:
            approval = HardwareSelectionApproval.create(
                approval_id=f"hardware-approval-{checked.proposal_id}",
                proposal_id=checked.proposal_id,
                project_id=checked.project_id,
                architecture_fingerprint=checked.architecture_fingerprint,
                selection_fingerprint=checked.fingerprint,
                evidence_fingerprints=tuple(dict.fromkeys(
                    item.source.fingerprint for item in checked.selected_components
                )),
                bom_fingerprint=_ZERO,
                decision=target,
                reviewer=reviewer,
                decided_at=decided_at,
            )
            return HardwareSelectionState(proposal=checked, status=HardwareSelectionStatus.REJECTED, approval=approval)

        if not self._proposal_sources_current(checked):
            raise ValueError("hardware selection source changed before decision")

        items = tuple(
            HardwareBOMItem(
                item_id=f"bom-item-{item.slot_id}",
                slot_id=item.slot_id,
                role=item.role,
                manufacturer=item.source.manufacturer,
                mpn=item.source.mpn,
                quantity=item.quantity,
                component_source_id=item.source.source_id,
                symbol=item.source.symbol,
                footprint=item.source.footprint,
                evidence_references=(item.source.source_id,),
                evidence_fingerprints=(item.source.fingerprint,),
                supporting_for=item.supporting_for,
            )
            for item in checked.selected_components
        )
        bom = HardwareBOM.create(
            bom_id=f"bom-{checked.proposal_id}",
            project_id=checked.project_id,
            conversation_id=checked.conversation_id,
            architecture_id=checked.architecture_id,
            architecture_fingerprint=checked.architecture_fingerprint,
            requirements_fingerprint=checked.requirements_fingerprint,
            revision=f"r-{checked.fingerprint[-12:]}",
            items=items,
        )
        approval = HardwareSelectionApproval.create(
            approval_id=f"hardware-approval-{checked.proposal_id}",
            proposal_id=checked.proposal_id,
            project_id=checked.project_id,
            architecture_fingerprint=checked.architecture_fingerprint,
            selection_fingerprint=checked.fingerprint,
            evidence_fingerprints=tuple(dict.fromkeys(
                item.source.fingerprint for item in checked.selected_components
            )),
            bom_fingerprint=bom.fingerprint,
            decision=target,
            reviewer=reviewer,
            decided_at=decided_at,
        )
        components = tuple(
            HardwareTruthComponent(
                slot_id=item.slot_id,
                architecture_component_id=item.architecture_component_id,
                role=item.role,
                quantity=item.quantity,
                manufacturer=item.source.manufacturer,
                mpn=item.source.mpn,
                symbol=item.source.symbol,
                footprint=item.source.footprint,
                source_id=item.source.source_id,
                source_uri=item.source.source_uri,
                source_revision=item.source.source_revision,
                source_fingerprint=item.source.fingerprint,
                pin_facts=item.source.pin_facts,
                electrical_facts=item.source.electrical_facts,
                interface_facts=item.source.interface_facts,
                power_facts=item.source.power_facts,
                supporting_requirements=item.source.supporting_requirements,
                supporting_for=item.supporting_for,
            )
            for item in checked.selected_components
        )
        truth = HardwareTruth.create(
            truth_id=f"hardware-truth-{checked.proposal_id}",
            project_id=checked.project_id,
            conversation_id=checked.conversation_id,
            architecture_id=checked.architecture_id,
            architecture_fingerprint=checked.architecture_fingerprint,
            requirements_fingerprint=checked.requirements_fingerprint,
            bom_id=bom.bom_id,
            bom_fingerprint=bom.fingerprint,
            approval_fingerprint=approval.fingerprint,
            components=components,
        )
        return HardwareSelectionState(
            proposal=checked,
            status=HardwareSelectionStatus.APPROVED,
            approval=approval,
            bom=bom,
            hardware_truth=truth,
        )

    def project_design_truth(
        self,
        base_truth: DesignTruth,
        hardware_truth: HardwareTruth,
        *,
        architecture_review: object | None = None,
    ) -> DesignTruth:
        checked_base = validate_copy(base_truth, DesignTruth)
        checked_hardware = HardwareTruth.model_validate(copy.deepcopy(hardware_truth.model_dump(mode="python")), strict=False)
        if checked_hardware.approval_fingerprint == _ZERO:
            raise ValueError("hardware truth approval is unavailable")
        if checked_base.project_id != checked_hardware.project_id or checked_base.architecture_id != checked_hardware.architecture_id:
            raise ValueError("hardware truth DesignTruth project binding mismatch")
        if architecture_review is not None and getattr(getattr(architecture_review, "status", None), "value", None) != "APPROVED":
            raise ValueError("hardware truth requires approved architecture review")
        truth_id = _projection_id("design-truth", checked_hardware.truth_id)
        bindings: list[DesignEvidenceBinding] = [
            DesignEvidenceBinding.create(
                binding_id=_projection_id("design-evidence", truth_id, "architecture"),
                entity_id=truth_id,
                project_id=checked_hardware.project_id,
                source=DesignSource.ARCHITECTURE,
                source_id=checked_hardware.architecture_id,
                artifact_ref=checked_hardware.architecture_id,
                locator="approved-architecture",
                source_fingerprint=checked_hardware.architecture_fingerprint,
                relationship="preserves approved architecture binding",
                truth_level=TruthLevel.EXPLICIT,
            ),
            DesignEvidenceBinding.create(
                binding_id=_projection_id("design-evidence", truth_id, "root"),
                entity_id=truth_id,
                project_id=checked_hardware.project_id,
                source=DesignSource.DATASHEET,
                source_id=checked_hardware.truth_id,
                artifact_ref=checked_hardware.bom_id,
                locator="approved-hardware-truth",
                source_fingerprint=checked_hardware.fingerprint,
                relationship="projected from approved hardware truth",
                truth_level=TruthLevel.EXPLICIT,
            )
        ]
        components: list[DesignComponent] = []
        pins: list[DesignPin] = []
        for selected in checked_hardware.components:
            component_id = _projection_id("design-component", truth_id, selected.slot_id)
            component_binding = _projection_id("design-evidence", component_id)
            bindings.append(
                DesignEvidenceBinding.create(
                    binding_id=component_binding,
                    entity_id=component_id,
                    project_id=checked_hardware.project_id,
                    source=DesignSource.DATASHEET,
                    source_id=selected.source_id,
                    artifact_ref=selected.source_id,
                    locator="component",
                    source_fingerprint=selected.source_fingerprint,
                    relationship="projects approved component identity",
                    truth_level=TruthLevel.EXPLICIT,
                )
            )
            components.append(
                DesignComponent.create(
                    component_id=component_id,
                    name=selected.mpn,
                    type=selected.role.casefold(),
                    category=selected.role,
                    description=f"{selected.manufacturer} {selected.mpn}",
                    symbol_binding=selected.symbol,
                    confidence=1.0,
                    truth_level=TruthLevel.EXPLICIT,
                    evidence_refs=(component_binding,),
                )
            )
            for index, pin in enumerate(selected.pin_facts, start=1):
                pin_id = _projection_id("design-pin", truth_id, selected.slot_id, pin.number)
                pin_binding = _projection_id("design-evidence", pin_id)
                bindings.append(
                    DesignEvidenceBinding.create(
                        binding_id=pin_binding,
                        entity_id=pin_id,
                        project_id=checked_hardware.project_id,
                        source=DesignSource.DATASHEET,
                        source_id=selected.source_id,
                        artifact_ref=selected.source_id,
                        locator=f"pin:{pin.number}",
                        source_fingerprint=selected.source_fingerprint,
                        relationship="projects datasheet pin fact",
                        truth_level=TruthLevel.EXPLICIT,
                    )
                )
                pins.append(
                    DesignPin.create(
                        pin_id=pin_id,
                        component_id=component_id,
                        pin_name=pin.name,
                        pin_number=pin.number,
                        function=pin.function,
                        confidence=1.0,
                        truth_level=TruthLevel.EXPLICIT,
                        evidence_refs=(pin_binding,),
                    )
                )
        provenance = (
            PlanProvenance.create(
                project_id=checked_hardware.project_id,
                source_type="ARCHITECTURE_DESIGN",
                source_id=checked_hardware.architecture_id,
                source_fingerprint=checked_hardware.architecture_fingerprint,
            ),
            PlanProvenance.create(
                project_id=checked_hardware.project_id,
                source_type="HARDWARE_TRUTH",
                source_id=checked_hardware.truth_id,
                source_fingerprint=checked_hardware.fingerprint,
            ),
        )
        return DesignTruth.create(
            truth_id=truth_id,
            tenant_id=checked_base.tenant_id,
            project_id=checked_hardware.project_id,
            design_revision=f"hardware-{checked_hardware.fingerprint[-24:]}",
            conversation_id=checked_hardware.conversation_id,
            origin=DesignTruthOrigin.CREATE,
            architecture_id=checked_hardware.architecture_id,
            components=tuple(components),
            pins=tuple(pins),
            evidence_bindings=tuple(bindings),
            provenance=provenance,
        )


__all__ = (
    "ComponentRequirement",
    "HardwareBOM",
    "HardwareBOMItem",
    "HardwareSelectionApproval",
    "HardwareSelectionDecision",
    "HardwareSelectionEvaluation",
    "HardwareSelectionProposal",
    "HardwareSelectionService",
    "HardwareSelectionState",
    "HardwareSelectionStatus",
    "HardwareTruth",
    "HardwareTruthComponent",
    "SelectedHardwareComponent",
)
