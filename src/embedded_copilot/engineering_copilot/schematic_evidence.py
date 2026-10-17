"""Finite S02 evidence contracts and XML observation, with no external access."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import TYPE_CHECKING, Literal, Protocol, Self
from xml.etree import ElementTree as ET

from pydantic import Field, field_validator, model_validator

from embedded_copilot.engineering_runtime.common import (
    RuntimeContract,
    canonical_fingerprint,
    validate_fingerprint,
    validate_identifier,
    validate_utc_datetime,
)

if TYPE_CHECKING:
    from embedded_copilot.component_recommendation.contracts import (
        ComponentSourceRecord,
    )
    from embedded_copilot.engineering_requirements import RequirementContext
    from embedded_copilot.engineering_runtime.objects.models import CalculationRecord
    from embedded_copilot.project_catalog.models import ReviewedStandardApplicabilityV2

    from .contracts import SchematicProductionState
    from .hardware_selection import HardwareSelectionState
    from .schematic_planning import SchematicPlanProposal

AUTO = "sha256:" + "0" * 64
PROFILE_ID = "S02-I2C-SCHEMATIC"
REQUIRED_CLAIM_IDS = tuple(f"S02-I2C-{index:02d}" for index in range(1, 9))
PROFILE_FINGERPRINT = "sha256:" + hashlib.sha256(json.dumps(
    {"profile_id": PROFILE_ID, "profile_version": 1, "required_claim_ids": REQUIRED_CLAIM_IDS},
    sort_keys=True, separators=(",", ":"),
).encode()).hexdigest()

PROFILE_FINGERPRINT_V2 = "sha256:" + hashlib.sha256(json.dumps(
    {"profile_id": PROFILE_ID, "profile_version": 2, "required_claim_ids": REQUIRED_CLAIM_IDS},
    sort_keys=True, separators=(",", ":"),
).encode()).hexdigest()


def decimal_text(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("parameter must be a finite decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("parameter decimal is invalid") from None
    if not number.is_finite() or abs(number) > Decimal("1e20") or number.as_tuple().exponent < -24:
        raise ValueError("parameter decimal is invalid")
    return format(number.normalize(), "f")


class EvidenceContract(RuntimeContract):
    fingerprint: str = AUTO

    @model_validator(mode="after")
    def _fingerprint_binding(self) -> Self:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("evidence fingerprint mismatch")
        return self


class ParameterLocator(RuntimeContract):
    page: int | None = Field(default=None, ge=1)
    table: str | None = None
    row: str | None = None
    section: str | None = None
    clause: str | None = None

    @model_validator(mode="after")
    def _locator(self) -> Self:
        if not ((self.page is not None and self.table and self.row) or (self.section and self.clause)):
            raise ValueError("parameter locator is unresolved")
        return self


class LibraryTerminalBindingV2(RuntimeContract):
    manufacturer_terminal: str
    manufacturer_locator: ParameterLocator
    logical_symbol_pin: str
    physical_pad_ids: tuple[str, ...]
    net_id: str

    @field_validator("manufacturer_terminal", mode="before")
    @classmethod
    def _manufacturer(cls, value: object) -> str:
        if value not in ("1", "2", "3", "4", "EP"):
            raise ValueError("manufacturer terminal is unsupported")
        return value

    @field_validator("logical_symbol_pin", "net_id", mode="before")
    @classmethod
    def _id(cls, value: object, info) -> str:
        return validate_identifier(value, field=info.field_name)

    @field_validator("physical_pad_ids", mode="before")
    @classmethod
    def _pads(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (tuple, list) or not 0 < len(value) <= 128:
            raise ValueError("physical pads must be a bounded nonempty sequence")
        pads = tuple(validate_identifier(item, field="physical_pad_id") for item in value)
        if len(pads) != len(set(pads)):
            raise ValueError("physical pad repeats")
        return pads


class ParameterConditions(RuntimeContract):
    voltage_min: str | None = None
    voltage_max: str | None = None
    temperature_min: str | None = None
    temperature_max: str | None = None
    mode: Literal["DC", "I2C_STANDARD", "UNKNOWN"] = "UNKNOWN"
    package: str | None = None

    _numbers = field_validator("voltage_min", "voltage_max", "temperature_min", "temperature_max", mode="before")(
        lambda value: None if value is None else decimal_text(value)
    )

    @model_validator(mode="after")
    def _ranges(self) -> Self:
        for minimum, maximum in ((self.voltage_min, self.voltage_max), (self.temperature_min, self.temperature_max)):
            if (minimum is None) != (maximum is None) or (minimum is not None and Decimal(minimum) > Decimal(maximum)):
                raise ValueError("parameter condition range is invalid")
        return self

    def covers(self, required: ParameterConditions) -> bool:
        if self.mode == "UNKNOWN" or required.mode == "UNKNOWN" or self.mode != required.mode or not required.package or self.package != required.package:
            return False
        for low, high, need_low, need_high in (
            (self.voltage_min, self.voltage_max, required.voltage_min, required.voltage_max),
            (self.temperature_min, self.temperature_max, required.temperature_min, required.temperature_max),
        ):
            if None in (low, high, need_low, need_high) or Decimal(low) > Decimal(need_low) or Decimal(high) < Decimal(need_high):
                return False
        return True


class ParameterCandidate(EvidenceContract):
    schema_version: Literal[1] = 1
    evidence_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    source_record_id: str
    source_record_fingerprint: str
    manufacturer: str
    mpn: str
    package: str
    document_uri: str
    document_revision: str
    content_sha256: str
    locator: ParameterLocator
    parameter_key: Literal["SUPPLY_MIN", "SUPPLY_MAX", "PULLUP_RESISTANCE", "PULLUP_TOLERANCE", "BUS_CAPACITANCE_MAX", "RISE_TIME_MAX", "BUS_FREQUENCY_MAX", "DECOUPLING_NOMINAL", "DECOUPLING_EFFECTIVE_MIN", "VOL_MAX", "VIL_MAX", "IOL_MIN", "LEAKAGE_MAX"]
    bound_kind: Literal["MIN", "MAX", "TYP", "EXACT"]
    value_decimal: str
    unit: Literal["V", "ohm", "F", "s", "Hz", "ratio", "A"]
    conditions: ParameterConditions

    _ids = field_validator("evidence_id", "tenant_id", "project_id", "conversation_id", "source_record_id")(
        lambda v, info: validate_identifier(v, field=info.field_name)
    )
    _fps = field_validator("source_record_fingerprint", "content_sha256")(lambda v: validate_fingerprint(v))
    _value = field_validator("value_decimal", mode="before")(decimal_text)

    @model_validator(mode="after")
    def _value_binding(self) -> Self:
        units = {"SUPPLY_MIN": "V", "SUPPLY_MAX": "V", "PULLUP_RESISTANCE": "ohm", "PULLUP_TOLERANCE": "ratio",
                 "BUS_CAPACITANCE_MAX": "F", "RISE_TIME_MAX": "s", "BUS_FREQUENCY_MAX": "Hz",
                 "DECOUPLING_NOMINAL": "F", "DECOUPLING_EFFECTIVE_MIN": "F", "VOL_MAX": "V", "VIL_MAX": "V", "IOL_MIN": "A", "LEAKAGE_MAX": "A"}
        if self.unit != units[self.parameter_key] or Decimal(self.value_decimal) < 0 or self.conditions.package != self.package:
            raise ValueError("parameter unit or exact package mismatch")
        if not all(isinstance(v, str) and v.strip() and len(v) < 1024 for v in (self.manufacturer, self.mpn, self.package, self.document_uri, self.document_revision)):
            raise ValueError("parameter source is invalid")
        return self


class ParameterReview(EvidenceContract):
    review_id: str
    tenant_id: str
    project_id: str
    evidence_id: str
    evidence_fingerprint: str
    decision: Literal["APPROVED", "REJECTED"]
    reviewer: str
    decided_at: datetime

    _ids = field_validator("review_id", "tenant_id", "project_id", "evidence_id", "reviewer")(
        lambda v, info: validate_identifier(v, field=info.field_name)
    )
    _fp = field_validator("evidence_fingerprint")(validate_fingerprint)
    _time = field_validator("decided_at")(validate_utc_datetime)


class ReviewedParameterEvidenceV1(EvidenceContract):
    candidate: ParameterCandidate
    review: ParameterReview

    @model_validator(mode="after")
    def _review_binding(self) -> Self:
        if (self.review.decision != "APPROVED" or self.review.tenant_id != self.candidate.tenant_id
                or self.review.project_id != self.candidate.project_id
                or self.review.evidence_id != self.candidate.evidence_id
                or self.review.evidence_fingerprint != self.candidate.fingerprint):
            raise ValueError("parameter review binding mismatch")
        return self


class ParameterDocumentPort(Protocol):
    """Trusted local source verifies digest, source identity and locator value."""
    def read_parameter(self, candidate: ParameterCandidate) -> ParameterCandidate: ...


class NetlistComponent(RuntimeContract):
    reference: str
    manufacturer: str
    mpn: str
    package: str
    footprint: str
    symbol: str = "UNKNOWN"


class XmlNetlistObservation(RuntimeContract):
    components: tuple[NetlistComponent, ...]
    pin_nets: tuple[tuple[str, str, str], ...]
    pin_definitions: tuple[tuple[str, str, str], ...] = ()


def parse_xml_netlist(content: bytes, *, require_properties: bool = True) -> XmlNetlistObservation:
    """Bounded independent XML reader, rejecting missing coverage and ambiguity."""
    if not isinstance(content, bytes) or not content or len(content) > 4 * 1024 * 1024 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("XML netlist is unavailable or unsafe")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ValueError("XML netlist is invalid") from None
    if root.tag != "export" or root.find("components") is None or root.find("nets") is None:
        raise ValueError("XML netlist coverage is unknown")
    components = []
    definitions = []
    libraries = {}
    for library in root.findall("./libparts/libpart"):
        identity = (library.get("lib"), library.get("part"))
        pins = tuple((pin.get("num"), pin.get("name")) for pin in library.findall("./pins/pin"))
        if identity in libraries or not all(identity) or not pins or not all(all(pin) for pin in pins) or len({pin[0] for pin in pins}) != len(pins):
            raise ValueError("XML library pin coverage is ambiguous")
        libraries[identity] = pins
    references = set()
    for item in root.findall("./components/comp"):
        ref = item.get("ref")
        props = {}
        for prop in item.findall("property"):
            name = prop.get("name", "").casefold()
            if name in props:
                raise ValueError("XML duplicate component property")
            props[name] = prop.get("value", "")
        values = (ref, props.get("manufacturer"), props.get("mpn"), props.get("package"), item.findtext("footprint"))
        if not ref or ref in references or (require_properties and not all(values)):
            raise ValueError("XML exact component coverage is unknown")
        libsource = item.find("libsource")
        identity = None if libsource is None else (libsource.get("lib"), libsource.get("part"))
        if require_properties and identity not in libraries:
            raise ValueError("XML native symbol pin coverage is unknown")
        symbol = "UNKNOWN" if identity is None else ":".join(identity)
        for pin, name in libraries.get(identity, ()):
            definitions.append((ref, pin, name))
        references.add(ref)
        components.append(NetlistComponent(reference=ref, manufacturer=values[1] or "UNKNOWN", mpn=values[2] or "UNKNOWN", package=values[3] or "UNKNOWN", footprint=values[4] or "UNKNOWN", symbol=symbol))
    if not components:
        raise ValueError("XML component coverage is empty")
    nodes = []
    seen = set()
    net_names = set()
    for net in root.findall("./nets/net"):
        name = net.get("name")
        if not name or name in net_names:
            raise ValueError("XML net identity is ambiguous")
        net_names.add(name)
        for node in net.findall("node"):
            ref, pin = node.get("ref"), node.get("pin")
            if ref not in references or not pin or (ref, pin) in seen:
                raise ValueError("XML physical pin mapping is ambiguous")
            seen.add((ref, pin))
            nodes.append((ref, pin, name))
    if not nodes:
        raise ValueError("XML pin/net coverage is empty")
    return XmlNetlistObservation(components=tuple(sorted(components, key=lambda x: x.reference)), pin_nets=tuple(sorted(nodes)), pin_definitions=tuple(sorted(definitions)))


class LocalParameterSnapshotPort:
    """Read-only source boundary; document and locator readers are explicitly injected.

    The locator reader returns the extracted typed parameter from the supplied
    original bytes, not from a cached candidate. No PDF or resource is bundled.
    """
    def __init__(self, *, read_document: Callable[[str, str], bytes],
                 read_locator: Callable[[bytes, ParameterLocator], ParameterCandidate],
                 read_component_source: Callable[[tuple[bytes, ...], str], ComponentSourceRecord] | None = None) -> None:
        self._read_document = read_document
        self._read_locator = read_locator
        self._read_component_source = read_component_source

    def read_parameter(self, candidate: ParameterCandidate) -> ParameterCandidate:
        original = self._read_document(candidate.document_uri, candidate.document_revision)
        if not isinstance(original, bytes) or not original or "sha256:" + hashlib.sha256(original).hexdigest() != candidate.content_sha256:
            raise ValueError("CONTENT_MISMATCH")
        extracted = ParameterCandidate.model_validate(self._read_locator(original, candidate.locator))
        if extracted != candidate:
            raise ValueError("LOCATOR_UNRESOLVED")
        return extracted

    def read_component_source(self, source: ComponentSourceRecord,
                              candidates: tuple[ParameterCandidate, ...]) -> ComponentSourceRecord:
        if self._read_component_source is None or not candidates:
            raise ValueError("SOURCE_UNAVAILABLE")
        originals = []
        for candidate in candidates:
            self.read_parameter(candidate)
            originals.append(self._read_document(candidate.document_uri, candidate.document_revision))
        extracted = self._read_component_source(tuple(originals), source.source_id)
        if extracted != source:
            raise ValueError("CONTENT_MISMATCH")
        return extracted


def compare_plan_xml(plan: SchematicPlanProposal, production: SchematicProductionState,
                     selection: HardwareSelectionState, observation: XmlNetlistObservation) -> bool:
    """Exact bijection of current parts and connected physical endpoints."""
    slots = {item.slot_id: item.source for item in selection.proposal.selected_components}
    references = {item.component_id: item.reference for item in production.proposal.components}
    expected = []
    for item in plan.component_instances:
        source = slots.get(item.hardware_slot_id)
        reference = references.get(item.instance_id)
        if source is None or reference is None or not item.footprint or not source.package or item.quantity != 1:
            return False
        expected.append(NetlistComponent(reference=reference, manufacturer=item.manufacturer,
            mpn=item.mpn, package=source.package, footprint=item.footprint, symbol=item.symbol))
    pins = tuple(sorted((references.get(node.component_id, "UNKNOWN"), node.pin_number, net.name)
                        for net in plan.nets for node in net.endpoints))
    return (len({item.reference for item in expected}) == len(expected)
            and tuple(sorted(expected, key=lambda item: item.reference)) == observation.components
            and pins == observation.pin_nets
            and tuple(sorted((references.get(binding.component_id, "UNKNOWN"), binding.pin_number, binding.pin_name)
                for binding in plan.pin_bindings)) == observation.pin_definitions)


def resolve_calculation_inputs(calculation: CalculationRecord, requirement: RequirementContext,
                               reviewed: tuple[ReviewedParameterEvidenceV1, ...], *,
                               reviewed_standards: tuple[ReviewedStandardApplicabilityV2, ...] = ()) -> dict[str, Decimal] | None:
    """Resolve every input from its exact current Owner; ignore claimed outputs/status."""
    evidence = {item.candidate.evidence_id: item for item in reviewed}
    standards = {item.evidence.evidence_id: item for item in reviewed_standards}
    if len(standards) != len(reviewed_standards) or set(standards) & set(evidence):
        return None
    fields = {item.category: item for item in requirement.standard_fields if item.status == "CONFIRMED"}
    expected_evidence = {"r_ohm": ("PULLUP_RESISTANCE", "ohm", {"EXACT"}),
        "r_tolerance": ("PULLUP_TOLERANCE", "ratio", {"MAX", "EXACT"}), "rise_limit_s": ("RISE_TIME_MAX", "s", {"MAX", "EXACT"}),
        "frequency_limit_hz": ("BUS_FREQUENCY_MAX", "Hz", {"MAX", "EXACT"}), "c_effective_f": ("DECOUPLING_EFFECTIVE_MIN", "F", {"MIN", "EXACT"}),
        "vol_max_v": ("VOL_MAX", "V", {"MAX", "EXACT"}), "vil_max_v": ("VIL_MAX", "V", {"MAX", "EXACT"}),
        "iol_min_a": ("IOL_MIN", "A", {"MIN", "EXACT"}), "leakage_max_a": ("LEAKAGE_MAX", "A", {"MAX", "EXACT"})}
    expected_requirement = {"cbus_f": ("S02_CBUS_MAX_F", "F"), "frequency_hz": ("S02_FREQUENCY_HZ", "Hz"),
        "c_required_f": ("S02_DECOUPLING_MIN_F", "F"), "vmax_v": ("S02_VOLTAGE_MAX_V", "V")}
    result = {}
    for source in calculation.input_sources:
        if source.condition != calculation.corner or source.input_name in result:
            return None
        if source.source_kind == "ENGINEERING_EVIDENCE":
            standard = standards.get(source.source_id)
            if standard is not None:
                fact, review = standard.evidence, standard.review
                expected = {"rise_limit_s": ("rise_time_max_s", "s"),
                            "frequency_limit_hz": ("frequency_max_hz", "Hz")}.get(source.input_name)
                targets = tuple(target for target in fact.target_components
                    if target.component_instance_id == source.component_instance_id)
                if (expected is None or review is None or review.decision != "APPROVED"
                    or fact.requirement_fingerprint != requirement.fingerprint
                    or fact.conversation_id != requirement.conversation_id
                    or source.source_fingerprint != fact.fingerprint
                    or source.content_fingerprint != fact.content_sha256
                    or source.locator != fact.locator.model_dump_json()
                    or source.unit != expected[1] or len(targets) != 1
                    or getattr(fact, expected[0]) is None
                    or decimal_text(str(source.value)) != getattr(fact, expected[0])):
                    return None
                try:
                    corner = ParameterConditions.model_validate_json(source.condition)
                except (TypeError, ValueError):
                    return None
                if (corner.mode != fact.mode or corner.package is not None
                    or any(getattr(corner, field) is None for field in (
                        "voltage_min", "voltage_max", "temperature_min", "temperature_max"))
                    or not targets[0].device_conditions.covers(corner.model_copy(
                        update={"package": targets[0].device_conditions.package}))):
                    return None
                result[source.input_name] = Decimal(decimal_text(str(source.value)))
                continue
            item = evidence.get(source.source_id)
            if item is None:
                return None
            candidate = item.candidate
            expected = expected_evidence.get(source.input_name)
            if expected is None or (candidate.parameter_key, candidate.unit) != expected[:2] or candidate.bound_kind not in expected[2]:
                return None
            if (source.source_fingerprint != item.fingerprint or source.content_fingerprint != candidate.content_sha256
                    or source.unit != candidate.unit or source.locator != candidate.locator.model_dump_json()
                    or source.condition != candidate.conditions.model_dump_json(exclude={"package"})
                    or decimal_text(str(source.value)) != candidate.value_decimal):
                return None
        elif source.source_kind == "CATALOG_REQUIREMENT":
            expected = expected_requirement.get(source.input_name)
            if expected is None or (source.locator, source.unit) != expected:
                return None
            field = fields.get(source.locator)
            if (field is None or source.source_id != requirement.conversation_id
                    or source.source_fingerprint != requirement.fingerprint or source.content_fingerprint != requirement.fingerprint
                    or field.value is None or decimal_text(str(source.value)) != decimal_text(field.value)):
                return None
        else:
            return None
        result[source.input_name] = Decimal(decimal_text(str(source.value)))
    if not result or set(result) != {name for name, _ in calculation.inputs}:
        return None
    return result


def finite_rc_decoupling(values: dict[str, Decimal]) -> bool:
    required = {"r_ohm", "r_tolerance", "cbus_f", "rise_limit_s", "frequency_hz", "frequency_limit_hz",
                "c_effective_f", "c_required_f", "vol_max_v", "iol_min_a", "leakage_max_a", "vil_max_v", "vmax_v"}
    if set(values) != required or any(not value.is_finite() or value < 0 for value in values.values()):
        return False
    # Decimal multiplication/division observes mutable caller precision, rounding
    # and traps. Preserve the same rule exactly, without rounding a failed corner
    # into a pass or modifying the caller's arithmetic context.
    exact = {name: Fraction(value) for name, value in values.items()}
    r = exact["r_ohm"]
    tolerance = exact["r_tolerance"]
    if r <= 0 or tolerance >= 1 or exact["cbus_f"] <= 0 or exact["frequency_hz"] <= 0 or exact["c_required_f"] <= 0:
        return False
    rmin, rmax = r * (1 - tolerance), r * (1 + tolerance)
    return (Fraction(8473, 10000) * rmax * exact["cbus_f"] <= exact["rise_limit_s"]
            and exact["frequency_hz"] <= exact["frequency_limit_hz"]
            and exact["c_effective_f"] >= exact["c_required_f"]
            and exact["vmax_v"] > exact["vol_max_v"]
            and (exact["vmax_v"] - exact["vol_max_v"]) / rmin <= exact["iol_min_a"]
            and exact["vol_max_v"] + rmax * exact["leakage_max_a"] <= exact["vil_max_v"])
