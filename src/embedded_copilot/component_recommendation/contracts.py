from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ComponentContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _text(value: object, *, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} is invalid")
    text = unicodedata.normalize("NFC", value.strip())
    if (
        not text
        or len(text) > maximum
        or any(char in text for char in ("\x00", "\r", "\n"))
        or re.search(
            r"(?:password|credential|secret|token|api[_ -]?key)\s*[:=]", text, re.I
        )
    ):
        raise ValueError(f"{field} is unsafe")
    return text


def _url(value: object) -> str:
    text = _text(value, field="supplier_link", maximum=512)
    if not re.fullmatch(r"https://[^\s/]+(?:/[^\s]*)?", text, re.IGNORECASE):
        raise ValueError("supplier_link is invalid")
    return text


class ComponentRecommendation(ComponentContract):
    part_number: str
    manufacturer: str
    reason: str
    datasheet_reference: str
    supplier_links: tuple[str, ...] = Field(max_length=16)
    alternatives: tuple[str, ...] = Field(max_length=16)

    @field_validator(
        "part_number", "manufacturer", "reason", "datasheet_reference", mode="before"
    )
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("supplier_links", mode="before")
    @classmethod
    def validate_links(cls, value: object) -> object:
        if type(value) is not tuple:
            raise ValueError("supplier_links must be a tuple")
        return tuple(_url(item) for item in value)

    @field_validator("alternatives", mode="before")
    @classmethod
    def validate_alternatives(cls, value: object) -> object:
        if type(value) is not tuple:
            raise ValueError("alternatives must be a tuple")
        return tuple(_text(item, field="alternative") for item in value)


def _catalog_fingerprint(value: BaseModel, *, exclude: set[str]) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _catalog_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    if type(value) is not tuple:
        raise ValueError(f"{field} must be a tuple")
    checked = tuple(_text(item, field=field, maximum=512) for item in value)
    if len(checked) != len(set(item.casefold() for item in checked)):
        raise ValueError(f"{field} must be unique")
    return checked


class ComponentPinFact(ComponentContract):
    number: str
    name: str
    function: str
    pad_number: str | None = None
    is_power: bool = False
    interface_protocols: tuple[str, ...] = ()

    @field_validator("number", "name", "function", mode="before")
    @classmethod
    def validate_pin_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=128)

    @field_validator("pad_number", mode="before")
    @classmethod
    def validate_pad_number(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="pad_number", maximum=128)

    @field_validator("interface_protocols", mode="before")
    @classmethod
    def validate_protocols(cls, value: object) -> tuple[str, ...]:
        return _catalog_tuple(value, field="interface_protocols")

    @classmethod
    def create(cls, **values: object) -> "ComponentPinFact":
        return cls.model_validate(values)


class ComponentElectricalFact(ComponentContract):
    parameter: str
    minimum: float | None = None
    typical: float | None = None
    maximum: float | None = None
    unit: str

    @field_validator("parameter", "unit", mode="before")
    @classmethod
    def validate_electrical_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=128)

    @model_validator(mode="after")
    def validate_range(self) -> "ComponentElectricalFact":
        values = [item for item in (self.minimum, self.typical, self.maximum) if item is not None]
        if not values or values != sorted(values):
            raise ValueError("electrical fact range is invalid")
        return self

    @classmethod
    def create(cls, **values: object) -> "ComponentElectricalFact":
        return cls.model_validate(values)


class ComponentInterfaceFact(ComponentContract):
    protocol: str
    pin_names: tuple[str, ...] = ()
    address: str | None = None
    constraints: tuple[str, ...] = ()

    @field_validator("protocol", mode="before")
    @classmethod
    def validate_protocol(cls, value: object) -> str:
        return _text(value, field="protocol", maximum=128)

    @field_validator("pin_names", "constraints", mode="before")
    @classmethod
    def validate_interface_values(cls, value: object, info) -> tuple[str, ...]:
        return _catalog_tuple(value, field=info.field_name)

    @field_validator("address", mode="before")
    @classmethod
    def validate_address(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="address", maximum=128)

    @classmethod
    def create(cls, **values: object) -> "ComponentInterfaceFact":
        return cls.model_validate(values)


class SupportingConnectionExpectation(ComponentContract):
    target_pin_number: str
    target_net_name: str
    signal_pin_number: str
    signal_net_name: str
    return_pin_number: str
    return_net_name: str

    @field_validator(
        "target_pin_number", "target_net_name", "signal_pin_number",
        "signal_net_name", "return_pin_number", "return_net_name", mode="before",
    )
    @classmethod
    def validate_endpoint(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=128)

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> SupportingConnectionExpectation:
        if (
            self.signal_pin_number == self.return_pin_number
            or self.target_net_name == self.return_net_name
        ):
            raise ValueError("supporting connection endpoints are not distinct")
        return self

    @classmethod
    def create(cls, **values: object) -> SupportingConnectionExpectation:
        return cls.model_validate(values)


class SupportingComponentRequirement(ComponentContract):
    role: str
    description: str
    quantity: int = Field(default=1, ge=1, le=1000)
    evidence_refs: tuple[str, ...] = ()
    connection_expectations: tuple[SupportingConnectionExpectation, ...] = Field(
        default=(), exclude_if=lambda value: not value,
    )

    @field_validator("role", "description", mode="before")
    @classmethod
    def validate_requirement_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=1024)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_requirement_refs(cls, value: object) -> tuple[str, ...]:
        return _catalog_tuple(value, field="evidence_refs")

    @field_validator("connection_expectations", mode="before")
    @classmethod
    def validate_connection_expectations(cls, value: object) -> tuple:
        if type(value) is list:
            value = tuple(value)
        if type(value) is not tuple:
            raise ValueError("connection_expectations must be a tuple")
        return value

    @classmethod
    def create(cls, **values: object) -> "SupportingComponentRequirement":
        return cls.model_validate(values)


class ComponentPowerFact(ComponentContract):
    domain: str
    role: str
    nominal_voltage: float | None
    minimum_voltage: float | None = None
    maximum_voltage: float | None = None
    pin_numbers: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    @field_validator("domain", "role", mode="before")
    @classmethod
    def validate_power_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=128)

    @field_validator("nominal_voltage", "minimum_voltage", "maximum_voltage", mode="before")
    @classmethod
    def validate_power_voltage(cls, value: object, info) -> float | None:
        if value is None:
            return None
        if type(value) not in {int, float} or isinstance(value, bool):
            raise ValueError(f"{info.field_name} is invalid")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0 or numeric > 1000:
            raise ValueError(f"{info.field_name} is invalid")
        return numeric

    @field_validator("pin_numbers", "constraints", mode="before")
    @classmethod
    def validate_power_values(cls, value: object, info) -> tuple[str, ...]:
        return _catalog_tuple(value, field=info.field_name)

    @model_validator(mode="after")
    def validate_power_range(self) -> "ComponentPowerFact":
        if self.nominal_voltage is None and (
            self.role.casefold() != "consumer"
            or self.minimum_voltage is None
            or self.maximum_voltage is None
        ):
            raise ValueError("unresolved consumer power requires a sourced voltage range")
        values = [
            item
            for item in (self.minimum_voltage, self.nominal_voltage, self.maximum_voltage)
            if item is not None
        ]
        if values != sorted(values):
            raise ValueError("power fact voltage range is invalid")
        if self.role.casefold() not in {"source", "consumer", "ground"}:
            raise ValueError("power fact role is invalid")
        if self.role.casefold() != "ground" and not self.pin_numbers:
            raise ValueError("non-ground power fact requires pin numbers")
        return self

    @classmethod
    def create(cls, **values: object) -> "ComponentPowerFact":
        return cls.model_validate(values)


class ComponentSourceRecord(ComponentContract):
    source_id: str
    source_uri: str
    source_revision: str
    manufacturer: str
    mpn: str
    category: str
    package: str
    description: str
    symbol: str | None
    footprint: str | None
    supported_roles: tuple[str, ...]
    pin_facts: tuple[ComponentPinFact, ...] = ()
    electrical_facts: tuple[ComponentElectricalFact, ...] = ()
    interface_facts: tuple[ComponentInterfaceFact, ...] = ()
    power_facts: tuple[ComponentPowerFact, ...] = ()
    supporting_requirements: tuple[SupportingComponentRequirement, ...] = ()
    fingerprint: str

    @field_validator(
        "source_id", "source_uri", "source_revision", "manufacturer", "mpn",
        "category", "package", "description", "symbol", "footprint", mode="before"
    )
    @classmethod
    def validate_source_text(cls, value: object, info) -> str | None:
        if value is None and info.field_name in {"symbol", "footprint"}:
            return None
        return _text(value, field=info.field_name, maximum=1024)

    @field_validator("supported_roles", mode="before")
    @classmethod
    def validate_roles(cls, value: object) -> tuple[str, ...]:
        roles = _catalog_tuple(value, field="supported_roles")
        if not roles:
            raise ValueError("supported_roles cannot be empty")
        return roles

    @field_validator(
        "pin_facts", "electrical_facts", "interface_facts", "power_facts", "supporting_requirements",
        mode="before",
    )
    @classmethod
    def validate_fact_tuples(cls, value: object, info) -> tuple:
        if type(value) is list:
            value = tuple(value)
        if type(value) is not tuple:
            raise ValueError(f"{info.field_name} must be a tuple")
        return tuple(value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_source_fingerprint(cls, value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            raise ValueError("source fingerprint is invalid")
        return value

    @classmethod
    def create(cls, **values: object) -> "ComponentSourceRecord":
        material = dict(values)
        fact_models = {
            "pin_facts": ComponentPinFact,
            "electrical_facts": ComponentElectricalFact,
            "interface_facts": ComponentInterfaceFact,
            "power_facts": ComponentPowerFact,
            "supporting_requirements": SupportingComponentRequirement,
        }
        for field, model in fact_models.items():
            raw_items = material.get(field, ())
            if type(raw_items) is list:
                raw_items = tuple(raw_items)
            material[field] = tuple(
                item
                if type(item) is model
                else model.model_validate(copy.deepcopy(item), strict=False)
                for item in raw_items
            )
        provisional = cls.model_construct(
            **{**material, "fingerprint": "sha256:" + "0" * 64}
        )
        material["fingerprint"] = _catalog_fingerprint(provisional, exclude={"fingerprint"})
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify_source_fingerprint(self) -> "ComponentSourceRecord":
        if self.fingerprint != _catalog_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("source fingerprint mismatch")
        pin_numbers = tuple(item.number for item in self.pin_facts)
        if len(pin_numbers) != len(set(pin_numbers)):
            raise ValueError("source pin numbers must be unique")
        known_pin_names = {
            value.casefold()
            for pin in self.pin_facts
            for value in (pin.number, pin.name)
        }
        if any(
            pin.casefold() not in known_pin_names
            for item in self.interface_facts
            for pin in item.pin_names
        ):
            raise ValueError("source interface pin reference is invalid")
        return self


@runtime_checkable
class ComponentRecommendationPort(Protocol):
    def get_recommendations(
        self, project_id: str
    ) -> tuple[ComponentRecommendation, ...] | None: ...


@runtime_checkable
class ComponentSourcePort(Protocol):
    def search(
        self, project_id: str, architecture_id: str, requirement: object
    ) -> tuple[ComponentSourceRecord, ...]: ...


def validate_recommendations(value: object) -> tuple[ComponentRecommendation, ...]:
    if type(value) is not tuple:
        raise TypeError("recommendations must be a tuple")
    checked_items: list[ComponentRecommendation] = []
    for item in value:
        if type(item) is not ComponentRecommendation:
            raise TypeError("recommendation is invalid")
        checked_items.append(
            ComponentRecommendation.model_validate(
                copy.deepcopy(item.model_dump(mode="python"))
            )
        )
    checked = tuple(checked_items)
    parts = tuple(item.part_number for item in checked)
    if len(parts) != len(set(parts)):
        raise ValueError("recommendations must be unique")
    return checked


__all__ = [
    "ComponentRecommendation",
    "ComponentRecommendationPort",
    "ComponentElectricalFact",
    "ComponentInterfaceFact",
    "ComponentPinFact",
    "ComponentPowerFact",
    "ComponentSourcePort",
    "ComponentSourceRecord",
    "SupportingComponentRequirement",
    "SupportingConnectionExpectation",
    "validate_recommendations",
]
