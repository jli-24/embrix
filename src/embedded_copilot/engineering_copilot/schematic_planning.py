from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.engineering_runtime import CircuitIntentObject, LifecycleStatus

from .common import ident, text
from .contracts import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureInterfaceType,
    DesignComponent,
    DesignEvidenceBinding,
    DesignInterface,
    DesignPin,
    DesignPowerDomain,
    DesignSignal,
    DesignSource,
    DesignTruth,
    DesignTruthOrigin,
    DesignTruthStatus,
    NetBinding,
    PlanProvenance,
    TruthLevel,
)
from .hardware_selection import (
    HardwareBOM,
    HardwareSelectionState,
    HardwareSelectionStatus,
    HardwareTruth,
    HardwareTruthComponent,
)

_ZERO = "sha256:" + "0" * 64
_KNOWN = {"UNKNOWN", "TBD", "N/A", "NA", "NONE", "-"}
_PROTOCOL_SIGNALS: dict[str, tuple[str, ...]] = {
    "I2C": ("SCL", "SDA"),
    "SPI": ("SCK", "MOSI", "MISO", "CS"),
    "UART": ("TX", "RX"),
    "CAN_CONTROLLER": ("TX", "RX"),
    "CAN": ("CANH", "CANL"),
    "USB": ("DP", "DM"),
    "SWD": ("SWDIO", "SWCLK", "NRST"),
    "GPIO": ("GPIO",),
}
_PIN_ALIASES: dict[str, tuple[str, ...]] = {
    "SCL": ("SCL", "SCLK"),
    "SDA": ("SDA",),
    "SCK": ("SCK", "SCLK", "CLK"),
    "MOSI": ("MOSI", "SDI"),
    "MISO": ("MISO", "SDO"),
    "CS": ("CS", "NSS", "SS"),
    "TX": ("TX", "TXD"),
    "RX": ("RX", "RXD"),
    "CANH": ("CANH",),
    "CANL": ("CANL",),
    "DP": ("DP", "D+", "USB_DP"),
    "DM": ("DM", "D-", "USB_DM"),
    "SWDIO": ("SWDIO",),
    "SWCLK": ("SWCLK",),
    "NRST": ("NRST", "RESET"),
    "GPIO": ("GPIO",),
}


class SchematicPlanningBlocked(ValueError):
    """A known unresolved engineering input, distinct from invalid or stale data."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


def _fp(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError(f"{field} is invalid")
    return value


def _known(value: object, field: str, maximum: int = 2048) -> str:
    checked = text(value, field, maximum)
    if checked.upper() in _KNOWN:
        raise ValueError(f"{field} cannot be UNKNOWN")
    return checked


def _refs(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    result = tuple(ident(item, field.removesuffix("s")) for item in value)
    if required and not result:
        raise ValueError(f"{field} cannot be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must be unique")
    return result


def _optional_ident(value: object, field: str) -> str | None:
    return None if value is None else ident(value, field)


def _optional_fp(value: object, field: str) -> str | None:
    return None if value is None else _fp(value, field)


def _texts(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    result = tuple(_known(item, field.removesuffix("s")) for item in value)
    if required and not result:
        raise ValueError(f"{field} cannot be empty")
    if len(result) != len({item.casefold() for item in result}):
        raise ValueError(f"{field} must be unique")
    return result


def _canonical(value: BaseModel, *, exclude: set[str]) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _finalize(cls: type[BaseModel], values: dict[str, object]) -> BaseModel:
    material = dict(values)
    material["fingerprint"] = _ZERO
    provisional = cls.model_construct(**material)
    exclude = {"fingerprint"}
    if cls.__name__ in {"SchematicPlanProposal", "SchematicPlan"}:
        exclude.add("status")
    material["fingerprint"] = _canonical(provisional, exclude=exclude)
    return cls.model_validate(material)


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _path(value: object) -> str:
    checked = text(value, "target_relative_path", 255).replace("\\", "/")
    parts = checked.split("/")
    if (
        checked.startswith("/")
        or ":" in checked
        or any(part in {"", ".", ".."} for part in parts)
        or not checked.casefold().endswith(".kicad_sch")
    ):
        raise ValueError("target_relative_path is unsafe")
    return checked


class SchematicPlanningContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class SchematicPlanningStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    BLOCKED = "BLOCKED"


class SchematicPlanDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SupportingConnectionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    REQUIREMENT_ONLY = "REQUIREMENT_ONLY"


class SchematicComponentInstance(SchematicPlanningContract):
    instance_id: str
    architecture_component_id: str
    hardware_slot_id: str
    design_truth_component_id: str
    manufacturer: str
    mpn: str
    symbol: str
    footprint: str
    quantity: int = Field(default=1, ge=1, le=1000)
    source_refs: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]

    @field_validator(
        "instance_id",
        "architecture_component_id",
        "hardware_slot_id",
        "design_truth_component_id",
        mode="before",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("manufacturer", "mpn", "symbol", "footprint", mode="before")
    @classmethod
    def _identity(cls, value: object, info) -> str:
        return _known(value, info.field_name, 1024)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> tuple[str, ...]:
        return _refs(value, "source_refs", required=True)

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def _evidence_fps(cls, value: object) -> tuple[str, ...]:
        return tuple(_fp(item, "evidence_fingerprint") for item in _refs(value, "evidence_fingerprints", required=True))


class SchematicSignal(SchematicPlanningContract):
    signal_id: str
    name: str
    semantic_role: str
    direction: str
    voltage_domain: str
    protocol: str
    source_component_id: str
    target_component_ids: tuple[str, ...]
    source_requirement_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("signal_id", "source_component_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("name", "semantic_role", "direction", "voltage_domain", "protocol", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _known(value, info.field_name, 512)

    @field_validator("target_component_ids", "source_requirement_refs", "source_refs", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        return _refs(value, info.field_name, required=True)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicSignal:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicSignal:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic signal fingerprint mismatch")
        if self.source_component_id in self.target_component_ids:
            raise ValueError("schematic signal source cannot be its target")
        return self


class SchematicPinBinding(SchematicPlanningContract):
    binding_id: str
    component_id: str
    architecture_component_id: str
    pin_number: str
    pin_name: str
    function: str
    pad_number: str | None = None
    signal_id: str | None = None
    intentionally_unconnected: bool = False
    unconnected_reason: str | None = None
    source_refs: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    fingerprint: str

    @field_validator("binding_id", "component_id", "architecture_component_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("pin_number", "pin_name", "function", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _known(value, info.field_name, 256)

    @field_validator("pad_number", "unconnected_reason", mode="before")
    @classmethod
    def _optional_text(cls, value: object, info) -> str | None:
        return None if value is None else _known(value, info.field_name, 1024)

    @field_validator("signal_id", mode="before")
    @classmethod
    def _signal(cls, value: object) -> str | None:
        return None if value is None else ident(value, "signal_id")

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> tuple[str, ...]:
        return _refs(value, "source_refs", required=True)

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def _evidence_fps(cls, value: object) -> tuple[str, ...]:
        return tuple(_fp(item, "evidence_fingerprint") for item in _refs(value, "evidence_fingerprints", required=True))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicPinBinding:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicPinBinding:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic pin binding fingerprint mismatch")
        if self.pad_number is None:
            raise ValueError("schematic pin binding requires pad mapping")
        if self.intentionally_unconnected:
            if self.signal_id is not None or self.unconnected_reason is None:
                raise ValueError("intentional NC requires reason and no signal")
        elif self.signal_id is None or self.unconnected_reason is not None:
            raise ValueError("schematic pin binding requires signal or typed NC")
        return self


class SchematicEndpoint(SchematicPlanningContract):
    component_id: str
    pin_number: str
    binding_id: str

    @field_validator("component_id", "binding_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("pin_number", mode="before")
    @classmethod
    def _pin(cls, value: object) -> str:
        return _known(value, "pin_number", 256)

    @property
    def node_ref(self) -> str:
        return f"{self.component_id}.{self.pin_number}"


class SchematicNet(SchematicPlanningContract):
    net_id: str
    name: str
    signal_id: str
    endpoints: tuple[SchematicEndpoint, ...]
    power_domain: str | None = None
    source_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("net_id", "signal_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("name", "power_domain", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str | None:
        return None if value is None else _known(value, info.field_name, 512)

    @field_validator("endpoints", mode="before")
    @classmethod
    def _endpoints(cls, value: object) -> tuple[SchematicEndpoint, ...]:
        if type(value) is list:
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("endpoints must be a tuple")
        return tuple(
            item if type(item) is SchematicEndpoint else SchematicEndpoint.model_validate(copy.deepcopy(item), strict=False)
            for item in value
        )

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> tuple[str, ...]:
        return _refs(value, "source_refs", required=True)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicNet:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicNet:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic net fingerprint mismatch")
        nodes = tuple(item.node_ref for item in self.endpoints)
        if len(nodes) < 2 or len(nodes) != len(set(nodes)):
            raise ValueError("schematic net requires unique endpoints")
        return self

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(item.node_ref for item in self.endpoints)


class SchematicPowerDomain(SchematicPlanningContract):
    domain_id: str
    name: str
    nominal_voltage: str
    source_component_ids: tuple[str, ...]
    consumer_component_ids: tuple[str, ...]
    ground_reference: str
    constraints: tuple[str, ...] = ()
    source_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("domain_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "domain_id")

    @field_validator("name", "nominal_voltage", "ground_reference", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _known(value, info.field_name, 512)

    @field_validator("source_component_ids", "consumer_component_ids", mode="before")
    @classmethod
    def _components(cls, value: object, info) -> tuple[str, ...]:
        return _refs(value, info.field_name)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value: object) -> tuple[str, ...]:
        return _texts(value, "constraints")

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> tuple[str, ...]:
        return _refs(value, "source_refs", required=True)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicPowerDomain:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicPowerDomain:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic power domain fingerprint mismatch")
        if not self.source_component_ids and self.name.casefold() != "gnd":
            raise ValueError("non-ground power domain requires source")
        if not self.consumer_component_ids and self.name.casefold() != "gnd":
            raise ValueError("non-ground power domain requires consumer")
        if set(self.source_component_ids) & set(self.consumer_component_ids):
            raise ValueError("power domain source and consumer overlap")
        return self


class SchematicInterfaceBus(SchematicPlanningContract):
    interface_id: str
    protocol: str
    participants: tuple[str, ...]
    signal_ids: tuple[str, ...]
    voltage_domain: str
    constraints: tuple[str, ...] = ()
    source_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("interface_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "interface_id")

    @field_validator("protocol", "voltage_domain", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return _known(value, info.field_name, 256)

    @field_validator("participants", "signal_ids", "source_refs", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        return _refs(value, info.field_name, required=True)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value: object) -> tuple[str, ...]:
        return _texts(value, "constraints")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicInterfaceBus:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicInterfaceBus:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic interface fingerprint mismatch")
        if len(self.participants) < 2:
            raise ValueError("schematic interface requires participants")
        return self


class SupportingComponentFulfillment(SchematicPlanningContract):
    fulfillment_id: str
    project_id: str
    conversation_id: str
    parent_slot_id: str
    parent_source_fingerprint: str
    requirement_ordinal: int = Field(ge=0, le=999)
    requirement_fingerprint: str
    expectation_ordinal: int = Field(ge=0, le=999)
    support_slot_id: str
    support_instance_id: str
    unit_ordinal: int = Field(ge=0, le=999)
    role: str
    quantity: int = Field(default=1, ge=1, le=1)
    manufacturer: str
    mpn: str
    source_id: str
    source_fingerprint: str
    target_pin_number: str
    target_net_name: str
    signal_pin_number: str
    signal_net_name: str
    return_pin_number: str
    return_net_name: str
    selection_proposal_id: str
    selection_proposal_fingerprint: str
    selection_approval_id: str
    selection_approval_fingerprint: str
    bom_id: str
    bom_fingerprint: str
    hardware_truth_id: str
    hardware_truth_fingerprint: str
    fingerprint: str

    @field_validator(
        "fulfillment_id", "project_id", "conversation_id", "parent_slot_id",
        "support_slot_id", "support_instance_id", "source_id", "selection_proposal_id",
        "selection_approval_id", "bom_id", "hardware_truth_id", mode="before",
    )
    @classmethod
    def _identities(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator(
        "parent_source_fingerprint", "requirement_fingerprint", "source_fingerprint",
        "selection_proposal_fingerprint", "selection_approval_fingerprint",
        "bom_fingerprint", "hardware_truth_fingerprint", "fingerprint", mode="before",
    )
    @classmethod
    def _fingerprints(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator(
        "role", "manufacturer", "mpn", "target_pin_number", "target_net_name",
        "signal_pin_number", "signal_net_name", "return_pin_number", "return_net_name",
        mode="before",
    )
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _known(value, info.field_name, 1024)

    @classmethod
    def create(cls, **values: object) -> SupportingComponentFulfillment:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SupportingComponentFulfillment:
        if (
            self.fingerprint != _canonical(self, exclude={"fingerprint"})
            or self.role not in {"I2C_PULLUPS", "DECOUPLING"}
            or self.target_net_name != self.signal_net_name
            or self.target_net_name == self.return_net_name
            or self.signal_pin_number == self.return_pin_number
            or self.unit_ordinal != self.expectation_ordinal
        ):
            raise ValueError("supporting component fulfillment binding mismatch")
        return self


class SchematicSupportingConnection(SchematicPlanningContract):
    connection_id: str
    requirement_role: str
    description: str
    status: SupportingConnectionStatus
    source_refs: tuple[str, ...]
    fulfillments: tuple[SupportingComponentFulfillment, ...] = Field(
        default=(), exclude_if=lambda value: not value,
    )
    fingerprint: str

    @field_validator("connection_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return ident(value, "connection_id")

    @field_validator("requirement_role", "description", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return _known(value, info.field_name, 2048)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _refs(cls, value: object) -> tuple[str, ...]:
        return _refs(value, "source_refs", required=True)

    @field_validator("fulfillments", mode="before")
    @classmethod
    def _fulfillments(cls, value: object) -> tuple[SupportingComponentFulfillment, ...]:
        if type(value) is list:
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError("fulfillments must be a tuple")
        return tuple(
            item if type(item) is SupportingComponentFulfillment
            else SupportingComponentFulfillment.model_validate(copy.deepcopy(item), strict=False)
            for item in value
        )

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicSupportingConnection:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicSupportingConnection:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic supporting connection fingerprint mismatch")
        if (self.status is SupportingConnectionStatus.RESOLVED) != bool(self.fulfillments):
            raise ValueError("supporting connection status and fulfillment mismatch")
        if len({item.support_slot_id for item in self.fulfillments}) != len(self.fulfillments):
            raise ValueError("supporting connection reuses a physical unit")
        if any(item.role != self.requirement_role for item in self.fulfillments):
            raise ValueError("supporting connection role mismatch")
        return self


class SchematicIntentionalUnconnected(SchematicPlanningContract):
    component_id: str
    architecture_component_id: str
    pin_number: str
    pin_name: str
    reason: str
    source_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("component_id", "architecture_component_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("pin_number", "pin_name", "reason", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _known(value, info.field_name, 1024)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _refs(cls, value: object) -> tuple[str, ...]:
        return _refs(value, "source_refs", required=True)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicIntentionalUnconnected:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicIntentionalUnconnected:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic intentional NC fingerprint mismatch")
        return self


class SchematicTarget(SchematicPlanningContract):
    project_id: str
    relative_path: str
    sheet_name: str
    eda_format: str
    component_instance_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("project_id", mode="before")
    @classmethod
    def _project(cls, value: object) -> str:
        return ident(value, "project_id")

    @field_validator("relative_path", mode="before")
    @classmethod
    def _relative_path(cls, value: object) -> str:
        return _path(value)

    @field_validator("sheet_name", "eda_format", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return _known(value, info.field_name, 256)

    @field_validator("component_instance_ids", "source_refs", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        return _refs(value, info.field_name, required=True)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return _fp(value, "fingerprint")

    @classmethod
    def create(cls, **values: object) -> SchematicTarget:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicTarget:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic target fingerprint mismatch")
        if self.eda_format != "KICAD_SCHEMATIC":
            raise ValueError("unsupported schematic target format")
        return self


class _SchematicPlanPayload(SchematicPlanningContract):
    plan_id: str
    project_id: str
    conversation_id: str
    architecture_id: str
    architecture_fingerprint: str
    requirements_fingerprint: str
    hardware_truth_id: str
    hardware_truth_fingerprint: str
    bom_id: str
    bom_fingerprint: str
    circuit_intent_id: str | None = None
    circuit_intent_revision: str | None = None
    circuit_intent_fingerprint: str | None = None
    target: SchematicTarget
    component_instances: tuple[SchematicComponentInstance, ...]
    signals: tuple[SchematicSignal, ...]
    pin_bindings: tuple[SchematicPinBinding, ...]
    nets: tuple[SchematicNet, ...]
    power_domains: tuple[SchematicPowerDomain, ...]
    interfaces: tuple[SchematicInterfaceBus, ...]
    supporting_connections: tuple[SchematicSupportingConnection, ...] = ()
    intentional_unconnected: tuple[SchematicIntentionalUnconnected, ...] = ()
    source_refs: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    status: SchematicPlanningStatus
    blocking_reasons: tuple[str, ...] = ()
    fingerprint: str

    @field_validator(
        "plan_id", "project_id", "conversation_id", "architecture_id", "hardware_truth_id", "bom_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("circuit_intent_id", mode="before")
    @classmethod
    def _circuit_intent_id(cls, value: object) -> str | None:
        return _optional_ident(value, "circuit_intent_id")

    @field_validator("circuit_intent_revision", mode="before")
    @classmethod
    def _circuit_intent_revision(cls, value: object) -> str | None:
        if value is None:
            return None
        checked = text(value, "circuit_intent_revision", 128)
        if not re.fullmatch(r"r[1-9][0-9]*", checked):
            raise ValueError("circuit_intent_revision is invalid")
        return checked

    @field_validator("circuit_intent_fingerprint", mode="before")
    @classmethod
    def _circuit_intent_fingerprint(cls, value: object) -> str | None:
        return _optional_fp(value, "circuit_intent_fingerprint")

    @field_validator(
        "architecture_fingerprint", "requirements_fingerprint", "hardware_truth_fingerprint", "bom_fingerprint", "fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator("component_instances", "signals", "pin_bindings", "nets", "power_domains", "interfaces", "supporting_connections", "intentional_unconnected", mode="before")
    @classmethod
    def _objects(cls, value: object, info) -> tuple:
        if type(value) is list:
            value = tuple(value)
        if type(value) is not tuple:
            raise TypeError(f"{info.field_name} must be a tuple")
        models = {
            "component_instances": SchematicComponentInstance,
            "signals": SchematicSignal,
            "pin_bindings": SchematicPinBinding,
            "nets": SchematicNet,
            "power_domains": SchematicPowerDomain,
            "interfaces": SchematicInterfaceBus,
            "supporting_connections": SchematicSupportingConnection,
            "intentional_unconnected": SchematicIntentionalUnconnected,
        }
        model = models[info.field_name]
        return tuple(
            item if type(item) is model else model.model_validate(copy.deepcopy(item), strict=False)
            for item in value
        )

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value: object) -> tuple[str, ...]:
        return _refs(value, "source_refs", required=True)

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def _evidence_fps_tuple(cls, value: object) -> tuple[str, ...]:
        return tuple(_fp(item, "evidence_fingerprint") for item in _refs(value, "evidence_fingerprints", required=True))

    @field_validator("blocking_reasons", mode="before")
    @classmethod
    def _blocking_reasons(cls, value: object) -> tuple[str, ...]:
        return _texts(value, "blocking_reasons")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> SchematicPlanningStatus:
        return value if isinstance(value, SchematicPlanningStatus) else SchematicPlanningStatus(value)

    @classmethod
    def create(cls, **values: object):
        return _finalize(cls, values)

    @model_validator(mode="after")
    def _verify_payload(self):
        if self.fingerprint != _canonical(self, exclude={"fingerprint", "status"}):
            raise ValueError("schematic plan fingerprint mismatch")
        intent_identity = (
            self.circuit_intent_id,
            self.circuit_intent_revision,
            self.circuit_intent_fingerprint,
        )
        if any(item is not None for item in intent_identity) and any(
            item is None for item in intent_identity
        ):
            raise ValueError("schematic plan circuit intent identity is incomplete")
        if not self.component_instances:
            raise ValueError("schematic plan requires component instances")
        instance_ids = tuple(item.instance_id for item in self.component_instances)
        component_ids = {item.design_truth_component_id for item in self.component_instances}
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("schematic plan instance ids must be unique")
        if len({item.signal_id for item in self.signals}) != len(self.signals):
            raise ValueError("schematic plan signal ids must be unique")
        if len({item.binding_id for item in self.pin_bindings}) != len(self.pin_bindings):
            raise ValueError("schematic plan pin binding ids must be unique")
        if len({item.net_id for item in self.nets}) != len(self.nets):
            raise ValueError("schematic plan net ids must be unique")
        if len({item.domain_id for item in self.power_domains}) != len(self.power_domains):
            raise ValueError("schematic plan power domain ids must be unique")
        if len({item.interface_id for item in self.interfaces}) != len(self.interfaces):
            raise ValueError("schematic plan interface ids must be unique")
        if set(self.target.component_instance_ids) != set(instance_ids):
            raise ValueError("schematic target instance binding mismatch")
        signal_ids = {item.signal_id for item in self.signals}
        binding_by_key = {(item.component_id, item.pin_number): item for item in self.pin_bindings}
        nc_keys = {(item.component_id, item.pin_number) for item in self.intentional_unconnected}
        if len(nc_keys) != len(self.intentional_unconnected):
            raise ValueError("schematic intentional NC identities must be unique")
        for binding in self.pin_bindings:
            if binding.component_id not in component_ids:
                raise ValueError("schematic pin component identity is invalid")
            if binding.signal_id is not None and binding.signal_id not in signal_ids:
                raise ValueError("schematic pin signal reference is invalid")
            if binding.intentionally_unconnected and (binding.component_id, binding.pin_number) not in nc_keys:
                raise ValueError("schematic NC binding is not declared")
        for item in self.intentional_unconnected:
            binding = binding_by_key.get((item.component_id, item.pin_number))
            if binding is None or not binding.intentionally_unconnected:
                raise ValueError("schematic NC declaration is not bound")
        domain_names = {item.name for item in self.power_domains}
        for net in self.nets:
            if net.signal_id not in signal_ids:
                raise ValueError("schematic net signal reference is invalid")
            for endpoint in net.endpoints:
                binding = binding_by_key.get((endpoint.component_id, endpoint.pin_number))
                if binding is None or binding.binding_id != endpoint.binding_id or binding.signal_id != net.signal_id:
                    raise ValueError("schematic net endpoint binding is invalid")
            if net.power_domain is not None and net.power_domain not in domain_names:
                raise ValueError("schematic net power domain is invalid")
        for domain in self.power_domains:
            if not set(domain.source_component_ids + domain.consumer_component_ids) <= component_ids:
                raise ValueError("schematic power domain component reference is invalid")
        for interface in self.interfaces:
            if not set(interface.participants) <= component_ids or not set(interface.signal_ids) <= signal_ids:
                raise ValueError("schematic interface reference is invalid")
        instances_by_slot = {item.hardware_slot_id: item for item in self.component_instances}
        nets_by_name = {item.name: item for item in self.nets}
        used_support_slots: set[str] = set()
        for connection in self.supporting_connections:
            for proof in connection.fulfillments:
                parent = instances_by_slot.get(proof.parent_slot_id)
                support = instances_by_slot.get(proof.support_slot_id)
                target_net = nets_by_name.get(proof.target_net_name)
                return_net = nets_by_name.get(proof.return_net_name)
                if (
                    proof.support_slot_id in used_support_slots
                    or proof.project_id != self.project_id
                    or proof.conversation_id != self.conversation_id
                    or proof.hardware_truth_id != self.hardware_truth_id
                    or proof.hardware_truth_fingerprint != self.hardware_truth_fingerprint
                    or proof.bom_id != self.bom_id
                    or proof.bom_fingerprint != self.bom_fingerprint
                    or parent is None or support is None
                    or support.design_truth_component_id != proof.support_instance_id
                    or support.manufacturer != proof.manufacturer
                    or support.mpn != proof.mpn
                    or proof.source_id not in support.source_refs
                    or proof.source_fingerprint not in support.evidence_fingerprints
                    or target_net is None or return_net is None
                    or not any(
                        item.component_id == parent.design_truth_component_id
                        and item.pin_number == proof.target_pin_number
                        for item in target_net.endpoints
                    )
                    or not any(
                        item.component_id == support.design_truth_component_id
                        and item.pin_number == proof.signal_pin_number
                        for item in target_net.endpoints
                    )
                    or not any(
                        item.component_id == support.design_truth_component_id
                        and item.pin_number == proof.return_pin_number
                        for item in return_net.endpoints
                    )
                ):
                    raise ValueError("schematic supporting fulfillment endpoint binding is invalid")
                used_support_slots.add(proof.support_slot_id)
        if self.status is SchematicPlanningStatus.BLOCKED and not self.blocking_reasons:
            raise ValueError("blocked schematic plan requires reasons")
        if self.status is not SchematicPlanningStatus.BLOCKED and self.blocking_reasons:
            raise ValueError("non-blocked schematic plan cannot contain blocking reasons")
        return self


class SchematicPlanProposal(_SchematicPlanPayload):
    @model_validator(mode="after")
    def _reviewable(self) -> SchematicPlanProposal:
        if self.status not in {SchematicPlanningStatus.DRAFT, SchematicPlanningStatus.REVIEW, SchematicPlanningStatus.BLOCKED}:
            raise ValueError("schematic plan proposal status is invalid")
        return self


class SchematicPlan(_SchematicPlanPayload):
    @model_validator(mode="after")
    def _approved(self) -> SchematicPlan:
        if self.status is not SchematicPlanningStatus.APPROVED:
            raise ValueError("schematic plan must be approved")
        return self


class SchematicPlanApproval(SchematicPlanningContract):
    approval_id: str
    proposal_id: str
    project_id: str
    architecture_fingerprint: str
    hardware_truth_fingerprint: str
    bom_fingerprint: str
    plan_fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    decision: SchematicPlanDecision
    reviewer: str
    decided_at: datetime
    fingerprint: str

    @field_validator("approval_id", "proposal_id", "project_id", "reviewer", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("architecture_fingerprint", "hardware_truth_fingerprint", "bom_fingerprint", "plan_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        return tuple(_fp(item, "evidence_fingerprint") for item in _refs(value, "evidence_fingerprints", required=True))

    @field_validator("decision", mode="before")
    @classmethod
    def _decision(cls, value: object) -> SchematicPlanDecision:
        return value if isinstance(value, SchematicPlanDecision) else SchematicPlanDecision(value)

    @field_validator("decided_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("decided_at is invalid")
        return value.astimezone(UTC)

    @classmethod
    def create(cls, **values: object) -> SchematicPlanApproval:
        return _finalize(cls, values)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _verify(self) -> SchematicPlanApproval:
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("schematic plan approval fingerprint mismatch")
        return self


class SchematicPlanningState(SchematicPlanningContract):
    proposal: SchematicPlanProposal
    status: SchematicPlanningStatus
    approval: SchematicPlanApproval | None = None
    approved_plan: SchematicPlan | None = None

    @model_validator(mode="after")
    def _verify(self) -> SchematicPlanningState:
        if self.status is SchematicPlanningStatus.APPROVED:
            if self.approval is None or self.approved_plan is None:
                raise ValueError("approved schematic planning state is incomplete")
            if self.proposal.status is not SchematicPlanningStatus.REVIEW:
                raise ValueError("approved schematic planning proposal is invalid")
            if self.approval.decision is not SchematicPlanDecision.APPROVED:
                raise ValueError("approved schematic planning decision is invalid")
            if (
                self.approval.proposal_id != self.proposal.plan_id
                or self.approved_plan.plan_id != self.proposal.plan_id
                or self.approval.project_id != self.proposal.project_id
                or self.approval.architecture_fingerprint != self.proposal.architecture_fingerprint
                or self.approval.hardware_truth_fingerprint != self.proposal.hardware_truth_fingerprint
                or self.approval.bom_fingerprint != self.proposal.bom_fingerprint
                or self.approval.plan_fingerprint != self.proposal.fingerprint
                or self.approved_plan.fingerprint != self.proposal.fingerprint
            ):
                raise ValueError("schematic planning approval binding mismatch")
            if self.approval.evidence_fingerprints != self.proposal.evidence_fingerprints:
                raise ValueError("schematic planning evidence binding mismatch")
        elif self.status is SchematicPlanningStatus.REJECTED:
            if self.approval is None or self.approval.decision is not SchematicPlanDecision.REJECTED:
                raise ValueError("rejected schematic planning state is invalid")
            if self.approved_plan is not None:
                raise ValueError("rejected schematic planning cannot contain approved plan")
        else:
            if self.proposal.status not in {SchematicPlanningStatus.DRAFT, SchematicPlanningStatus.REVIEW, SchematicPlanningStatus.BLOCKED}:
                raise ValueError("unapproved schematic planning proposal is invalid")
            if self.approval is not None or self.approved_plan is not None:
                raise ValueError("unapproved schematic planning cannot contain decision outputs")
        return self


def _copy(value: BaseModel, model: type[BaseModel]):
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")), strict=False)


def _component_map(
    hardware_truth: HardwareTruth,
    design_truth: DesignTruth,
) -> dict[str, tuple[HardwareTruthComponent, DesignComponent]]:
    result: dict[str, tuple[HardwareTruthComponent, DesignComponent]] = {}
    for hardware in hardware_truth.components:
        if hardware.supporting_for is not None:
            continue
        candidates = tuple(
            component
            for component in design_truth.components
            if component.name == hardware.mpn
            or component.description == f"{hardware.manufacturer} {hardware.mpn}"
        )
        if len(candidates) != 1:
            raise ValueError(f"hardware truth component DesignTruth mapping is ambiguous: {hardware.mpn}")
        component = candidates[0]
        if component.symbol_binding != hardware.symbol:
            raise ValueError(f"hardware component symbol mapping conflicts: {hardware.mpn}")
        result[hardware.architecture_component_id] = (hardware, component)
    return result


def _support_component_map(
    hardware_truth: HardwareTruth,
    design_truth: DesignTruth,
) -> dict[str, tuple[HardwareTruthComponent, DesignComponent]]:
    by_id = {item.component_id: item for item in design_truth.components}
    result: dict[str, tuple[HardwareTruthComponent, DesignComponent]] = {}
    for hardware in hardware_truth.components:
        if hardware.supporting_for is None:
            continue
        component_id = _id("design-component", design_truth.truth_id, hardware.slot_id)
        component = by_id.get(component_id)
        if (
            component is None or component.name != hardware.mpn
            or component.description != f"{hardware.manufacturer} {hardware.mpn}"
            or component.symbol_binding != hardware.symbol
        ):
            raise ValueError("supporting component DesignTruth slot mapping is stale")
        result[hardware.slot_id] = (hardware, component)
    return result


def _project_supply_selector_ids(truth_id: str, domain_name: str,
                                 ground_name: str) -> tuple[str, str, str]:
    domain_id = _id("schematic-power-domain", truth_id, domain_name)
    supply_net = _id("schematic-net", truth_id,
                     _id("schematic-signal", truth_id, domain_name))
    ground_net = _id("schematic-net", truth_id,
                     _id("schematic-signal", truth_id, ground_name))
    return domain_id, supply_net, ground_net


def _project_supply_net_options(hardware_truth: HardwareTruth,
                                design_truth: DesignTruth) -> tuple[dict[str, str], ...]:
    """Expose only current deterministic selectors, without creating a plan."""
    checked_hardware = _copy(hardware_truth, HardwareTruth)
    checked_truth = _copy(design_truth, DesignTruth)
    mapped = _component_map(checked_hardware, checked_truth)
    ground_names = {fact.domain for component, _truth in mapped.values()
                    for fact in component.power_facts if fact.domain.casefold() == "gnd"}
    if len(ground_names) != 1:
        return ()
    ground_name = next(iter(ground_names))
    roles: dict[str, set[str]] = {}
    for component, design_component in mapped.values():
        for fact in component.power_facts:
            if fact.domain.casefold() == "gnd":
                continue
            roles.setdefault(fact.domain, set()).add(fact.role.casefold())
            if not fact.pin_numbers or any(
                not any(pin.number == number and pin.is_power for pin in component.pin_facts)
                for number in fact.pin_numbers
            ):
                return ()
    if not roles or any(not {"source", "consumer"} <= values for values in roles.values()):
        return ()
    return tuple({"power_domain_id": domain_id, "domain_name": name,
                  "supply_net_ref": supply_net, "ground_net_ref": ground_net}
        for name in sorted(roles)
        for domain_id, supply_net, ground_net in (
            _project_supply_selector_ids(checked_hardware.truth_id, name, ground_name),))


def _pin_candidates(component: HardwareTruthComponent, protocol: str, alias: str) -> tuple:
    protocol_key = protocol.casefold()
    aliases = _PIN_ALIASES.get(alias, (alias,))
    interface_facts = tuple(
        item for item in component.interface_facts if item.protocol.casefold() == protocol_key
    )
    declared = {
        value.casefold()
        for item in interface_facts
        for value in item.pin_names
    }
    result = []
    for pin in component.pin_facts:
        if protocol_key not in {item.casefold() for item in pin.interface_protocols}:
            continue
        if protocol_key == "can_controller" and pin.function.strip().upper() != f"CAN_{alias}":
            continue
        if declared and pin.number.casefold() not in declared and pin.name.casefold() not in declared:
            continue
        normalized = f"{pin.name}_{pin.function}".upper().replace("-", "_").replace("/", "_")
        if any(candidate in normalized for candidate in aliases):
            result.append(pin)
    return tuple(sorted(result, key=lambda item: item.number))


def _requirement_refs(requirements: object) -> tuple[str, ...]:
    values = tuple(
        item.requirement_id
        for item in getattr(requirements, "requirements", ())
        if getattr(getattr(item, "status", None), "value", None) == "CONFIRMED"
    )
    return values or ("approved-requirement-context",)


def _voltage(fact) -> tuple[float, float]:
    minimum = fact.minimum_voltage if fact.minimum_voltage is not None else fact.nominal_voltage
    maximum = fact.maximum_voltage if fact.maximum_voltage is not None else fact.nominal_voltage
    if minimum is None or maximum is None:
        raise ValueError("consumer power range is invalid")
    return minimum, maximum


class SchematicPlanningService:
    """Deterministically plan connectivity from approved hardware facts."""

    def create_proposal(
        self,
        architecture: ArchitectureDesign,
        requirements: object,
        hardware_truth: HardwareTruth,
        bom: HardwareBOM,
        *,
        design_truth: DesignTruth,
        conversation_id: str,
        target_relative_path: str = "hardware/main.kicad_sch",
        circuit_intent: CircuitIntentObject | None = None,
        selection: HardwareSelectionState | None = None,
        _project_supply_relations: tuple[object, ...] | None = None,
        _project_supply_evidence_fingerprints: tuple[str, ...] = (),
        _plan_id: str | None = None,
    ) -> SchematicPlanProposal:
        checked_architecture = _copy(architecture, ArchitectureDesign)
        checked_hardware = _copy(hardware_truth, HardwareTruth)
        checked_bom = _copy(bom, HardwareBOM)
        checked_truth = _copy(design_truth, DesignTruth)
        checked_selection = (
            None if selection is None else _copy(selection, HardwareSelectionState)
        )
        checked_intent = (
            None
            if circuit_intent is None
            else _copy(circuit_intent, CircuitIntentObject)
        )
        if checked_architecture.status is not ArchitectureDesignStatus.APPROVED:
            raise ValueError("schematic planning requires approved architecture")
        if checked_intent is not None:
            if checked_intent.status not in {
                LifecycleStatus.APPROVED,
                LifecycleStatus.ACTIVE,
            }:
                raise ValueError("schematic planning requires an approved CircuitIntent snapshot")
            if checked_intent.project_id != checked_architecture.project_id:
                raise ValueError("schematic planning CircuitIntent project mismatch")
            for calculation in checked_intent.calculations:
                if calculation.status.upper() in {"EXECUTED", "VERIFIED"} and not all((
                    calculation.inputs,
                    calculation.outputs,
                    calculation.units,
                    calculation.evidence_refs,
                )):
                    raise ValueError("schematic planning executed calculation is incomplete")
            for claim in checked_intent.verification_requirements:
                if (
                    claim.status.upper() in {"VERIFIED", "PASS"}
                    and not claim.produced_evidence_refs
                ):
                    raise ValueError("schematic planning verified claim has no produced evidence")
        if (
            not isinstance(requirements, BaseModel)
            or requirements.__class__.__name__ != "RequirementContext"
            or requirements.__class__.__module__ != "embedded_copilot.engineering_requirements.models"
        ):
            raise TypeError("requirements must be RequirementContext")
        if (
            requirements.project_id != checked_architecture.project_id
            or requirements.conversation_id != conversation_id
        ):
            raise ValueError("schematic planning requirements binding mismatch")
        if checked_hardware.approval_fingerprint == _ZERO:
            raise ValueError("schematic planning requires approved hardware truth")
        if checked_selection is not None and (
            checked_selection.status is not HardwareSelectionStatus.APPROVED
            or checked_selection.approval is None
            or checked_selection.bom is None
            or checked_selection.hardware_truth is None
            or checked_selection.proposal.project_id != checked_hardware.project_id
            or checked_selection.proposal.conversation_id != conversation_id
            or checked_selection.approval.fingerprint != checked_hardware.approval_fingerprint
            or checked_selection.bom.fingerprint != checked_bom.fingerprint
            or checked_selection.hardware_truth.fingerprint != checked_hardware.fingerprint
        ):
            raise ValueError("schematic planning selection approval binding is stale")
        if (
            checked_architecture.project_id != checked_hardware.project_id
            or checked_hardware.project_id != checked_bom.project_id
            or checked_truth.project_id != checked_hardware.project_id
            or checked_truth.architecture_id != checked_architecture.architecture_id
            or conversation_id != checked_hardware.conversation_id
            or checked_bom.conversation_id != conversation_id
            or checked_bom.bom_id != checked_hardware.bom_id
            or checked_bom.fingerprint != checked_hardware.bom_fingerprint
            or checked_hardware.architecture_id != checked_architecture.architecture_id
            or checked_hardware.architecture_fingerprint != checked_architecture.fingerprint
        ):
            raise ValueError("schematic planning input identity mismatch")
        architecture_map = _component_map(checked_hardware, checked_truth)
        support_map = _support_component_map(checked_hardware, checked_truth)
        requirement_refs = _requirement_refs(requirements)
        instances = tuple(
            SchematicComponentInstance(
                instance_id=design_component.component_id,
                architecture_component_id=component.architecture_component_id,
                hardware_slot_id=component.slot_id,
                design_truth_component_id=design_component.component_id,
                manufacturer=component.manufacturer,
                mpn=component.mpn,
                symbol=component.symbol,
                footprint=component.footprint,
                quantity=component.quantity,
                source_refs=(component.source_id,),
                evidence_fingerprints=(component.source_fingerprint,),
            )
            for component, design_component in (*architecture_map.values(), *support_map.values())
        )
        parent_slots = {item.slot_id for item, _ in architecture_map.values()}
        instance_by_arch = {
            item.architecture_component_id: item
            for item in instances if item.hardware_slot_id in parent_slots
        }
        instance_by_slot = {item.hardware_slot_id: item for item in instances}
        binding_values: dict[tuple[str, str], SchematicPinBinding] = {}
        intentional: list[SchematicIntentionalUnconnected] = []
        signals: list[SchematicSignal] = []
        nets: list[SchematicNet] = []
        power_domains: list[SchematicPowerDomain] = []
        interface_buses: list[SchematicInterfaceBus] = []
        blocking: list[str] = []

        def add_binding(
            component: HardwareTruthComponent,
            pin,
            *,
            signal_id: str | None,
            source_refs: tuple[str, ...],
            nc_reason: str | None = None,
        ) -> SchematicPinBinding:
            key = (instance_by_slot[component.slot_id].design_truth_component_id, pin.number)
            if key in binding_values:
                previous = binding_values[key]
                if previous.signal_id != signal_id:
                    raise ValueError("pin conflict: exclusive pin assigned to incompatible signals")
                return previous
            binding = SchematicPinBinding.create(
                binding_id=_id("schematic-pin-binding", checked_hardware.truth_id, key[0], pin.number),
                component_id=key[0],
                architecture_component_id=component.architecture_component_id,
                pin_number=pin.number,
                pin_name=pin.name,
                function=pin.function,
                pad_number=pin.pad_number,
                signal_id=signal_id,
                intentionally_unconnected=nc_reason is not None,
                unconnected_reason=nc_reason,
                source_refs=source_refs,
                evidence_fingerprints=(component.source_fingerprint,),
            )
            binding_values[key] = binding
            if nc_reason is not None:
                intentional.append(
                    SchematicIntentionalUnconnected.create(
                        component_id=key[0],
                        architecture_component_id=component.architecture_component_id,
                        pin_number=pin.number,
                        pin_name=pin.name,
                        reason=nc_reason,
                        source_refs=source_refs,
                    )
                )
            return binding

        def add_signal(
            *,
            signal_id: str,
            name: str,
            role: str,
            direction: str,
            voltage_domain: str,
            protocol: str,
            source_component_id: str,
            target_component_ids: tuple[str, ...],
            source_refs: tuple[str, ...],
        ) -> SchematicSignal:
            signal = SchematicSignal.create(
                signal_id=signal_id,
                name=name,
                semantic_role=role,
                direction=direction,
                voltage_domain=voltage_domain,
                protocol=protocol,
                source_component_id=source_component_id,
                target_component_ids=target_component_ids,
                source_requirement_refs=requirement_refs,
                source_refs=source_refs,
            )
            signals.append(signal)
            return signal

        power_by_domain: dict[str, dict[str, list]] = {}
        for component, _design_component in architecture_map.values():
            declared_power_pins = {pin for fact in component.power_facts for pin in fact.pin_numbers}
            for fact in component.power_facts:
                domain = fact.domain
                entry = power_by_domain.setdefault(domain, {"sources": [], "consumers": [], "facts": [], "pins": []})
                entry["facts"].append((component, fact))
                if fact.role.casefold() == "source":
                    entry["sources"].append(component)
                elif fact.role.casefold() == "consumer":
                    entry["consumers"].append(component)
                else:
                    entry["consumers"].append(component)
                for pin_number in fact.pin_numbers:
                    pin = next((item for item in component.pin_facts if item.number == pin_number), None)
                    if pin is None or not pin.is_power:
                        raise ValueError("power fact references unknown or non-power pin")
                    entry["pins"].append((component, pin))
            for pin in component.pin_facts:
                if pin.is_power and pin.number not in declared_power_pins:
                    raise ValueError("required power pin is missing power fact")

        approved_supply = None
        ground_names = tuple(name for name in power_by_domain if name.casefold() == "gnd")
        if _project_supply_relations is not None:
            if len(ground_names) != 1:
                raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
            approved_supply = {item.power_domain_id: item for item in _project_supply_relations}
            if len(approved_supply) != len(_project_supply_relations):
                raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
        checked_supply_ids: set[str] = set()
        for domain_name, entry in sorted(power_by_domain.items()):
            facts = entry["facts"]
            source_facts = [(component, fact) for component, fact in facts if fact.role.casefold() == "source"]
            consumer_facts = [(component, fact) for component, fact in facts if fact.role.casefold() == "consumer"]
            if domain_name.casefold() != "gnd" and (not source_facts or not consumer_facts):
                raise SchematicPlanningBlocked(
                    "POWER_SOURCE_UNRESOLVED",
                    f"power domain {domain_name} lacks explicit source or consumer",
                )
            if source_facts:
                nominal = source_facts[0][1].nominal_voltage
                if nominal is None:
                    raise ValueError(f"power domain {domain_name} source nominal is invalid")
                for _component, fact in source_facts[1:]:
                    if fact.nominal_voltage != nominal:
                        raise ValueError(f"voltage conflict in power domain {domain_name}")
                for component, fact in consumer_facts:
                    minimum, maximum = _voltage(fact)
                    if nominal < minimum or nominal > maximum:
                        raise ValueError(f"voltage conflict: {component.mpn} cannot consume {domain_name}")
                nominal_text = f"{nominal:g}V"
            else:
                nominal_text = "0V"
            if approved_supply is not None and domain_name.casefold() != "gnd":
                domain_id, expected_supply_net, expected_ground_net = _project_supply_selector_ids(
                    checked_hardware.truth_id, domain_name, ground_names[0])
                relation = approved_supply.get(domain_id)
                source_components = tuple(dict.fromkeys(instance_by_arch[item.architecture_component_id].design_truth_component_id
                    for item, _fact in source_facts))
                consumer_components = tuple(dict.fromkeys(instance_by_arch[item.architecture_component_id].design_truth_component_id
                    for item, _fact in consumer_facts))
                if (relation is None or tuple(relation.source_component_refs) != source_components
                    or tuple(relation.consumer_component_refs) != consumer_components
                    or relation.supply_net_ref != expected_supply_net
                    or relation.ground_net_ref != expected_ground_net
                    or Decimal(relation.nominal_voltage_v) != Decimal(str(nominal))):
                    raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")
                nominal_text = f"{relation.nominal_voltage_v} V"
                checked_supply_ids.add(domain_id)
            source_instance_id = (
                instance_by_arch[entry["sources"][0].architecture_component_id].design_truth_component_id
                if entry["sources"]
                else instance_by_arch[entry["consumers"][0].architecture_component_id].design_truth_component_id
            )
            target_instance_ids = tuple(
                dict.fromkeys(
                    instance_by_arch[item.architecture_component_id].design_truth_component_id
                    for item, _fact in facts
                    if instance_by_arch[item.architecture_component_id].design_truth_component_id != source_instance_id
                )
            )
            signal = add_signal(
                signal_id=_id("schematic-signal", checked_hardware.truth_id, domain_name),
                name=domain_name,
                role="POWER" if domain_name.casefold() != "gnd" else "GROUND",
                direction="PASSIVE",
                voltage_domain=domain_name,
                protocol="POWER",
                source_component_id=source_instance_id,
                target_component_ids=target_instance_ids,
                source_refs=tuple(dict.fromkeys(item.source_id for item, _fact in facts)),
            )
            domain_endpoints: list[SchematicEndpoint] = []
            source_ids: list[str] = []
            consumer_ids: list[str] = []
            for component, pin in entry["pins"]:
                instance_id = instance_by_arch[component.architecture_component_id].design_truth_component_id
                binding = add_binding(
                    component,
                    pin,
                    signal_id=signal.signal_id,
                    source_refs=(component.source_id, domain_name),
                )
                domain_endpoints.append(SchematicEndpoint(component_id=instance_id, pin_number=pin.number, binding_id=binding.binding_id))
                for _holder, fact in facts:
                    if component is _holder and pin.number in fact.pin_numbers:
                        if fact.role.casefold() == "source":
                            source_ids.append(instance_id)
                        else:
                            consumer_ids.append(instance_id)
            if len(domain_endpoints) < 2:
                raise SchematicPlanningBlocked(
                    "POWER_NET_INCOMPLETE", f"required power net {domain_name} is incomplete",
                )
            power_domains.append(
                SchematicPowerDomain.create(
                    domain_id=_id("schematic-power-domain", checked_hardware.truth_id, domain_name),
                    name=domain_name,
                    nominal_voltage=nominal_text,
                    source_component_ids=tuple(dict.fromkeys(source_ids)),
                    consumer_component_ids=tuple(dict.fromkeys(consumer_ids)),
                    ground_reference="GND",
                    constraints=tuple(dict.fromkeys(item for _component, fact in facts for item in fact.constraints)),
                    source_refs=tuple(dict.fromkeys(item.source_id for item, _fact in facts)),
                )
            )
            nets.append(
                SchematicNet.create(
                    net_id=_id("schematic-net", checked_hardware.truth_id, signal.signal_id),
                    name=domain_name,
                    signal_id=signal.signal_id,
                    endpoints=tuple(domain_endpoints),
                    power_domain=domain_name,
                    source_refs=tuple(dict.fromkeys(item.source_id for item, _fact in facts)),
                )
            )

        if approved_supply is not None and checked_supply_ids != set(approved_supply):
            raise SchematicPlanningBlocked("POWER_SOURCE_UNRESOLVED")

        hardware_interfaces = tuple(
            item
            for item in checked_architecture.interfaces
            if item.interface_type is ArchitectureInterfaceType.HARDWARE
        )
        interface_index: dict[str, int] = {}
        for architecture_interface in hardware_interfaces:
            protocol = architecture_interface.protocol.strip().upper()
            if protocol in {"UNKNOWN", ""}:
                raise SchematicPlanningBlocked(
                    "INTERFACE_PROTOCOL_UNRESOLVED", "hardware interface protocol is unknown",
                )
            if protocol == "POWER":
                continue
            aliases = _PROTOCOL_SIGNALS.get(protocol)
            if aliases is None:
                raise ValueError(f"unsupported hardware interface protocol: {protocol}")
            participants_arch = (
                architecture_interface.source_component_id,
                architecture_interface.target_component_id,
            )
            if any(item not in instance_by_arch for item in participants_arch):
                raise ValueError("hardware interface participant is not selected")
            index = interface_index.get(protocol, 0) + 1
            interface_index[protocol] = index
            component_by_arch = {item.architecture_component_id: item for item, _ in architecture_map.values()}
            participant_instances = tuple(instance_by_arch[item].design_truth_component_id for item in participants_arch)
            voltage_domains = {
                fact.domain
                for architecture_component_id in participants_arch
                for fact in component_by_arch[architecture_component_id].power_facts
                if fact.role.casefold() == "consumer"
            }
            common_voltage = sorted(voltage_domains & {item.name for item in power_domains})
            if not common_voltage:
                raise ValueError(f"interface {protocol} has no explicit common voltage domain")
            signal_ids: list[str] = []
            for alias in aliases:
                signal_id = _id("schematic-signal", checked_hardware.truth_id, protocol, str(index), alias)
                if index == 1 and protocol == "CAN_CONTROLLER":
                    name = f"CAN_{alias}"
                elif index == 1 and protocol == "CAN":
                    name = alias
                else:
                    name = f"{protocol}{index}_{alias}"
                controller_arch = next(
                    (item for item in participants_arch if instance_by_arch[item].architecture_component_id and component_by_arch[item].role == "CONTROLLER"),
                    participants_arch[0],
                )
                source_id = instance_by_arch[controller_arch].design_truth_component_id
                target_ids = tuple(item for item in participant_instances if item != source_id)
                signal = add_signal(
                    signal_id=signal_id,
                    name=name,
                    role="INTERFACE",
                    direction="BIDIRECTIONAL" if protocol in {"I2C", "CAN", "USB", "SWD"} else "SIGNAL",
                    voltage_domain=common_voltage[0],
                    protocol=protocol,
                    source_component_id=source_id,
                    target_component_ids=target_ids,
                    source_refs=(architecture_interface.interface_id,),
                )
                signal_ids.append(signal.signal_id)
                endpoint_list: list[SchematicEndpoint] = []
                for architecture_component_id in participants_arch:
                    component = component_by_arch[architecture_component_id]
                    candidates = _pin_candidates(component, protocol, alias)
                    if len(candidates) != 1:
                        raise ValueError(f"required interface pin is missing or ambiguous: {protocol} {alias}")
                    pin = candidates[0]
                    binding = add_binding(
                        component,
                        pin,
                        signal_id=signal.signal_id,
                        source_refs=(architecture_interface.interface_id, component.source_id),
                    )
                    endpoint_list.append(
                        SchematicEndpoint(
                            component_id=instance_by_arch[architecture_component_id].design_truth_component_id,
                            pin_number=pin.number,
                            binding_id=binding.binding_id,
                        )
                    )
                nets.append(
                    SchematicNet.create(
                        net_id=_id("schematic-net", checked_hardware.truth_id, signal.signal_id),
                        name=signal.name,
                        signal_id=signal.signal_id,
                        endpoints=tuple(endpoint_list),
                        source_refs=(architecture_interface.interface_id,),
                    )
                )
            interface_buses.append(
                SchematicInterfaceBus.create(
                    interface_id=_id("schematic-interface", checked_hardware.truth_id, architecture_interface.interface_id),
                    protocol=protocol,
                    participants=participant_instances,
                    signal_ids=tuple(signal_ids),
                    voltage_domain=common_voltage[0],
                    constraints=(),
                    source_refs=(architecture_interface.interface_id,),
                )
            )

        for component, _design_component in architecture_map.values():
            instance_id = instance_by_arch[component.architecture_component_id].design_truth_component_id
            for pin in component.pin_facts:
                key = (instance_id, pin.number)
                if key in binding_values:
                    continue
                normalized = pin.function.casefold()
                if normalized in {"nc", "unused", "reserved", "intentionally_unconnected"}:
                    add_binding(
                        component,
                        pin,
                        signal_id=None,
                        source_refs=(component.source_id,),
                        nc_reason=f"Evidence marks {pin.name} as intentionally unconnected.",
                    )
                else:
                    raise SchematicPlanningBlocked(
                        "PIN_BINDING_UNRESOLVED",
                        f"required pin is unassigned: {component.mpn}.{pin.number}",
                    )

        selected_by_slot = (
            {} if checked_selection is None else {
                item.slot_id: item for item in checked_selection.proposal.selected_components
            }
        )
        bom_by_slot = {item.slot_id: item for item in checked_bom.items}
        support_by_key = {
            (
                component.supporting_for.parent_slot_id,
                component.supporting_for.requirement_ordinal,
                component.supporting_for.expectation_ordinal,
            ): component
            for component in checked_hardware.components
            if component.supporting_for is not None
        }
        used_support_slots: set[str] = set()
        supporting_connections: list[SchematicSupportingConnection] = []

        def attach_support_endpoint(component: HardwareTruthComponent, pin, net_name: str) -> None:
            net_index = next(index for index, item in enumerate(nets) if item.name == net_name)
            net = nets[net_index]
            instance_id = instance_by_slot[component.slot_id].design_truth_component_id
            binding = add_binding(
                component, pin, signal_id=net.signal_id,
                source_refs=(component.source_id, net.name),
            )
            endpoint = SchematicEndpoint(
                component_id=instance_id, pin_number=pin.number, binding_id=binding.binding_id,
            )
            nets[net_index] = SchematicNet.create(**{
                **net.model_dump(mode="python"),
                "endpoints": (*net.endpoints, endpoint),
                "source_refs": tuple(dict.fromkeys((*net.source_refs, component.source_id))),
            })
            signal_index = next(index for index, item in enumerate(signals) if item.signal_id == net.signal_id)
            signal = signals[signal_index]
            signals[signal_index] = SchematicSignal.create(**{
                **signal.model_dump(mode="python"),
                "target_component_ids": tuple(dict.fromkeys((*signal.target_component_ids, instance_id))),
                "source_refs": tuple(dict.fromkeys((*signal.source_refs, component.source_id))),
            })

        for parent in checked_hardware.components:
            if parent.supporting_for is not None:
                continue
            for requirement_ordinal, requirement in enumerate(parent.supporting_requirements):
                digest = _canonical(requirement, exclude=set())
                resolved: list[SupportingComponentFulfillment] = []
                candidates: list[tuple[HardwareTruthComponent, object, object, object]] = []
                eligible = (
                    checked_selection is not None
                    and requirement.role in {"I2C_PULLUPS", "DECOUPLING"}
                    and bool(requirement.connection_expectations)
                    and requirement.quantity == len(requirement.connection_expectations)
                )
                if eligible:
                    for expectation_ordinal, expectation in enumerate(requirement.connection_expectations):
                        support = support_by_key.get((parent.slot_id, requirement_ordinal, expectation_ordinal))
                        if support is None or support.slot_id in used_support_slots:
                            eligible = False
                            break
                        link = support.supporting_for
                        selected = selected_by_slot.get(support.slot_id)
                        bom_item = bom_by_slot.get(support.slot_id)
                        instance = instance_by_slot.get(support.slot_id)
                        expected_slot = _id(
                            "support-slot", checked_hardware.project_id, parent.slot_id,
                            parent.source_fingerprint, str(requirement_ordinal), digest,
                            str(expectation_ordinal),
                        )
                        if (
                            link is None or support.slot_id != expected_slot
                            or link.parent_source_fingerprint != parent.source_fingerprint
                            or link.requirement_fingerprint != digest
                            or support.role != requirement.role or support.quantity != 1
                            or selected is None or selected.supporting_for != link
                            or selected.quantity != 1 or selected.role != requirement.role
                            or requirement.role not in selected.source.supported_roles
                            or selected.source.fingerprint != support.source_fingerprint
                            or selected.source.source_id != support.source_id
                            or bom_item is None or bom_item.supporting_for != link
                            or bom_item.quantity != 1 or bom_item.role != requirement.role
                            or bom_item.mpn != support.mpn
                            or support.source_fingerprint not in bom_item.evidence_fingerprints
                            or instance is None or instance.hardware_slot_id != support.slot_id
                        ):
                            eligible = False
                            break
                        target_nets = tuple(item for item in nets if item.name == expectation.target_net_name)
                        return_nets = tuple(item for item in nets if item.name == expectation.return_net_name)
                        parent_id = instance_by_slot[parent.slot_id].design_truth_component_id
                        if (
                            len(target_nets) != 1 or len(return_nets) != 1
                            or expectation.signal_net_name != expectation.target_net_name
                            or not any(
                                endpoint.component_id == parent_id
                                and endpoint.pin_number == expectation.target_pin_number
                                for endpoint in target_nets[0].endpoints
                            )
                            or (
                                requirement.role == "I2C_PULLUPS"
                                and (
                                    next((item.protocol for item in signals if item.signal_id == target_nets[0].signal_id), None) != "I2C"
                                    or return_nets[0].power_domain in {None, "GND"}
                                )
                            )
                            or (
                                requirement.role == "DECOUPLING"
                                and (target_nets[0].power_domain is None or expectation.return_net_name != "GND")
                            )
                        ):
                            eligible = False
                            break
                        pins = {item.number: item for item in support.pin_facts}
                        signal_pin = pins.get(expectation.signal_pin_number)
                        return_pin = pins.get(expectation.return_pin_number)
                        if (
                            signal_pin is None or return_pin is None or signal_pin is return_pin
                            or (instance.design_truth_component_id, signal_pin.number) in binding_values
                            or (instance.design_truth_component_id, return_pin.number) in binding_values
                            or any(item[0].slot_id == support.slot_id for item in candidates)
                        ):
                            eligible = False
                            break
                        candidates.append((support, expectation, signal_pin, return_pin))
                if eligible and len(candidates) == requirement.quantity:
                    assert checked_selection is not None and checked_selection.approval is not None
                    for expectation_ordinal, (support, expectation, signal_pin, return_pin) in enumerate(candidates):
                        attach_support_endpoint(support, signal_pin, expectation.signal_net_name)
                        attach_support_endpoint(support, return_pin, expectation.return_net_name)
                        used_support_slots.add(support.slot_id)
                        link = support.supporting_for
                        assert link is not None
                        resolved.append(SupportingComponentFulfillment.create(
                            fulfillment_id=_id(
                                "support-fulfillment", checked_hardware.truth_id,
                                parent.slot_id, support.slot_id,
                            ),
                            project_id=checked_hardware.project_id,
                            conversation_id=conversation_id,
                            parent_slot_id=parent.slot_id,
                            parent_source_fingerprint=parent.source_fingerprint,
                            requirement_ordinal=requirement_ordinal,
                            requirement_fingerprint=digest,
                            expectation_ordinal=expectation_ordinal,
                            support_slot_id=support.slot_id,
                            support_instance_id=instance_by_slot[support.slot_id].design_truth_component_id,
                            unit_ordinal=expectation_ordinal,
                            role=requirement.role, quantity=1,
                            manufacturer=support.manufacturer, mpn=support.mpn,
                            source_id=support.source_id, source_fingerprint=support.source_fingerprint,
                            target_pin_number=expectation.target_pin_number,
                            target_net_name=expectation.target_net_name,
                            signal_pin_number=expectation.signal_pin_number,
                            signal_net_name=expectation.signal_net_name,
                            return_pin_number=expectation.return_pin_number,
                            return_net_name=expectation.return_net_name,
                            selection_proposal_id=checked_selection.proposal.proposal_id,
                            selection_proposal_fingerprint=checked_selection.proposal.fingerprint,
                            selection_approval_id=checked_selection.approval.approval_id,
                            selection_approval_fingerprint=checked_selection.approval.fingerprint,
                            bom_id=checked_bom.bom_id, bom_fingerprint=checked_bom.fingerprint,
                            hardware_truth_id=checked_hardware.truth_id,
                            hardware_truth_fingerprint=checked_hardware.fingerprint,
                        ))
                else:
                    blocking.append("BLOCKED_SUPPORTING_COMPONENT_SELECTION")
                connection_parts = (
                    (str(requirement_ordinal), digest)
                    if requirement.connection_expectations else ()
                )
                supporting_connections.append(SchematicSupportingConnection.create(
                    connection_id=_id(
                        "schematic-supporting", checked_hardware.truth_id,
                        parent.slot_id, requirement.role, *connection_parts,
                    ),
                    requirement_role=requirement.role,
                    description=requirement.description,
                    status=(
                        SupportingConnectionStatus.RESOLVED if resolved
                        else SupportingConnectionStatus.REQUIREMENT_ONLY
                    ),
                    source_refs=(parent.source_id, *requirement.evidence_refs),
                    fulfillments=tuple(resolved),
                ))

        source_refs = tuple(
            dict.fromkeys(
                (
                    checked_architecture.architecture_id,
                    checked_hardware.truth_id,
                    checked_bom.bom_id,
                    *((checked_intent.object_id,) if checked_intent is not None else ()),
                    *requirement_refs,
                    *(item.source_id for item in checked_hardware.components),
                )
            )
        )
        evidence_fingerprints = tuple(
            dict.fromkeys(
                (
                    checked_architecture.fingerprint,
                    checked_hardware.fingerprint,
                    checked_bom.fingerprint,
                    *(
                        (checked_intent.fingerprint,)
                        if checked_intent is not None
                        else ()
                    ),
                    *(_fp(item, "project_supply_evidence_fingerprint")
                      for item in _project_supply_evidence_fingerprints),
                )
            )
        )
        proposal = SchematicPlanProposal.create(
            plan_id=(ident(_plan_id, "plan_id") if _plan_id is not None else
                     _id("schematic-plan", checked_hardware.truth_id, checked_bom.fingerprint, target_relative_path)),
            project_id=checked_hardware.project_id,
            conversation_id=conversation_id,
            architecture_id=checked_architecture.architecture_id,
            architecture_fingerprint=checked_architecture.fingerprint,
            requirements_fingerprint=requirements.fingerprint,
            hardware_truth_id=checked_hardware.truth_id,
            hardware_truth_fingerprint=checked_hardware.fingerprint,
            bom_id=checked_bom.bom_id,
            bom_fingerprint=checked_bom.fingerprint,
            circuit_intent_id=(None if checked_intent is None else checked_intent.object_id),
            circuit_intent_revision=(None if checked_intent is None else checked_intent.revision),
            circuit_intent_fingerprint=(None if checked_intent is None else checked_intent.fingerprint),
            target=SchematicTarget.create(
                project_id=checked_hardware.project_id,
                relative_path=target_relative_path,
                sheet_name="main",
                eda_format="KICAD_SCHEMATIC",
                component_instance_ids=tuple(item.instance_id for item in instances),
                source_refs=source_refs,
            ),
            component_instances=instances,
            signals=tuple(signals),
            pin_bindings=tuple(binding_values.values()),
            nets=tuple(nets),
            power_domains=tuple(power_domains),
            interfaces=tuple(interface_buses),
            supporting_connections=tuple(supporting_connections),
            intentional_unconnected=tuple(intentional),
            source_refs=source_refs,
            evidence_fingerprints=evidence_fingerprints,
            status=SchematicPlanningStatus.BLOCKED if blocking else SchematicPlanningStatus.REVIEW,
            blocking_reasons=tuple(dict.fromkeys(blocking)),
        )
        return proposal

    def create_model_candidate_proposal(
        self,
        architecture: ArchitectureDesign,
        requirements: object,
        hardware_truth: HardwareTruth,
        bom: HardwareBOM,
        *,
        design_truth: DesignTruth,
        conversation_id: str,
        review_items: tuple[tuple[str, tuple[str, ...]], ...],
        target_relative_path: str = "hardware/main.kicad_sch",
        circuit_intent: CircuitIntentObject | None = None,
        selection: HardwareSelectionState | None = None,
        _project_supply_relations: tuple[object, ...] | None = None,
        _project_supply_evidence_fingerprints: tuple[str, ...] = (),
    ) -> tuple[SchematicPlanProposal, tuple[tuple[str, tuple[str, ...]], ...]]:
        """Check advisory IDs against a fresh deterministic plan without changing it."""
        proposal = self.create_proposal(
            architecture, requirements, hardware_truth, bom,
            design_truth=design_truth,
            conversation_id=conversation_id,
            target_relative_path=target_relative_path,
            circuit_intent=circuit_intent,
            selection=selection,
            _project_supply_relations=_project_supply_relations,
            _project_supply_evidence_fingerprints=_project_supply_evidence_fingerprints,
        )
        if type(review_items) is not tuple or len(review_items) > len(proposal.nets):
            raise ValueError("schematic model review items are invalid")
        net_by_id = {item.net_id: item for item in proposal.nets}
        checked: list[tuple[str, tuple[str, ...]]] = []
        seen: set[str] = set()
        for item in review_items:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("schematic model review item is invalid")
            net_id = ident(item[0], "net_id")
            binding_ids = _refs(item[1], "binding_ids", required=True)
            net = net_by_id.get(net_id)
            if net is None or net_id in seen:
                raise ValueError("schematic model review net is not current and unique")
            if set(binding_ids) != {endpoint.binding_id for endpoint in net.endpoints}:
                raise ValueError("schematic model review endpoints do not match current plan")
            seen.add(net_id)
            checked.append((net_id, binding_ids))
        return proposal, tuple(checked)

    def decide(
        self,
        proposal: SchematicPlanProposal,
        decision: SchematicPlanDecision,
        *,
        reviewer: str,
        decided_at: datetime,
    ) -> SchematicPlanningState:
        checked = _copy(proposal, SchematicPlanProposal)
        target = decision if isinstance(decision, SchematicPlanDecision) else SchematicPlanDecision(decision)
        if checked.status is not SchematicPlanningStatus.REVIEW:
            raise ValueError("schematic plan proposal is not reviewable")
        approval = SchematicPlanApproval.create(
            approval_id=_id("schematic-plan-approval", checked.plan_id),
            proposal_id=checked.plan_id,
            project_id=checked.project_id,
            architecture_fingerprint=checked.architecture_fingerprint,
            hardware_truth_fingerprint=checked.hardware_truth_fingerprint,
            bom_fingerprint=checked.bom_fingerprint,
            plan_fingerprint=checked.fingerprint,
            evidence_fingerprints=checked.evidence_fingerprints,
            decision=target,
            reviewer=reviewer,
            decided_at=decided_at,
        )
        if target is SchematicPlanDecision.REJECTED:
            return SchematicPlanningState(
                proposal=checked,
                status=SchematicPlanningStatus.REJECTED,
                approval=approval,
            )
        approved = SchematicPlan.model_validate(
            {**checked.model_dump(mode="python"), "status": SchematicPlanningStatus.APPROVED},
            strict=False,
        )
        return SchematicPlanningState(
            proposal=checked,
            status=SchematicPlanningStatus.APPROVED,
            approval=approval,
            approved_plan=approved,
        )

    def project_design_truth(
        self,
        base_truth: DesignTruth,
        plan: SchematicPlan,
    ) -> DesignTruth:
        checked_base = _copy(base_truth, DesignTruth)
        checked_plan = _copy(plan, SchematicPlan)
        if checked_plan.status is not SchematicPlanningStatus.APPROVED:
            raise ValueError("only approved schematic plan can update DesignTruth")
        if checked_base.project_id != checked_plan.project_id or checked_base.architecture_id != checked_plan.architecture_id:
            raise ValueError("schematic plan DesignTruth identity mismatch")
        component_ids = {item.design_truth_component_id for item in checked_plan.component_instances}
        if component_ids != {item.component_id for item in checked_base.components}:
            raise ValueError("schematic plan component identity mismatch")
        old_root = checked_base.truth_id
        preserved_bindings = tuple(item for item in checked_base.evidence_bindings if item.entity_id != old_root)
        truth_id = _id("design-truth", checked_plan.plan_id, checked_plan.fingerprint)
        new_bindings: list[DesignEvidenceBinding] = []

        architecture_binding = next(
            (
                item
                for item in checked_base.evidence_bindings
                if item.entity_id == old_root
                and item.source is DesignSource.ARCHITECTURE
                and item.source_id == checked_plan.architecture_id
            ),
            None,
        )
        if architecture_binding is None:
            raise ValueError("base DesignTruth architecture evidence is unavailable")
        new_bindings.append(
            DesignEvidenceBinding.create(
                binding_id=_id("design-evidence", truth_id, "architecture"),
                entity_id=truth_id,
                project_id=checked_plan.project_id,
                source=DesignSource.ARCHITECTURE,
                source_id=checked_plan.architecture_id,
                artifact_ref=architecture_binding.artifact_ref,
                locator=architecture_binding.locator,
                artifact_revision=architecture_binding.artifact_revision,
                source_fingerprint=checked_plan.architecture_fingerprint,
                relationship="preserves approved architecture binding",
                truth_level=TruthLevel.EXPLICIT,
            )
        )

        def evidence(entity_id: str, source_id: str, locator: str, relationship: str) -> str:
            binding_id = _id("design-evidence", truth_id, entity_id, source_id, locator)
            new_bindings.append(
                DesignEvidenceBinding.create(
                    binding_id=binding_id,
                    entity_id=entity_id,
                    project_id=checked_plan.project_id,
                    source=DesignSource.SCHEMATIC,
                    source_id=checked_plan.plan_id,
                    artifact_ref=checked_plan.target.relative_path,
                    locator=locator,
                    artifact_revision=checked_plan.fingerprint,
                    source_fingerprint=checked_plan.fingerprint,
                    relationship=relationship,
                    truth_level=TruthLevel.EXPLICIT,
                )
            )
            return binding_id

        signals = tuple(
            DesignSignal.create(
                signal_id=item.signal_id,
                name=item.name,
                type=item.semantic_role,
                source=item.source_component_id,
                targets=item.target_component_ids,
                protocol=item.protocol,
                direction=item.direction,
                confidence=1.0,
                truth_level=TruthLevel.EXPLICIT,
                evidence_refs=(evidence(item.signal_id, item.signal_id, "signal", "approved schematic signal"),),
            )
            for item in checked_plan.signals
        )
        binding_by_key = {(item.component_id, item.pin_number): item for item in checked_plan.pin_bindings}
        pins: list[DesignPin] = []
        base_by_key = {(item.component_id, item.pin_number): item for item in checked_base.pins}
        for key, binding in binding_by_key.items():
            previous = base_by_key.get(key)
            pin_id = previous.pin_id if previous is not None else _id("design-pin", truth_id, *key)
            binding_ref = evidence(pin_id, binding.binding_id, f"pin:{binding.component_id}.{binding.pin_number}", "approved schematic pin binding")
            pins.append(
                DesignPin.create(
                    pin_id=pin_id,
                    component_id=binding.component_id,
                    pin_name=binding.pin_name,
                    pin_number=binding.pin_number,
                    function=binding.function,
                    signal_id=binding.signal_id,
                    intentionally_unconnected=binding.intentionally_unconnected,
                    unconnected_reason=binding.unconnected_reason,
                    confidence=1.0,
                    truth_level=TruthLevel.EXPLICIT,
                    evidence_refs=tuple(dict.fromkeys((binding_ref, *(previous.evidence_refs if previous else ())))),
                )
            )
        nets = tuple(
            NetBinding.create(
                binding_id=item.net_id,
                net_name=item.name,
                nodes=item.nodes,
                signal=item.signal_id,
                purpose=f"approved schematic net {item.name}",
                confidence=1.0,
                truth_level=TruthLevel.EXPLICIT,
                evidence_refs=(evidence(item.net_id, item.net_id, "net", "approved schematic net connectivity"),),
            )
            for item in checked_plan.nets
        )
        power_domains = tuple(
            DesignPowerDomain.create(
                power_domain_id=item.domain_id,
                name=item.name,
                component_refs=tuple(dict.fromkeys((*item.source_component_ids, *item.consumer_component_ids))),
                purpose=f"approved schematic power domain {item.name}",
                voltage=item.nominal_voltage,
                ground_net=item.ground_reference,
                confidence=1.0,
                truth_level=TruthLevel.EXPLICIT,
                evidence_refs=(evidence(item.domain_id, item.domain_id, "power-domain", "approved power domain plan"),),
            )
            for item in checked_plan.power_domains
        )
        interfaces = tuple(
            DesignInterface.create(
                interface_id=item.interface_id,
                source=item.participants[0],
                target=item.participants[1],
                protocol=item.protocol,
                direction="BIDIRECTIONAL",
                confidence=1.0,
                truth_level=TruthLevel.EXPLICIT,
                evidence_refs=(evidence(item.interface_id, item.interface_id, "interface", "approved interface bus plan"),),
            )
            for item in checked_plan.interfaces
        )
        replanned_interface_ids = {item.interface_id for item in interfaces}
        base_interfaces_not_replanned = tuple(
            item
            for item in checked_base.interfaces
            if item.interface_id not in replanned_interface_ids
        )
        base_pins_not_replanned = tuple(
            item for item in checked_base.pins if (item.component_id, item.pin_number) not in binding_by_key
        )
        provenance = tuple(
            dict.fromkeys(
                (
                    *checked_base.provenance,
                    PlanProvenance.create(
                        project_id=checked_plan.project_id,
                        source_type="SCHEMATIC_PLAN",
                        source_id=checked_plan.plan_id,
                        source_fingerprint=checked_plan.fingerprint,
                    ),
                )
            )
        )
        return DesignTruth.create(
            truth_id=truth_id,
            tenant_id=checked_base.tenant_id,
            project_id=checked_plan.project_id,
            design_revision=f"schematic-{checked_plan.fingerprint[-24:]}",
            conversation_id=checked_plan.conversation_id,
            origin=DesignTruthOrigin.CREATE,
            status=DesignTruthStatus.APPROVED,
            architecture_id=checked_plan.architecture_id,
            components=checked_base.components,
            signals=signals,
            pins=(*base_pins_not_replanned, *pins),
            peripherals=checked_base.peripherals,
            nets=nets,
            interfaces=(*base_interfaces_not_replanned, *interfaces),
            power_domains=power_domains,
            firmware_bindings=checked_base.firmware_bindings,
            evidence_bindings=(*preserved_bindings, *new_bindings),
            provenance=provenance,
        )


__all__ = (
    "SchematicComponentInstance",
    "SchematicEndpoint",
    "SchematicIntentionalUnconnected",
    "SchematicInterfaceBus",
    "SchematicNet",
    "SchematicPinBinding",
    "SchematicPlan",
    "SchematicPlanApproval",
    "SchematicPlanDecision",
    "SchematicPlanProposal",
    "SchematicPlanningBlocked",
    "SchematicPlanningService",
    "SchematicPlanningState",
    "SchematicPlanningStatus",
    "SchematicPowerDomain",
    "SchematicSignal",
    "SchematicSupportingConnection",
    "SchematicTarget",
    "SupportingConnectionStatus",
)
