from __future__ import annotations

import copy
import hashlib
import json
import re
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import Field, field_validator, model_validator

from embedded_copilot.pcb.models import UnifiedPCBModel

from .models import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBContract,
)

_FP = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_ZERO = "sha256:" + "0" * 64


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _fp(value: object, field: str) -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _text(value: object, field: str, maximum: int = 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = value.strip()
    if not checked or len(checked) > maximum:
        raise ValueError(f"{field} is invalid")
    return checked


def _tuple(value: object, field: str) -> tuple:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return tuple(copy.deepcopy(value))


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(item) for item in parts)
    return f"{prefix}-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _bundle_fingerprint(values: tuple[PCBFoundationEvidence, ...]) -> str:
    payload = [item.model_dump(mode="json", exclude={"fingerprint"}) for item in values]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class PCBApplicability(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


Applicability = PCBApplicability


class PCBFoundationStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class PCBConnectivityStatus(StrEnum):
    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class PCBSignalClass(StrEnum):
    DIGITAL = "DIGITAL"
    ANALOG = "ANALOG"
    CLOCK = "CLOCK"
    POWER = "POWER"
    GROUND = "GROUND"
    DIFFERENTIAL = "DIFFERENTIAL"
    HIGH_CURRENT = "HIGH_CURRENT"
    UNKNOWN = "UNKNOWN"


class PCBGeometryQuality(StrEnum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    UNAVAILABLE = "UNAVAILABLE"


class PCBReferenceDiscontinuityType(StrEnum):
    SPLIT = "SPLIT"
    VOID = "VOID"
    KEEPOUT = "KEEPOUT"
    MISSING_PLANE = "MISSING_PLANE"
    UNKNOWN_GEOMETRY = "UNKNOWN_GEOMETRY"
    REFERENCE_DISCONTINUITY = "REFERENCE_DISCONTINUITY"


class PCBFoundationEvidenceSource(StrEnum):
    ARTIFACT = "ARTIFACT"
    DESIGN_TRUTH = "DESIGN_TRUTH"
    HARDWARE_TRUTH = "HARDWARE_TRUTH"
    G5_RECEIPT = "G5_RECEIPT"
    DATASHEET = "DATASHEET"
    APP_NOTE = "APP_NOTE"
    INTERFACE_SPEC = "INTERFACE_SPEC"
    PROJECT_REQUIREMENT = "PROJECT_REQUIREMENT"
    DESIGN_DECISION = "DESIGN_DECISION"
    MANUFACTURER_PROFILE = "MANUFACTURER_PROFILE"
    USER_DECISION = "USER_DECISION"
    REFERENCE_PATTERN = "REFERENCE_PATTERN"
    ENGINEERING_KNOWLEDGE = "ENGINEERING_KNOWLEDGE"
    TOOL_RECEIPT = "TOOL_RECEIPT"


class PCBConstraintType(StrEnum):
    DECOUPLING = "DECOUPLING"
    RETURN_PATH = "RETURN_PATH"
    SWITCHING_POWER = "SWITCHING_POWER"
    DIFFERENTIAL_PAIR = "DIFFERENTIAL_PAIR"
    FOOTPRINT_PIN_MAPPING = "FOOTPRINT_PIN_MAPPING"
    DATASHEET_LAYOUT = "DATASHEET_LAYOUT"
    MANUFACTURING = "MANUFACTURING"
    TESTABILITY = "TESTABILITY"
    GENERAL_PLACEMENT = "GENERAL_PLACEMENT"
    GENERAL_ROUTING = "GENERAL_ROUTING"


class PCBConstraintSource(StrEnum):
    ENGINEERING_KNOWLEDGE = "ENGINEERING_KNOWLEDGE"
    DATASHEET = "DATASHEET"
    APP_NOTE = "APP_NOTE"
    INTERFACE_SPEC = "INTERFACE_SPEC"
    PROJECT_REQUIREMENT = "PROJECT_REQUIREMENT"
    DESIGN_DECISION = "DESIGN_DECISION"
    MANUFACTURER_PROFILE = "MANUFACTURER_PROFILE"
    USER_DECISION = "USER_DECISION"
    REFERENCE_PATTERN = "REFERENCE_PATTERN"
    G5_RECEIPT = "G5_RECEIPT"
    ARTIFACT = "ARTIFACT"


class PCBConstraintEnforcementMode(StrEnum):
    HARD = "HARD"
    ADVISORY = "ADVISORY"


class PCBVerifier(StrEnum):
    DECOUPLING = "DecouplingVerifier"
    RETURN_PATH = "ReturnPathVerifier"
    POWER_LOOP = "PowerLoopVerifier"
    DIFFERENTIAL_PAIR = "DifferentialPairVerifier"
    FOOTPRINT_PIN_MAPPING = "FootprintPinConsistencyVerifier"
    DATASHEET_COMPLIANCE = "DatasheetComplianceVerifier"
    DFM = "DFMVerifier"
    DFT = "DFTVerifier"
    GENERAL_PLACEMENT = "PlacementVerifier"
    GENERAL_ROUTING = "RoutingVerifier"


class PCBFoundationEvidence(PCBContract):
    evidence_id: str
    project_id: str
    source_type: PCBFoundationEvidenceSource
    source_id: str
    source_fingerprint: str
    claim: str
    locator: str

    @field_validator("evidence_id", "project_id", "source_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("source_type", mode="before")
    @classmethod
    def _source(cls, value: object) -> PCBFoundationEvidenceSource:
        return value if isinstance(value, PCBFoundationEvidenceSource) else PCBFoundationEvidenceSource(value)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value: object) -> str:
        return _fp(value, "source_fingerprint")

    @field_validator("claim", "locator", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _text(value, info.field_name, 2048 if info.field_name == "claim" else 512)


class PCBBoardProjection(PCBContract):
    board_id: str
    outline_kind: str | None
    width_mm: float | None
    height_mm: float | None
    origin_x_mm: float | None
    origin_y_mm: float | None
    units: str
    layers: tuple[str, ...]
    copper_layers: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("board_id", "units", mode="before")
    @classmethod
    def _board_text(cls, value: object, info) -> str:
        return _id(value, info.field_name) if info.field_name == "board_id" else _text(value, info.field_name, 32)


class PCBStackupProjection(PCBContract):
    stackup_id: str
    layers: tuple[str, ...]
    material: str | None
    thickness_mm: float | None
    copper_weight: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("stackup_id", mode="before")
    @classmethod
    def _stackup_id(cls, value: object) -> str:
        return _id(value, "stackup_id")


class PCBPadProjection(PCBContract):
    pad_id: str
    pin_id: str | None
    number: str
    net_id: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    x_mm: float | None = None
    y_mm: float | None = None
    geometry_source: str = "UNAVAILABLE"

    @field_validator("pad_id", mode="before")
    @classmethod
    def _pad_id(cls, value: object) -> str:
        return _id(value, "pad_id")

    @field_validator("pin_id", "net_id", mode="before")
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator("number", mode="before")
    @classmethod
    def _number(cls, value: object) -> str:
        return _text(value, "number", 64)

    @field_validator("geometry_source", mode="before")
    @classmethod
    def _geometry_source(cls, value: object) -> str:
        return _text(value, "geometry_source", 64)

    @model_validator(mode="after")
    def _geometry_binding(self) -> PCBPadProjection:
        if (self.x_mm is None) != (self.y_mm is None):
            raise ValueError("PCB pad geometry must contain both coordinates")
        if self.geometry_source == "PAD" and self.x_mm is None:
            raise ValueError("pad geometry source requires coordinates")
        if self.x_mm is not None and self.geometry_source == "UNAVAILABLE":
            raise ValueError("pad coordinates require a geometry source")
        return self


class PCBComponentProjection(PCBContract):
    component_id: str
    reference: str
    value: str | None
    symbol_id: str | None
    footprint_id: str | None
    pads: tuple[PCBPadProjection, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("component_id", "reference", mode="before")
    @classmethod
    def _component_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("symbol_id", "footprint_id", mode="before")
    @classmethod
    def _component_optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)


class PCBFootprintProjection(PCBContract):
    footprint_id: str
    component_id: str
    reference: str
    library_identifier: str | None
    pads: tuple[PCBPadProjection, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("footprint_id", "component_id", "reference", mode="before")
    @classmethod
    def _footprint_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)


class PCBNetProjection(PCBContract):
    net_id: str
    name: str
    member_pad_ids: tuple[str, ...]
    signal_id: str | None
    design_truth_status: PCBConnectivityStatus
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("net_id", mode="before")
    @classmethod
    def _net_id(cls, value: object) -> str:
        return _id(value, "net_id")

    @field_validator("signal_id", mode="before")
    @classmethod
    def _signal_id(cls, value: object) -> str | None:
        return None if value is None else _id(value, "signal_id")

    @field_validator("design_truth_status", mode="before")
    @classmethod
    def _connectivity(cls, value: object) -> PCBConnectivityStatus:
        return value if isinstance(value, PCBConnectivityStatus) else PCBConnectivityStatus(value)


class PCBSignalClassProjection(PCBContract):
    signal_id: str
    net_id: str
    signal_class: PCBSignalClass
    protocol: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("signal_id", "net_id", mode="before")
    @classmethod
    def _signal_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("signal_class", mode="before")
    @classmethod
    def _signal_class(cls, value: object) -> PCBSignalClass:
        return value if isinstance(value, PCBSignalClass) else PCBSignalClass(value)


class PCBDifferentialPairProjection(PCBContract):
    pair_id: str
    positive_net: str
    negative_net: str
    interface_id: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("pair_id", "positive_net", "negative_net", mode="before")
    @classmethod
    def _pair_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("interface_id", mode="before")
    @classmethod
    def _pair_interface(cls, value: object) -> str | None:
        return None if value is None else _id(value, "interface_id")

    @model_validator(mode="after")
    def _pair_identity(self) -> PCBDifferentialPairProjection:
        if self.positive_net == self.negative_net:
            raise ValueError("differential pair positive and negative nets must differ")
        return self


class PCBDifferentialPairRuleMetric(StrEnum):
    LENGTH_DIFFERENCE = "LENGTH_DIFFERENCE"
    ELECTRICAL_LENGTH_DIFFERENCE = "ELECTRICAL_LENGTH_DIFFERENCE"
    PAIR_SPACING_MIN = "PAIR_SPACING_MIN"
    PAIR_SPACING_MAX = "PAIR_SPACING_MAX"
    MATCHED_TRANSITIONS = "MATCHED_TRANSITIONS"
    VIA_COUNT_MATCH = "VIA_COUNT_MATCH"
    MAX_STUB_LENGTH = "MAX_STUB_LENGTH"
    IMPEDANCE = "IMPEDANCE"


class PCBDifferentialPairBinding(PCBContract):
    """Approved P/N identity consumed by both Verify and Prevent."""

    pair_id: str
    interface_id: str | None
    positive_net_id: str
    negative_net_id: str
    positive_signal_id: str | None
    negative_signal_id: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("pair_id", "positive_net_id", "negative_net_id", mode="before")
    @classmethod
    def _binding_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("interface_id", "positive_signal_id", "negative_signal_id", mode="before")
    @classmethod
    def _binding_optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @model_validator(mode="after")
    def _binding_identity(self) -> PCBDifferentialPairBinding:
        if self.positive_net_id == self.negative_net_id:
            raise ValueError("differential pair binding positive and negative nets must differ")
        return self


class PCBDifferentialPairRule(PCBContract):
    rule_id: str
    metric: PCBDifferentialPairRuleMetric
    comparison: str | None
    limit: float | None
    unit: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("rule_id", mode="before")
    @classmethod
    def _rule_id(cls, value: object) -> str:
        return _id(value, "rule_id")

    @field_validator("metric", mode="before")
    @classmethod
    def _rule_metric(cls, value: object) -> PCBDifferentialPairRuleMetric:
        return value if isinstance(value, PCBDifferentialPairRuleMetric) else PCBDifferentialPairRuleMetric(value)

    @field_validator("comparison", "unit", mode="before")
    @classmethod
    def _rule_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 32)

    @model_validator(mode="after")
    def _rule_binding(self) -> PCBDifferentialPairRule:
        if self.limit is not None and self.limit < 0:
            raise ValueError("differential pair rule limit must not be negative")
        if self.limit is not None and not self.comparison:
            raise ValueError("numeric differential pair rule requires comparison")
        if self.comparison is not None and self.comparison not in {"LE", "GE", "EQ"}:
            raise ValueError("differential pair rule comparison is invalid")
        if self.limit is None and self.comparison is not None:
            raise ValueError("qualitative differential pair rule cannot have comparison")
        return self


class PCBInterfaceProjection(PCBContract):
    interface_id: str
    protocol: str
    signal_ids: tuple[str, ...]
    net_ids: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("interface_id", mode="before")
    @classmethod
    def _interface_id(cls, value: object) -> str:
        return _id(value, "interface_id")


class PCBPlacementProjection(PCBContract):
    placement_id: str
    component_id: str
    footprint_id: str
    x: float
    y: float
    rotation: float
    side: str
    region: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]


class PCBRouteSegmentProjection(PCBContract):
    segment_id: str
    net_id: str
    layer: str
    width_mm: float
    start: tuple[float, float]
    end: tuple[float, float]
    via_ids: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]


class PCBRoutePathProjection(PCBContract):
    route_id: str
    net_id: str
    segment_ids: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]


class PCBPolygonProjection(PCBContract):
    polygon_id: str
    points: tuple[tuple[float, float], ...]
    holes: tuple[tuple[tuple[float, float], ...], ...] = ()

    @field_validator("polygon_id", mode="before")
    @classmethod
    def _polygon_id(cls, value: object) -> str:
        return _id(value, "polygon_id")

    @field_validator("points", "holes", mode="before")
    @classmethod
    def _geometry_points(cls, value: object, info) -> tuple:
        if type(value) is not tuple:
            raise TypeError(f"{info.field_name} must be a tuple")
        rings = value if info.field_name == "holes" else (value,)
        if info.field_name == "points" and len(value) < 3:
            raise ValueError("polygon requires at least three points")
        normalized = []
        for ring in rings:
            if type(ring) is not tuple:
                raise TypeError("polygon ring must be a tuple")
            if info.field_name == "holes" and len(ring) < 3:
                raise ValueError("polygon hole requires at least three points")
            points = []
            for point in ring:
                if type(point) is not tuple or len(point) != 2:
                    raise TypeError("polygon point must be a two-item tuple")
                x, y = point
                if type(x) not in (int, float) or type(y) not in (int, float):
                    raise TypeError("polygon coordinates must be numeric")
                points.append((float(x), float(y)))
            normalized.append(tuple(points))
        return tuple(normalized[0]) if info.field_name == "points" else tuple(normalized)


class PCBPlaneProjection(PCBContract):
    plane_id: str
    net_id: str | None
    layer: str
    geometry_ref: str
    polygons: tuple[PCBPolygonProjection, ...] = ()
    geometry_quality: PCBGeometryQuality = PCBGeometryQuality.UNAVAILABLE
    discontinuity_type: PCBReferenceDiscontinuityType | None = None
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("geometry_quality", mode="before")
    @classmethod
    def _geometry_quality(cls, value: object) -> PCBGeometryQuality:
        return value if isinstance(value, PCBGeometryQuality) else PCBGeometryQuality(value)

    @field_validator("discontinuity_type", mode="before")
    @classmethod
    def _discontinuity_type(cls, value: object) -> PCBReferenceDiscontinuityType | None:
        return None if value is None else value if isinstance(value, PCBReferenceDiscontinuityType) else PCBReferenceDiscontinuityType(value)


class PCBKeepoutProjection(PCBContract):
    keepout_id: str
    layer: str
    polygons: tuple[PCBPolygonProjection, ...]
    geometry_quality: PCBGeometryQuality
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("keepout_id", mode="before")
    @classmethod
    def _keepout_id(cls, value: object) -> str:
        return _id(value, "keepout_id")

    @field_validator("geometry_quality", mode="before")
    @classmethod
    def _keepout_quality(cls, value: object) -> PCBGeometryQuality:
        return value if isinstance(value, PCBGeometryQuality) else PCBGeometryQuality(value)


class PCBViaProjection(PCBContract):
    via_id: str
    net_id: str | None
    x: float
    y: float
    from_layer: str
    to_layer: str
    diameter_mm: float | None
    drill_mm: float | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]


class PCBPowerTopology(StrEnum):
    BUCK = "BUCK"
    BOOST = "BOOST"
    BUCK_BOOST = "BUCK_BOOST"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class PCBPowerPinRole(StrEnum):
    VIN = "VIN"
    RETURN = "RETURN"
    SW = "SW"
    CIN_POWER = "CIN_POWER"
    CIN_RETURN = "CIN_RETURN"
    COUT_POWER = "COUT_POWER"
    COUT_RETURN = "COUT_RETURN"
    INDUCTOR_INPUT = "INDUCTOR_INPUT"
    INDUCTOR_OUTPUT = "INDUCTOR_OUTPUT"
    FB = "FB"
    SENSE = "SENSE"
    ENABLE = "ENABLE"
    UNKNOWN = "UNKNOWN"


class PCBPowerLoopMetric(StrEnum):
    HOT_LOOP_PERIMETER = "HOT_LOOP_PERIMETER"
    HOT_LOOP_AREA = "HOT_LOOP_AREA"
    SW_NODE_AREA = "SW_NODE_AREA"
    FB_KEEP_OUT_DISTANCE = "FB_KEEP_OUT_DISTANCE"


class PCBPowerPinEvidence(PCBContract):
    component_id: str
    pin_id: str | None
    pad_id: str | None
    role: PCBPowerPinRole
    net_id: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("component_id", mode="before")
    @classmethod
    def _component_id(cls, value: object) -> str:
        return _id(value, "component_id")

    @field_validator("pin_id", "pad_id", "net_id", mode="before")
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, value: object) -> PCBPowerPinRole:
        return value if isinstance(value, PCBPowerPinRole) else PCBPowerPinRole(value)


class PCBPowerStageEvidence(PCBContract):
    """Approved topology and identity evidence consumed by PowerLoopVerifier."""

    power_stage_id: str
    topology: PCBPowerTopology
    controller_component_id: str | None
    input_net_id: str | None
    output_net_id: str | None
    return_net_id: str | None
    switch_node_net_id: str | None
    input_capacitor_ids: tuple[str, ...]
    output_capacitor_ids: tuple[str, ...]
    inductor_ids: tuple[str, ...]
    feedback_component_ids: tuple[str, ...]
    feedback_net_ids: tuple[str, ...]
    switching_component_ids: tuple[str, ...]
    pin_roles: tuple[PCBPowerPinEvidence, ...]
    hot_loop_route_segment_ids: tuple[str, ...]
    hot_loop_via_ids: tuple[str, ...]
    output_route_segment_ids: tuple[str, ...]
    constraint_ids: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    unknowns: tuple[str, ...] = ()

    @field_validator("power_stage_id", mode="before")
    @classmethod
    def _stage_id(cls, value: object) -> str:
        return _id(value, "power_stage_id")

    @field_validator("topology", mode="before")
    @classmethod
    def _topology(cls, value: object) -> PCBPowerTopology:
        return value if isinstance(value, PCBPowerTopology) else PCBPowerTopology(value)

    @field_validator(
        "controller_component_id", "input_net_id", "output_net_id", "return_net_id",
        "switch_node_net_id", mode="before",
    )
    @classmethod
    def _optional_stage_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator(
        "input_capacitor_ids", "output_capacitor_ids", "inductor_ids",
        "feedback_component_ids", "feedback_net_ids", "switching_component_ids",
        "hot_loop_route_segment_ids", "hot_loop_via_ids", "output_route_segment_ids",
        "constraint_ids", mode="before",
    )
    @classmethod
    def _stage_id_tuples(cls, value: object, info) -> tuple[str, ...]:
        return tuple(_id(item, info.field_name) for item in _tuple(value, info.field_name))

    @field_validator("unknowns", mode="before")
    @classmethod
    def _unknowns(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, "unknown", 256) for item in _tuple(value, "unknowns"))


class PCBPowerLoopRule(PCBContract):
    rule_id: str
    metric: PCBPowerLoopMetric
    limit: float = Field(ge=0.0)
    unit: str
    comparison: str = "LE"
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("rule_id", mode="before")
    @classmethod
    def _rule_id(cls, value: object) -> str:
        return _id(value, "rule_id")

    @field_validator("metric", mode="before")
    @classmethod
    def _metric(cls, value: object) -> PCBPowerLoopMetric:
        return value if isinstance(value, PCBPowerLoopMetric) else PCBPowerLoopMetric(value)

    @field_validator("unit", "comparison", mode="before")
    @classmethod
    def _rule_text(cls, value: object, info) -> str:
        return _text(value, info.field_name, 64)

    @model_validator(mode="after")
    def _comparison(self) -> PCBPowerLoopRule:
        if self.comparison not in {"LE", "GE"}:
            raise ValueError("power-loop rule comparison must be LE or GE")
        return self


class PCBDecouplingRelation(PCBContract):
    """Evidence-bound relation projected from approved design intent and PCB state."""

    relation_id: str
    load_component_id: str
    load_power_pin_id: str | None
    load_pad_id: str | None
    capacitor_component_id: str
    capacitor_power_pad_id: str | None
    capacitor_return_pad_id: str | None
    power_net_id: str | None
    return_net_id: str | None
    power_domain_id: str | None
    constraint_id: str
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    unknowns: tuple[str, ...] = ()

    @field_validator(
        "relation_id", "load_component_id", "capacitor_component_id", "constraint_id",
        mode="before",
    )
    @classmethod
    def _relation_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator(
        "load_power_pin_id", "load_pad_id", "capacitor_power_pad_id",
        "capacitor_return_pad_id", "power_net_id", "return_net_id", "power_domain_id",
        mode="before",
    )
    @classmethod
    def _optional_relation_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator("unknowns", mode="before")
    @classmethod
    def _relation_unknowns(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, "unknown", 256) for item in _tuple(value, "unknowns"))


class PCBUnknown(PCBContract):
    unknown_id: str
    category: str
    subject: str
    reason: str
    affected_objects: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]


class PCBConflict(PCBContract):
    conflict_id: str
    category: str
    subject: str
    design_truth_value: str
    pcb_value: str
    reason: str
    affected_objects: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]


class PCBIntentBindingStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    BOUND = "BOUND"


class PCBEngineeringFactResolution(StrEnum):
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"


class PCBPowerFactRole(StrEnum):
    VIN_SOURCE = "VIN_SOURCE"
    PROTECTION_APPLICABILITY = "PROTECTION_APPLICABILITY"
    REGULATOR_INPUT = "REGULATOR_INPUT"
    REGULATOR_OUTPUT = "REGULATOR_OUTPUT"
    INPUT_CAPACITOR = "INPUT_CAPACITOR"
    OUTPUT_CAPACITOR = "OUTPUT_CAPACITOR"
    MCU_POWER_PIN = "MCU_POWER_PIN"
    DECOUPLING = "DECOUPLING"
    RETURN_PATH = "RETURN_PATH"


class PCBSWDFactRole(StrEnum):
    SWDIO = "SWDIO"
    SWCLK = "SWCLK"
    GROUND = "GROUND"
    VREF = "VREF"
    NRST = "NRST"
    HEADER_ORIENTATION = "HEADER_ORIENTATION"
    BOARD_SIDE = "BOARD_SIDE"
    ACCESS_ENVELOPE = "ACCESS_ENVELOPE"
    KEEPOUT = "KEEPOUT"


class PCBRequiredEngineeringFact(PCBContract):
    fact_id: str
    subject_id: str
    role: PCBPowerFactRole | PCBSWDFactRole
    applicability: PCBApplicability
    resolution: PCBEngineeringFactResolution
    evidence_refs: tuple[PCBFoundationEvidence, ...] = ()
    reason: str | None = None

    @field_validator("fact_id", "subject_id", mode="before")
    @classmethod
    def _fact_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def _fact_reason(cls, value: object) -> str | None:
        return None if value is None else _text(value, "reason", 512)

    @model_validator(mode="after")
    def _fact_authority(self) -> "PCBRequiredEngineeringFact":
        if self.resolution is PCBEngineeringFactResolution.CONFIRMED:
            if not self.evidence_refs:
                raise ValueError("confirmed engineering fact requires authoritative evidence")
        elif not self.reason:
            raise ValueError("unresolved engineering fact requires an explicit reason")
        if self.applicability is PCBApplicability.UNKNOWN and self.resolution is PCBEngineeringFactResolution.CONFIRMED:
            raise ValueError("unknown applicability cannot be confirmed")
        return self


class PCBPowerFactSet(PCBContract):
    facts: tuple[PCBRequiredEngineeringFact, ...]

    @model_validator(mode="after")
    def _complete_power_roles(self) -> "PCBPowerFactSet":
        roles = tuple(item.role for item in self.facts)
        expected = set(PCBPowerFactRole)
        if any(not isinstance(role, PCBPowerFactRole) for role in roles) or set(roles) != expected or len(roles) != len(expected):
            raise ValueError("power fact set requires one fact for every authoritative role")
        return self

    @property
    def is_resolved(self) -> bool:
        return all(
            item.resolution is PCBEngineeringFactResolution.CONFIRMED
            and item.applicability is not PCBApplicability.UNKNOWN
            for item in self.facts
        )


class PCBSWDFactSet(PCBContract):
    facts: tuple[PCBRequiredEngineeringFact, ...]

    @model_validator(mode="after")
    def _complete_swd_roles(self) -> "PCBSWDFactSet":
        roles = tuple(item.role for item in self.facts)
        expected = set(PCBSWDFactRole)
        if any(not isinstance(role, PCBSWDFactRole) for role in roles) or set(roles) != expected or len(roles) != len(expected):
            raise ValueError("SWD fact set requires one fact for every authoritative role")
        vref = next(item for item in self.facts if item.role is PCBSWDFactRole.VREF)
        if vref.applicability is not PCBApplicability.REQUIRED:
            raise ValueError("SWD VREF must be an explicit required fact")
        return self

    @property
    def is_resolved(self) -> bool:
        return all(
            item.resolution is PCBEngineeringFactResolution.CONFIRMED
            and item.applicability is not PCBApplicability.UNKNOWN
            for item in self.facts
        )


class PCBReadbackObjectKind(StrEnum):
    COMPONENT = "COMPONENT"
    FOOTPRINT = "FOOTPRINT"
    PAD = "PAD"
    TRACK = "TRACK"
    VIA = "VIA"
    ZONE = "ZONE"


class PCBReadbackObjectReference(PCBContract):
    object_id: str
    object_kind: PCBReadbackObjectKind
    pcb_artifact_id: str
    pcb_artifact_fingerprint: str
    readback_fingerprint: str
    evidence_refs: tuple[PCBFoundationEvidence, ...]

    @field_validator("object_id", "pcb_artifact_id", mode="before")
    @classmethod
    def _readback_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @model_validator(mode="after")
    def _readback_evidence(self) -> "PCBReadbackObjectReference":
        if not self.evidence_refs:
            raise ValueError("read-back object requires artifact or tool evidence")
        if not any(
            item.source_type in {PCBFoundationEvidenceSource.ARTIFACT, PCBFoundationEvidenceSource.TOOL_RECEIPT}
            and item.source_fingerprint in {self.pcb_artifact_fingerprint, self.readback_fingerprint}
            for item in self.evidence_refs
        ):
            raise ValueError("read-back object evidence is not bound to the current artifact")
        return self


class PCBIntentSourceBinding(PCBContract):
    """Explicit source authority for an intent; never inferred from display data."""

    intent_id: str
    source_id: str
    source_fingerprint: str
    status: PCBIntentBindingStatus
    tenant_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    circuit_intent_id: str | None = None
    circuit_intent_revision: str | None = None
    circuit_intent_fingerprint: str | None = None
    schematic_design_id: str | None = None
    schematic_design_revision: str | None = None
    schematic_design_fingerprint: str | None = None
    circuit_schematic_binding_id: str | None = None
    circuit_schematic_binding_fingerprint: str | None = None
    schematic_artifact_id: str | None = None
    schematic_artifact_fingerprint: str | None = None
    erc_receipt_reference: str | None = None
    erc_result_reference: str | None = None
    power_facts: PCBPowerFactSet | None = None
    swd_facts: PCBSWDFactSet | None = None
    evidence_refs: tuple[PCBFoundationEvidence, ...] = ()
    unknowns: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    @field_validator("intent_id", "source_id", mode="before")
    @classmethod
    def _binding_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _binding_fp(cls, value: object) -> str:
        return _fp(value, "source_fingerprint")

    @field_validator(
        "tenant_id", "project_id", "conversation_id", "circuit_intent_id",
        "circuit_intent_revision", "schematic_design_id", "schematic_design_revision",
        "circuit_schematic_binding_id", "schematic_artifact_id", "erc_receipt_reference",
        "erc_result_reference", mode="before",
    )
    @classmethod
    def _optional_binding_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator(
        "circuit_intent_fingerprint", "schematic_design_fingerprint",
        "circuit_schematic_binding_fingerprint", "schematic_artifact_fingerprint",
        mode="before",
    )
    @classmethod
    def _optional_binding_fps(cls, value: object, info) -> str | None:
        return None if value is None else _fp(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _binding_status(cls, value: object) -> PCBIntentBindingStatus:
        return value if isinstance(value, PCBIntentBindingStatus) else PCBIntentBindingStatus(value)

    @model_validator(mode="after")
    def _binding_state(self) -> "PCBIntentSourceBinding":
        if self.status is PCBIntentBindingStatus.BOUND and (self.unknowns or self.conflicts):
            raise ValueError("BOUND intent source cannot contain unknowns or conflicts")
        if self.status is PCBIntentBindingStatus.UNKNOWN and not self.unknowns:
            raise ValueError("UNKNOWN intent source requires unknowns")
        if self.status is PCBIntentBindingStatus.CONFLICT and not self.conflicts:
            raise ValueError("CONFLICT intent source requires conflicts")
        if self.status is PCBIntentBindingStatus.BOUND:
            required = (
                self.tenant_id, self.project_id, self.conversation_id,
                self.circuit_intent_id, self.circuit_intent_revision,
                self.circuit_intent_fingerprint, self.schematic_design_id,
                self.schematic_design_revision, self.schematic_design_fingerprint,
                self.circuit_schematic_binding_id,
                self.circuit_schematic_binding_fingerprint,
                self.schematic_artifact_id, self.schematic_artifact_fingerprint,
                self.erc_receipt_reference, self.erc_result_reference,
            )
            if any(value is None for value in required):
                raise ValueError("BOUND intent source requires complete PCB source lineage")
            if self.power_facts is None or self.swd_facts is None:
                raise ValueError("BOUND intent source requires authoritative Power and SWD facts")
            if not self.power_facts.is_resolved or not self.swd_facts.is_resolved:
                raise ValueError("BOUND intent source requires resolved Power and SWD facts")
        return self


class PCBElementBinding(PCBContract):
    """Explicit intent-to-symbol/unit/pad binding, including one-to-many pads."""

    intent_id: str
    element_id: str | None = None
    component_id: str | None = None
    symbol_id: str | None = None
    pin_id: str | None = None
    net_id: str | None = None
    footprint_id: str | None = None
    pad_id: str | None = None
    track_id: str | None = None
    via_id: str | None = None
    zone_id: str | None = None
    unit_id: str | None = None
    pad_ids: tuple[str, ...] = ()
    circuit_block_id: str | None = None
    circuit_component_id: str | None = None
    circuit_pin_id: str | None = None
    circuit_net_id: str | None = None
    schematic_component_instance_id: str | None = None
    schematic_pin_binding_id: str | None = None
    schematic_net_id: str | None = None
    pcb_component_id: str | None = None
    pcb_footprint_id: str | None = None
    pcb_pad_ids: tuple[str, ...] = ()
    pcb_track_ids: tuple[str, ...] = ()
    pcb_via_ids: tuple[str, ...] = ()
    pcb_zone_ids: tuple[str, ...] = ()
    pcb_artifact_id: str | None = None
    pcb_artifact_fingerprint: str | None = None
    readback_fingerprint: str | None = None
    readback_objects: tuple[PCBReadbackObjectReference, ...] = ()
    status: PCBIntentBindingStatus
    evidence_refs: tuple[PCBFoundationEvidence, ...] = ()
    unknowns: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    @field_validator("intent_id", mode="before")
    @classmethod
    def _element_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("element_id", "component_id", "symbol_id", "pin_id", "net_id", "footprint_id", "pad_id", "track_id", "via_id", "zone_id", mode="before")
    @classmethod
    def _optional_element_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator("unit_id", mode="before")
    @classmethod
    def _unit_id(cls, value: object) -> str | None:
        return None if value is None else _id(value, "unit_id")

    @field_validator("pad_ids", mode="before")
    @classmethod
    def _pad_ids(cls, value: object) -> tuple[str, ...]:
        values = tuple(_id(item, "pad_ids") for item in _tuple(value, "pad_ids"))
        if len(set(values)) != len(values):
            raise ValueError("pad_ids must be unique")
        return values

    @field_validator(
        "circuit_block_id", "circuit_component_id", "circuit_pin_id", "circuit_net_id",
        "schematic_component_instance_id", "schematic_pin_binding_id", "schematic_net_id",
        "pcb_component_id", "pcb_footprint_id", "pcb_artifact_id", mode="before",
    )
    @classmethod
    def _cross_layer_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, info.field_name)

    @field_validator("pcb_pad_ids", "pcb_track_ids", "pcb_via_ids", "pcb_zone_ids", mode="before")
    @classmethod
    def _physical_ids(cls, value: object, info) -> tuple[str, ...]:
        values = tuple(_id(item, info.field_name) for item in _tuple(value, info.field_name))
        if len(values) != len(set(values)):
            raise ValueError(f"{info.field_name} must be unique")
        return values

    @field_validator("pcb_artifact_fingerprint", "readback_fingerprint", mode="before")
    @classmethod
    def _readback_fingerprints(cls, value: object, info) -> str | None:
        return None if value is None else _fp(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _element_status(cls, value: object) -> PCBIntentBindingStatus:
        return value if isinstance(value, PCBIntentBindingStatus) else PCBIntentBindingStatus(value)

    @model_validator(mode="after")
    def _element_state(self) -> "PCBElementBinding":
        identities = (self.element_id, self.component_id, self.symbol_id, self.pin_id, self.net_id, self.footprint_id, self.pad_id, self.track_id, self.via_id, self.zone_id, self.circuit_block_id, self.circuit_component_id, self.circuit_pin_id, self.circuit_net_id, self.schematic_component_instance_id, self.schematic_pin_binding_id, self.schematic_net_id, self.pcb_component_id, self.pcb_footprint_id, *self.pcb_pad_ids, *self.pcb_track_ids, *self.pcb_via_ids, *self.pcb_zone_ids)
        if not any(value is not None for value in identities):
            raise ValueError("element binding requires an explicit object identity")
        if self.status is PCBIntentBindingStatus.BOUND and (self.unknowns or self.conflicts):
            raise ValueError("BOUND element binding cannot contain unknowns or conflicts")
        if self.status is PCBIntentBindingStatus.UNKNOWN and not self.unknowns:
            raise ValueError("UNKNOWN element binding requires unknowns")
        if self.status is PCBIntentBindingStatus.CONFLICT and not self.conflicts:
            raise ValueError("CONFLICT element binding requires conflicts")
        if self.status is PCBIntentBindingStatus.BOUND:
            source_identity = (
                self.circuit_block_id,
                self.circuit_component_id,
                self.circuit_pin_id,
                self.circuit_net_id,
                self.schematic_component_instance_id,
                self.schematic_pin_binding_id,
                self.schematic_net_id,
            )
            physical_identity = (
                self.pcb_component_id,
                self.pcb_footprint_id,
                *self.pcb_pad_ids,
                *self.pcb_track_ids,
                *self.pcb_via_ids,
                *self.pcb_zone_ids,
            )
            if not any(value is not None for value in source_identity):
                raise ValueError("BOUND element binding requires explicit circuit or schematic identity")
            if not any(value is not None for value in physical_identity):
                raise ValueError("BOUND element binding requires explicit PCB identity")
            if len(self.pcb_pad_ids) > 1 and self.schematic_pin_binding_id is None and self.circuit_pin_id is None:
                raise ValueError("multiple PCB pads require an explicit pin binding")
            if self.pcb_artifact_id is None or self.pcb_artifact_fingerprint is None or self.readback_fingerprint is None:
                raise ValueError("BOUND element binding requires current PCB artifact read-back")
            observed = {(item.object_kind, item.object_id) for item in self.readback_objects}
            required_objects = set()
            if self.pcb_component_id is not None:
                required_objects.add((PCBReadbackObjectKind.COMPONENT, self.pcb_component_id))
            if self.pcb_footprint_id is not None:
                required_objects.add((PCBReadbackObjectKind.FOOTPRINT, self.pcb_footprint_id))
            required_objects.update((PCBReadbackObjectKind.PAD, item) for item in self.pcb_pad_ids)
            required_objects.update((PCBReadbackObjectKind.TRACK, item) for item in self.pcb_track_ids)
            required_objects.update((PCBReadbackObjectKind.VIA, item) for item in self.pcb_via_ids)
            required_objects.update((PCBReadbackObjectKind.ZONE, item) for item in self.pcb_zone_ids)
            if not required_objects or not required_objects.issubset(observed):
                raise ValueError("BOUND element binding requires every PCB identity to exist in read-back")
            if any(
                item.pcb_artifact_id != self.pcb_artifact_id
                or item.pcb_artifact_fingerprint != self.pcb_artifact_fingerprint
                or item.readback_fingerprint != self.readback_fingerprint
                for item in self.readback_objects
            ):
                raise ValueError("read-back object does not match the bound PCB artifact")
        return self


# Phase5 uses the domain-neutral name; retain the intent-specific name above for
# persisted callers and source compatibility.
PCBSourceBinding = PCBIntentSourceBinding


class PCBEngineeringState(PCBContract):
    project_id: str
    revision: str
    source_artifact: PCBArtifactReference
    source_artifact_fingerprint: str
    board: PCBBoardProjection
    stackup: PCBStackupProjection
    components: tuple[PCBComponentProjection, ...]
    footprints: tuple[PCBFootprintProjection, ...]
    nets: tuple[PCBNetProjection, ...]
    power_nets: tuple[str, ...]
    signal_classes: tuple[PCBSignalClassProjection, ...]
    differential_pairs: tuple[PCBDifferentialPairProjection, ...]
    clocks: tuple[str, ...]
    analog_nets: tuple[str, ...]
    high_current_nets: tuple[str, ...]
    interfaces: tuple[PCBInterfaceProjection, ...]
    placement: tuple[PCBPlacementProjection, ...]
    routing: tuple[PCBRouteSegmentProjection, ...]
    route_paths: tuple[PCBRoutePathProjection, ...]
    planes: tuple[PCBPlaneProjection, ...]
    keepouts: tuple[PCBKeepoutProjection, ...] = ()
    vias: tuple[PCBViaProjection, ...]
    constraints: tuple[str, ...]
    unknowns: tuple[PCBUnknown, ...]
    conflicts: tuple[PCBConflict, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    design_truth_fingerprint: str | None = None
    hardware_truth_fingerprint: str | None = None
    status: PCBFoundationStatus = PCBFoundationStatus.CURRENT
    stale_reason: str | None = None
    affected_objects: tuple[str, ...] = ()
    required_reverification: tuple[str, ...] = ()
    decoupling_relations: tuple[PCBDecouplingRelation, ...] = ()
    intent_source_binding: PCBIntentSourceBinding | None = None
    element_bindings: tuple[PCBElementBinding, ...] = ()

    @field_validator("project_id", "revision", mode="before")
    @classmethod
    def _state_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("source_artifact_fingerprint", "design_truth_fingerprint", "hardware_truth_fingerprint", mode="before")
    @classmethod
    def _state_fingerprints(cls, value: object, info) -> str | None:
        return None if value is None else _fp(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _state_status(cls, value: object) -> PCBFoundationStatus:
        return value if isinstance(value, PCBFoundationStatus) else PCBFoundationStatus(value)

    @model_validator(mode="after")
    def _state_binding(self) -> PCBEngineeringState:
        if self.source_artifact.artifact_type is not PCBArtifactType.BOARD:
            raise ValueError("PCB engineering state requires a BOARD artifact")
        if self.source_artifact.project_id != self.project_id or self.source_artifact_fingerprint != self.source_artifact.content_fingerprint:
            raise ValueError("PCB engineering state artifact binding mismatch")
        if self.status is PCBFoundationStatus.CURRENT and self.stale_reason is not None:
            raise ValueError("current PCB engineering state cannot have stale reason")
        if self.status is PCBFoundationStatus.CURRENT and self.intent_source_binding is not None and self.intent_source_binding.status is not PCBIntentBindingStatus.BOUND:
            raise ValueError("current PCB engineering state requires a bound source lineage")
        if self.status is PCBFoundationStatus.STALE and not self.stale_reason:
            raise ValueError("stale PCB engineering state requires reason")
        if len({item.component_id for item in self.components}) != len(self.components):
            raise ValueError("PCB component identities must be unique")
        if any(item.component_id == item.reference for item in self.components):
            raise ValueError("PCB component display reference cannot be authoritative identity")
        if len({item.net_id for item in self.nets}) != len(self.nets):
            raise ValueError("PCB net identities must be unique")
        if any(self.intent_source_binding is None or item.intent_id != self.intent_source_binding.intent_id for item in self.element_bindings):
            raise ValueError("PCB element binding references an unknown intent")
        return self

    @property
    def state_fingerprint(self) -> str:
        return self.fingerprint

    @property
    def pcb_artifact_fingerprint(self) -> str:
        """Canonical PCB artifact identity; source_artifact_fingerprint is legacy."""

        return self.source_artifact.content_fingerprint

    def mark_stale(self, *, reason: str, affected_objects: tuple[str, ...], required_reverification: tuple[str, ...]) -> PCBEngineeringState:
        values = self.model_dump(mode="python", exclude={"fingerprint"})
        values.update(
            status=PCBFoundationStatus.STALE,
            stale_reason=_text(reason, "stale_reason", 512),
            affected_objects=tuple(affected_objects),
            required_reverification=tuple(required_reverification),
        )
        return PCBEngineeringState.create(**values)


class PCBConstraint(PCBContract):
    constraint_id: str
    constraint_type: PCBConstraintType
    scope: str
    target_ids: tuple[str, ...]
    requirements: tuple[str, ...]
    source: PCBConstraintSource
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    applicability: PCBApplicability
    revision: str
    source_fingerprint: str
    evaluation_method: str | None = None
    threshold: float | None = None
    threshold_unit: str | None = None
    comparison: str | None = None
    enforcement_mode: PCBConstraintEnforcementMode = PCBConstraintEnforcementMode.ADVISORY
    return_path_signal_ids: tuple[str, ...] = ()
    return_path_net_ids: tuple[str, ...] = ()
    return_path_reference_net_id: str | None = None
    return_path_reference_layers: tuple[str, ...] = ()
    return_path_requirement: str | None = None
    return_path_scope: str | None = None
    power_stage_evidence: tuple[PCBPowerStageEvidence, ...] = ()
    power_loop_rules: tuple[PCBPowerLoopRule, ...] = ()
    differential_pair_bindings: tuple[PCBDifferentialPairBinding, ...] = ()
    differential_pair_rules: tuple[PCBDifferentialPairRule, ...] = ()
    differential_reference_requirement: str | None = None
    differential_impedance_ohms: float | None = None
    intent_source_binding: PCBIntentSourceBinding | None = None
    element_bindings: tuple[PCBElementBinding, ...] = ()
    source_binding_fingerprint: str | None = None
    circuit_requirement_refs: tuple[str, ...] = ()
    circuit_decision_refs: tuple[str, ...] = ()
    circuit_block_refs: tuple[str, ...] = ()
    calculation_record_refs: tuple[str, ...] = ()
    verification_requirement_refs: tuple[str, ...] = ()
    expected_evidence_scope: tuple[str, ...] = ()

    @field_validator("constraint_id", "scope", "revision", mode="before")
    @classmethod
    def _constraint_text(cls, value: object, info) -> str:
        return _id(value, info.field_name) if info.field_name != "scope" else _text(value, info.field_name)

    @field_validator("constraint_type", mode="before")
    @classmethod
    def _constraint_type(cls, value: object) -> PCBConstraintType:
        return value if isinstance(value, PCBConstraintType) else PCBConstraintType(value)

    @field_validator("source", mode="before")
    @classmethod
    def _constraint_source(cls, value: object) -> PCBConstraintSource:
        return value if isinstance(value, PCBConstraintSource) else PCBConstraintSource(value)

    @field_validator("applicability", mode="before")
    @classmethod
    def _constraint_applicability(cls, value: object) -> PCBApplicability:
        return value if isinstance(value, PCBApplicability) else PCBApplicability(value)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _constraint_source_fingerprint(cls, value: object) -> str:
        return _fp(value, "source_fingerprint")

    @field_validator("source_binding_fingerprint", mode="before")
    @classmethod
    def _source_binding_fp(cls, value: object) -> str | None:
        return None if value is None else _fp(value, "source_binding_fingerprint")

    @field_validator(
        "circuit_requirement_refs", "circuit_decision_refs", "circuit_block_refs",
        "calculation_record_refs", "verification_requirement_refs", "expected_evidence_scope",
        mode="before",
    )
    @classmethod
    def _source_refs(cls, value: object, info) -> tuple[str, ...]:
        values = tuple(_id(item, info.field_name) for item in _tuple(value, info.field_name))
        if len(values) != len(set(values)):
            raise ValueError(f"{info.field_name} must be unique")
        return values

    @field_validator(
        "evaluation_method", "threshold_unit", "comparison", "return_path_requirement", "return_path_scope",
        "differential_reference_requirement",
        mode="before",
    )
    @classmethod
    def _evaluation_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, info.field_name, 128)

    @field_validator("return_path_signal_ids", "return_path_net_ids", "return_path_reference_layers", mode="before")
    @classmethod
    def _return_path_ids(cls, value: object, info) -> tuple[str, ...]:
        return tuple(_id(item, info.field_name) for item in _tuple(value, info.field_name))

    @field_validator("return_path_reference_net_id", mode="before")
    @classmethod
    def _return_path_reference_net(cls, value: object) -> str | None:
        return None if value is None else _id(value, "return_path_reference_net_id")

    @field_validator("enforcement_mode", mode="before")
    @classmethod
    def _enforcement(cls, value: object) -> PCBConstraintEnforcementMode:
        return value if isinstance(value, PCBConstraintEnforcementMode) else PCBConstraintEnforcementMode(value)

    @model_validator(mode="after")
    def _evaluation_binding(self) -> PCBConstraint:
        if self.threshold is not None and (
            self.evaluation_method is None
            or self.threshold_unit is None
            or self.comparison is None
        ):
            raise ValueError("machine-evaluable constraint threshold is incomplete")
        if self.threshold is not None and self.threshold < 0:
            raise ValueError("constraint threshold must not be negative")
        if self.constraint_type is not PCBConstraintType.SWITCHING_POWER and (
            self.power_stage_evidence or self.power_loop_rules
        ):
            raise ValueError("power-loop evidence requires SWITCHING_POWER constraint")
        if self.constraint_type is not PCBConstraintType.DIFFERENTIAL_PAIR and (
            self.differential_pair_bindings
            or self.differential_pair_rules
            or self.differential_reference_requirement is not None
            or self.differential_impedance_ohms is not None
        ):
            raise ValueError("differential-pair evidence requires DIFFERENTIAL_PAIR constraint")
        if self.differential_impedance_ohms is not None and self.differential_impedance_ohms <= 0:
            raise ValueError("differential impedance must be positive")
        target_ids = set(self.target_ids)
        if any(item.pair_id not in target_ids for item in self.differential_pair_bindings):
            raise ValueError("differential pair binding is outside constraint targets")
        if any(item.project_id != self._project_id_from_evidence() for item in self.evidence_refs):
            raise ValueError("constraint evidence project binding mismatch")
        differential_evidence = tuple(
            item
            for binding in self.differential_pair_bindings
            for item in binding.evidence_refs
        ) + tuple(
            item
            for rule in self.differential_pair_rules
            for item in rule.evidence_refs
        )
        if any(item.project_id != self._project_id_from_evidence() for item in differential_evidence):
            raise ValueError("differential pair evidence project binding mismatch")
        for stage in self.power_stage_evidence:
            if self.constraint_id not in stage.constraint_ids:
                raise ValueError("power-stage evidence must bind the current constraint")
        if self.intent_source_binding is not None:
            if self.source_binding_fingerprint != self.intent_source_binding.fingerprint:
                raise ValueError("constraint source binding fingerprint mismatch")
            if not (
                self.circuit_requirement_refs
                or self.circuit_decision_refs
                or self.circuit_block_refs
                or self.calculation_record_refs
                or self.verification_requirement_refs
            ):
                raise ValueError("bound PCB constraint requires an upstream source reference")
            if not self.expected_evidence_scope:
                raise ValueError("bound PCB constraint requires an evidence scope")
            if any(item.intent_id != self.intent_source_binding.intent_id for item in self.element_bindings):
                raise ValueError("constraint element binding intent mismatch")
            if self.intent_source_binding.status is PCBIntentBindingStatus.BOUND:
                if self.evaluation_method is None:
                    raise ValueError("bound PCB constraint requires an evaluation method")
                if not self.evidence_refs:
                    raise ValueError("bound PCB constraint requires verification evidence")
        return self

    def _project_id_from_evidence(self) -> str:
        return self.evidence_refs[0].project_id if self.evidence_refs else ""


class PCBConstraintSet(PCBContract):
    project_id: str
    revision: str
    state_fingerprint: str
    constraints: tuple[PCBConstraint, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    status: PCBFoundationStatus = PCBFoundationStatus.CURRENT
    stale_reason: str | None = None
    affected_objects: tuple[str, ...] = ()
    required_reverification: tuple[str, ...] = ()
    intent_source_binding: PCBIntentSourceBinding | None = None
    element_bindings: tuple[PCBElementBinding, ...] = ()

    @field_validator("project_id", "revision", mode="before")
    @classmethod
    def _constraint_set_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("state_fingerprint", mode="before")
    @classmethod
    def _state_fp(cls, value: object) -> str:
        return _fp(value, "state_fingerprint")

    @field_validator("status", mode="before")
    @classmethod
    def _constraint_set_status(cls, value: object) -> PCBFoundationStatus:
        return value if isinstance(value, PCBFoundationStatus) else PCBFoundationStatus(value)

    @model_validator(mode="after")
    def _constraint_set_binding(self) -> PCBConstraintSet:
        if any(item.revision != self.revision for item in self.constraints):
            raise ValueError("PCB constraint revision binding mismatch")
        if self.status is PCBFoundationStatus.CURRENT and self.stale_reason is not None:
            raise ValueError("current PCB constraint set cannot have stale reason")
        if self.status is PCBFoundationStatus.CURRENT and self.intent_source_binding is not None and self.intent_source_binding.status is not PCBIntentBindingStatus.BOUND:
            raise ValueError("current PCB constraint set requires a bound source lineage")
        if self.status is PCBFoundationStatus.STALE and not self.stale_reason:
            raise ValueError("stale PCB constraint set requires reason")
        if any(item.intent_source_binding != self.intent_source_binding or item.element_bindings != self.element_bindings for item in self.constraints):
            raise ValueError("PCB constraint binding identity mismatch")
        return self

    @property
    def constraint_set_fingerprint(self) -> str:
        return self.fingerprint

    def mark_stale(self, *, reason: str, affected_objects: tuple[str, ...], required_reverification: tuple[str, ...]) -> PCBConstraintSet:
        values = self.model_dump(mode="python", exclude={"fingerprint"})
        values.update(
            status=PCBFoundationStatus.STALE,
            stale_reason=_text(reason, "stale_reason", 512),
            affected_objects=tuple(affected_objects),
            required_reverification=tuple(required_reverification),
        )
        return PCBConstraintSet.create(**values)


class PCBVerificationPlan(PCBContract):
    project_id: str
    revision: str
    pcb_state_fingerprint: str
    constraint_set_fingerprint: str
    required_verifiers: tuple[str, ...]
    optional_verifiers: tuple[str, ...]
    not_applicable_verifiers: tuple[str, ...]
    unknown_applicability: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    # G7 final closure extends the existing plan instead of introducing a
    # second verification-plan contract.  Defaults preserve persisted G7-A/B
    # plans created before engineering verification was added.
    engineering_required_verifiers: tuple[str, ...] = ()
    engineering_optional_verifiers: tuple[str, ...] = ()
    engineering_not_applicable_verifiers: tuple[str, ...] = ()
    engineering_unknown_applicability: tuple[str, ...] = ()
    engineering_evidence_fingerprint: str | None = None
    status: PCBFoundationStatus = PCBFoundationStatus.CURRENT
    stale_reason: str | None = None
    affected_objects: tuple[str, ...] = ()
    required_reverification: tuple[str, ...] = ()

    @field_validator("project_id", "revision", mode="before")
    @classmethod
    def _plan_ids(cls, value: object, info) -> str:
        return _id(value, info.field_name)

    @field_validator("pcb_state_fingerprint", "constraint_set_fingerprint", mode="before")
    @classmethod
    def _plan_fingerprints(cls, value: object, info) -> str:
        return _fp(value, info.field_name)

    @field_validator("engineering_evidence_fingerprint", mode="before")
    @classmethod
    def _engineering_evidence_fingerprint(cls, value: object) -> str | None:
        return None if value is None else _fp(value, "engineering_evidence_fingerprint")

    @field_validator("status", mode="before")
    @classmethod
    def _plan_status(cls, value: object) -> PCBFoundationStatus:
        return value if isinstance(value, PCBFoundationStatus) else PCBFoundationStatus(value)

    @model_validator(mode="after")
    def _plan_binding(self) -> PCBVerificationPlan:
        if self.status is PCBFoundationStatus.CURRENT and self.stale_reason is not None:
            raise ValueError("current PCB verification plan cannot have stale reason")
        if self.status is PCBFoundationStatus.STALE and not self.stale_reason:
            raise ValueError("stale PCB verification plan requires reason")
        return self

    @property
    def plan_fingerprint(self) -> str:
        return self.fingerprint

    def mark_stale(self, *, reason: str, affected_objects: tuple[str, ...], required_reverification: tuple[str, ...]) -> PCBVerificationPlan:
        values = self.model_dump(mode="python", exclude={"fingerprint"})
        values.update(
            status=PCBFoundationStatus.STALE,
            stale_reason=_text(reason, "stale_reason", 512),
            affected_objects=tuple(affected_objects),
            required_reverification=tuple(required_reverification),
        )
        return PCBVerificationPlan.create(**values)


class PCBEngineeringProjector:
    """Project one parsed G5 board into a deterministic physical state.

    This projector never writes a board and never changes DesignTruth. When an
    authoritative match is unavailable it emits UNKNOWN or CONFLICT metadata.
    """

    def project(
        self,
        *,
        project_id: str,
        revision: str,
        source_artifact: PCBArtifactReference,
        parsed: UnifiedPCBModel,
        evidence_refs: tuple[PCBFoundationEvidence, ...] = (),
        g5_request: Any | None = None,
        design_truth: Any | None = None,
        hardware_truth: Any | None = None,
        generation_receipt: Any | None = None,
        differential_pairs: tuple[PCBDifferentialPairProjection, ...] = (),
    ) -> PCBEngineeringState:
        checked_artifact = PCBArtifactReference.model_validate(source_artifact.model_dump(mode="python"))
        if checked_artifact.project_id != project_id:
            raise ValueError("PCB artifact project binding mismatch")
        if not evidence_refs:
            evidence_refs = (
                PCBFoundationEvidence.create(
                    evidence_id=_stable_id("evidence", project_id, checked_artifact.artifact_id),
                    project_id=project_id,
                    source_type=PCBFoundationEvidenceSource.ARTIFACT,
                    source_id=checked_artifact.artifact_id,
                    source_fingerprint=checked_artifact.content_fingerprint,
                    claim="Observed from the current PCB artifact.",
                    locator=f"artifact://pcb/{checked_artifact.artifact_id}",
                ),
            )
        evidence = tuple(PCBFoundationEvidence.model_validate(item.model_dump(mode="python")) for item in evidence_refs)
        if any(item.project_id != project_id for item in evidence):
            raise ValueError("PCB foundation evidence project binding mismatch")
        g5_components = {item.reference: item for item in getattr(g5_request, "components", ())}
        truth_components = tuple(getattr(design_truth, "components", ()))
        truth_pins = tuple(getattr(design_truth, "pins", ()))
        truth_nets = tuple(getattr(design_truth, "nets", ()))
        truth_signals = {item.signal_id: item for item in getattr(design_truth, "signals", ())}
        truth_interfaces = tuple(getattr(design_truth, "interfaces", ()))
        truth_power_domains = tuple(getattr(design_truth, "power_domains", ()))
        explicit_ground_names = {
            str(getattr(item, "ground_net", "")).strip().casefold()
            for item in truth_power_domains
            if str(getattr(item, "ground_net", "")).strip()
        }
        design_evidence = tuple(item for item in evidence if item.source_type is PCBFoundationEvidenceSource.DESIGN_TRUTH)
        hardware_evidence = tuple(item for item in evidence if item.source_type is PCBFoundationEvidenceSource.HARDWARE_TRUTH)
        explicit_differential_pairs = tuple(
            PCBDifferentialPairProjection.model_validate(
                item.model_dump(mode="python") if isinstance(item, PCBDifferentialPairProjection) else item
            )
            for item in (
                tuple(differential_pairs)
                or tuple(getattr(design_truth, "differential_pairs", ()))
            )
        )
        if any(
            item.project_id != project_id
            for pair in explicit_differential_pairs
            for item in pair.evidence_refs
        ):
            raise ValueError("differential pair evidence project binding mismatch")

        component_ids: dict[str, str] = {}
        component_truth_matches: dict[str, Any] = {}
        unknowns: list[PCBUnknown] = []
        conflicts: list[PCBConflict] = []
        components: list[PCBComponentProjection] = []
        footprints: list[PCBFootprintProjection] = []
        pads_by_ref_pin: dict[tuple[str, str], PCBPadProjection] = {}
        for component in sorted(parsed.components, key=lambda item: item.reference):
            g5 = g5_components.get(component.reference)
            match = self._match_design_component(component, g5, truth_components)
            truth_component_id = None if match is None else match.component_id
            component_id = _stable_id(
                "component",
                project_id,
                component.reference,
                truth_component_id or "unknown",
            )
            component_ids[component.reference] = component_id
            if match is not None:
                component_truth_matches[component.reference] = match
            else:
                unknowns.append(
                    PCBUnknown.create(
                        unknown_id=_stable_id("unknown", "component", project_id, component.reference),
                        category="component_identity",
                        subject=component.reference,
                        reason="No DesignTruth component identity could be matched without fuzzy inference.",
                        affected_objects=(component_id,),
                        evidence_refs=evidence,
                    )
                )
            footprint_id = _stable_id("footprint", project_id, component_id, component.footprint)
            pad_models: list[PCBPadProjection] = []
            for pin in component.pins:
                truth_pin = next(
                    (
                        item
                        for item in truth_pins
                        if (
                            truth_component_id is not None
                            and item.component_id == truth_component_id
                            and item.pin_number == pin.number
                        )
                    ),
                    None,
                )
                pad_id = _stable_id("pad", project_id, component_id, pin.number)
                net_id = None
                for candidate in parsed.nets:
                    if any(node.reference == component.reference and node.pin == pin.number for node in candidate.nodes):
                        binding = next(
                            (item for item in truth_nets if getattr(item, "net_name", None) == candidate.name),
                            None,
                        )
                        net_id = _stable_id(
                            "net",
                            project_id,
                            candidate.name,
                            "unknown" if binding is None else binding.binding_id,
                        )
                        break
                pad = PCBPadProjection.create(
                    pad_id=pad_id,
                    pin_id=None if truth_pin is None else truth_pin.pin_id,
                    number=pin.number,
                    net_id=net_id,
                    evidence_refs=evidence if truth_pin is None else tuple(dict.fromkeys((*evidence, *design_evidence))),
                    x_mm=None if pin.position is None else float(pin.position.x_mm),
                    y_mm=None if pin.position is None else float(pin.position.y_mm),
                    geometry_source="PAD" if pin.position is not None else "UNAVAILABLE",
                )
                pads_by_ref_pin[(component.reference, pin.number)] = pad
                pad_models.append(pad)
                if truth_pin is None and design_truth is not None:
                    unknowns.append(
                        PCBUnknown.create(
                            unknown_id=_stable_id("unknown", "pin", project_id, component.reference, pin.number),
                            category="pin_pad_mapping",
                            subject=f"{component.reference}:{pin.number}",
                            reason="PCB pad could not be bound to an authoritative DesignTruth pin.",
                            affected_objects=(pad_id,),
                            evidence_refs=evidence,
                        )
                    )
            component_evidence = evidence if match is None else tuple(dict.fromkeys((*evidence, *design_evidence)))
            components.append(
                PCBComponentProjection.create(
                    component_id=component_id,
                    reference=component.reference,
                    value=component.value,
                    symbol_id=None if match is None else match.component_id,
                    footprint_id=footprint_id,
                    pads=tuple(pad_models),
                    evidence_refs=component_evidence,
                )
            )
            footprints.append(
                PCBFootprintProjection.create(
                    footprint_id=footprint_id,
                    component_id=component_id,
                    reference=component.reference,
                    library_identifier=component.footprint,
                    pads=tuple(pad_models),
                    evidence_refs=component_evidence,
                )
            )

        net_bindings = {item.net_name: item for item in truth_nets}
        net_projections: list[PCBNetProjection] = []
        signal_projections: list[PCBSignalClassProjection] = []
        for parsed_net in sorted(parsed.nets, key=lambda item: item.name):
            binding = net_bindings.get(parsed_net.name)
            net_id = _stable_id(
                "net",
                project_id,
                parsed_net.name,
                "unknown" if binding is None else binding.binding_id,
            )
            signal_id = None if binding is None else binding.signal
            member_pad_ids = tuple(
                sorted(
                    pads_by_ref_pin[(node.reference, node.pin)].pad_id
                    for node in parsed_net.nodes
                    if (node.reference, node.pin) in pads_by_ref_pin
                )
            )
            status = PCBConnectivityStatus.MATCH if binding is not None else PCBConnectivityStatus.UNKNOWN
            net_evidence = evidence if binding is None else tuple(dict.fromkeys((*evidence, *design_evidence)))
            net_projections.append(
                PCBNetProjection.create(
                    net_id=net_id,
                    name=parsed_net.name,
                    member_pad_ids=member_pad_ids,
                    signal_id=signal_id,
                    design_truth_status=status,
                    evidence_refs=net_evidence,
                )
            )
            signal = None if signal_id is None else truth_signals.get(signal_id)
            signal_class = self._signal_class(signal, ground_names=explicit_ground_names)
            signal_projections.append(
                PCBSignalClassProjection.create(
                    signal_id=signal_id or _stable_id("signal", project_id, parsed_net.name),
                    net_id=net_id,
                    signal_class=signal_class,
                    protocol=None if signal is None else signal.protocol,
                    evidence_refs=net_evidence,
                )
            )
            if signal_class is PCBSignalClass.UNKNOWN:
                unknowns.append(
                    PCBUnknown.create(
                        unknown_id=_stable_id("unknown", "signal", project_id, parsed_net.name),
                        category="signal_classification",
                        subject=parsed_net.name,
                        reason="No evidence-bound signal class exists for this PCB net.",
                        affected_objects=(net_id,),
                        evidence_refs=net_evidence,
                    )
                )

        parsed_net_by_name = {item.name: item for item in net_projections}
        power_nets = tuple(
            sorted(
                item.net_id
                for item in net_projections
                if next(item2 for item2 in signal_projections if item2.net_id == item.net_id).signal_class
                in {PCBSignalClass.POWER, PCBSignalClass.GROUND}
            )
        )
        clocks = tuple(sorted(item.net_id for item in signal_projections if item.signal_class is PCBSignalClass.CLOCK))
        analog_nets = tuple(sorted(item.net_id for item in signal_projections if item.signal_class is PCBSignalClass.ANALOG))
        high_current_nets = tuple(sorted(item.net_id for item in signal_projections if item.signal_class is PCBSignalClass.HIGH_CURRENT))

        placement: list[PCBPlacementProjection] = []
        for component in parsed.components:
            component_id = component_ids[component.reference]
            footprint_id = _stable_id("footprint", project_id, component_id, component.footprint)
            placement.append(
                PCBPlacementProjection.create(
                    placement_id=_stable_id("placement", project_id, component_id),
                    component_id=component_id,
                    footprint_id=footprint_id,
                    x=float(component.position.x_mm),
                    y=float(component.position.y_mm),
                    rotation=float(component.rotation),
                    side=component.layer,
                    region=None,
                    evidence_refs=evidence,
                )
            )

        routes: list[PCBRouteSegmentProjection] = []
        route_segments_by_net: dict[str, list[str]] = {}
        for index, segment in enumerate(parsed.tracks):
            if segment.net_name is None or segment.net_name not in parsed_net_by_name:
                unknowns.append(
                    PCBUnknown.create(
                        unknown_id=_stable_id("unknown", "route", project_id, index),
                        category="route_net",
                        subject=f"segment:{index}",
                        reason="PCB route segment has no authoritative net binding.",
                        affected_objects=(),
                        evidence_refs=evidence,
                    )
                )
                continue
            net_id = parsed_net_by_name[segment.net_name].net_id
            segment_id = _stable_id("route-segment", project_id, net_id, index)
            route_segments_by_net.setdefault(net_id, []).append(segment_id)
            routes.append(
                PCBRouteSegmentProjection.create(
                    segment_id=segment_id,
                    net_id=net_id,
                    layer=segment.layer,
                    width_mm=float(segment.width_mm),
                    start=(float(segment.start.x_mm), float(segment.start.y_mm)),
                    end=(float(segment.end.x_mm), float(segment.end.y_mm)),
                    via_ids=(),
                    evidence_refs=evidence,
                )
            )
        route_paths = tuple(
            PCBRoutePathProjection.create(
                route_id=_stable_id("route", project_id, net_id),
                net_id=net_id,
                segment_ids=tuple(segment_ids),
                evidence_refs=evidence,
            )
            for net_id, segment_ids in sorted(route_segments_by_net.items())
        )

        planes: list[PCBPlaneProjection] = []
        for index, zone in enumerate(parsed.zones):
            net_id = None if zone.net_name is None else parsed_net_by_name.get(zone.net_name, None)
            polygons = ()
            if zone.polygon_points:
                polygons = (
                    PCBPolygonProjection.create(
                        polygon_id=_stable_id("zone-polygon", project_id, index),
                        points=tuple(zone.polygon_points),
                        holes=(),
                    ),
                )
            planes.append(
                PCBPlaneProjection.create(
                    plane_id=_stable_id("plane", project_id, index),
                    net_id=None if net_id is None else net_id.net_id,
                    layer=zone.layers[0],
                    geometry_ref=f"artifact://pcb/{checked_artifact.artifact_id}/zone/{index}",
                    polygons=polygons,
                    geometry_quality=PCBGeometryQuality.EXACT if polygons else PCBGeometryQuality.UNAVAILABLE,
                    evidence_refs=evidence,
                )
            )
        vias = tuple(
            PCBViaProjection.create(
                via_id=_stable_id("via", project_id, index),
                net_id=None if via.net_name is None or via.net_name not in parsed_net_by_name else parsed_net_by_name[via.net_name].net_id,
                x=float(via.position.x_mm),
                y=float(via.position.y_mm),
                from_layer=via.layers[0] if via.layers else "UNKNOWN",
                to_layer=via.layers[-1] if via.layers else "UNKNOWN",
                diameter_mm=float(via.diameter_mm),
                drill_mm=float(via.drill_mm),
                evidence_refs=evidence,
            )
            for index, via in enumerate(parsed.vias)
        )

        interfaces = self._interfaces(truth_interfaces, truth_signals, net_projections, evidence, design_evidence)
        board_definition = getattr(g5_request, "board", None)
        board_evidence = tuple(dict.fromkeys((*evidence, *hardware_evidence)))
        board = PCBBoardProjection.create(
            board_id=_stable_id("board", project_id, checked_artifact.content_fingerprint),
            outline_kind=None if board_definition is None else board_definition.outline_kind,
            width_mm=None if board_definition is None else float(board_definition.width_mm),
            height_mm=None if board_definition is None else float(board_definition.height_mm),
            origin_x_mm=None if board_definition is None else float(board_definition.origin_x_mm),
            origin_y_mm=None if board_definition is None else float(board_definition.origin_y_mm),
            units="mm",
            layers=tuple(item.name for item in parsed.layers),
            copper_layers=tuple(item.name for item in parsed.layers if item.type.casefold() == "signal"),
            evidence_refs=board_evidence,
        )
        stackup = PCBStackupProjection.create(
            stackup_id=_stable_id("stackup", project_id, checked_artifact.content_fingerprint),
            layers=tuple(item.name for item in parsed.layers),
            material=None,
            thickness_mm=None if board_definition is None or board_definition.board_thickness_mm is None else float(board_definition.board_thickness_mm),
            copper_weight=None,
            evidence_refs=board_evidence,
        )
        if stackup.material is None:
            unknowns.append(
                PCBUnknown.create(
                    unknown_id=_stable_id("unknown", "stackup", project_id),
                    category="stackup_material",
                    subject=stackup.stackup_id,
                    reason="PCB artifact and G5 input do not establish dielectric material or copper weight.",
                    affected_objects=(stackup.stackup_id,),
                    evidence_refs=board_evidence,
                )
            )
        if not planes:
            unknowns.append(
                PCBUnknown.create(
                    unknown_id=_stable_id("unknown", "plane", project_id),
                    category="reference_plane",
                    subject="board",
                    reason="No copper zone/reference plane was present in the current artifact.",
                    affected_objects=(),
                    evidence_refs=evidence,
                )
            )
        self._compare_g5_connectivity(
            project_id=project_id,
            parsed=parsed,
            net_projections=net_projections,
            g5_request=g5_request,
            evidence=evidence,
            g5_evidence=tuple(item for item in evidence if item.source_type is PCBFoundationEvidenceSource.G5_RECEIPT),
            conflicts=conflicts,
        )
        self._compare_design_truth_connectivity(
            project_id=project_id,
            parsed=parsed,
            net_projections=net_projections,
            design_truth=design_truth,
            evidence=evidence,
            design_evidence=design_evidence,
            conflicts=conflicts,
        )
        return PCBEngineeringState.create(
            project_id=project_id,
            revision=revision,
            source_artifact=checked_artifact,
            source_artifact_fingerprint=checked_artifact.content_fingerprint,
            board=board,
            stackup=stackup,
            components=tuple(components),
            footprints=tuple(footprints),
            nets=tuple(net_projections),
            power_nets=power_nets,
            signal_classes=tuple(signal_projections),
            differential_pairs=explicit_differential_pairs,
            clocks=clocks,
            analog_nets=analog_nets,
            high_current_nets=high_current_nets,
            interfaces=interfaces,
            placement=tuple(placement),
            routing=tuple(routes),
            route_paths=route_paths,
            planes=tuple(planes),
            vias=vias,
            constraints=(),
            unknowns=tuple(unknowns),
            conflicts=tuple(conflicts),
            evidence_refs=tuple(sorted(evidence, key=lambda item: item.evidence_id)),
            design_truth_fingerprint=None if design_truth is None else design_truth.fingerprint,
            hardware_truth_fingerprint=None if hardware_truth is None else hardware_truth.fingerprint,
        )

    @staticmethod
    def _match_design_component(component: Any, g5_component: Any, truth_components: tuple[Any, ...]) -> Any | None:
        if g5_component is None:
            return None
        candidates = [
            item
            for item in truth_components
            if getattr(item, "symbol_binding", None) is not None
            and getattr(item, "symbol_binding", None) == getattr(g5_component, "symbol_binding", None)
        ]
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _signal_class(
        signal: Any | None,
        *,
        ground_names: set[str] | frozenset[str] = frozenset(),
    ) -> PCBSignalClass:
        if signal is None:
            return PCBSignalClass.UNKNOWN
        value = str(getattr(signal, "type", "")).strip().upper()
        mapping = {
            "DIGITAL": PCBSignalClass.DIGITAL,
            "ANALOG": PCBSignalClass.ANALOG,
            "CLOCK": PCBSignalClass.CLOCK,
            "POWER": PCBSignalClass.POWER,
            "GROUND": PCBSignalClass.GROUND,
            "DIFFERENTIAL": PCBSignalClass.DIFFERENTIAL,
            "HIGH_CURRENT": PCBSignalClass.HIGH_CURRENT,
        }
        if value in mapping and value != "ELECTRICAL":
            return mapping[value]
        protocol = str(getattr(signal, "protocol", "")).strip().upper()
        if protocol == "POWER":
            name = str(getattr(signal, "name", "")).strip().casefold()
            return PCBSignalClass.GROUND if name in ground_names else PCBSignalClass.POWER
        return PCBSignalClass.UNKNOWN

    @staticmethod
    def _interfaces(truth_interfaces: tuple[Any, ...], truth_signals: dict[str, Any], nets: list[PCBNetProjection], evidence: tuple[PCBFoundationEvidence, ...], design_evidence: tuple[PCBFoundationEvidence, ...]) -> tuple[PCBInterfaceProjection, ...]:
        result: list[PCBInterfaceProjection] = []
        for item in truth_interfaces:
            signal_ids = tuple(
                sorted(
                    signal_id
                    for signal_id, signal in truth_signals.items()
                    if str(getattr(signal, "protocol", "")).casefold() == str(getattr(item, "protocol", "")).casefold()
                )
            )
            net_ids = tuple(sorted(net.net_id for net in nets if net.signal_id in signal_ids))
            result.append(
                PCBInterfaceProjection.create(
                    interface_id=item.interface_id,
                    protocol=item.protocol,
                    signal_ids=signal_ids,
                    net_ids=net_ids,
                    evidence_refs=tuple(dict.fromkeys((*evidence, *design_evidence))),
                )
            )
        return tuple(result)

    @staticmethod
    def _compare_design_truth_connectivity(
        *,
        project_id: str,
        parsed: UnifiedPCBModel,
        net_projections: list[PCBNetProjection],
        design_truth: Any | None,
        evidence: tuple[PCBFoundationEvidence, ...],
        design_evidence: tuple[PCBFoundationEvidence, ...],
        conflicts: list[PCBConflict],
    ) -> None:
        if design_truth is None:
            return

        def nodes_for(binding: Any) -> set[tuple[str, str]] | None:
            result: set[tuple[str, str]] = set()
            for raw_node in getattr(binding, "nodes", ()):
                if not isinstance(raw_node, str) or "." not in raw_node:
                    return None
                reference, pin = raw_node.split(".", 1)
                if not reference.strip() or not pin.strip():
                    return None
                result.add((reference.strip(), pin.strip()))
            return result

        expected: dict[str, set[tuple[str, str]]] = {}
        for binding in getattr(design_truth, "nets", ()):
            name = str(getattr(binding, "net_name", "")).strip()
            nodes = nodes_for(binding)
            if name and nodes is not None:
                expected[name] = nodes
        actual = {
            item.name: {(node.reference, node.pin) for node in item.nodes}
            for item in parsed.nets
        }
        for name in sorted(set(expected) & set(actual)):
            if expected[name] == actual[name]:
                continue
            net_id = next(
                (item.net_id for item in net_projections if item.name == name),
                _stable_id("net", project_id, name, "unknown"),
            )
            conflicts.append(
                PCBConflict.create(
                    conflict_id=_stable_id("conflict", project_id, "design-truth-net", name),
                    category="design_truth_connectivity",
                    subject=name,
                    design_truth_value=repr(sorted(expected[name])),
                    pcb_value=repr(sorted(actual[name])),
                    reason="Current PCB artifact connectivity differs from DesignTruth.",
                    affected_objects=(net_id,),
                    evidence_refs=tuple(dict.fromkeys((*evidence, *design_evidence))),
                )
            )

    @staticmethod
    def _compare_g5_connectivity(*, project_id: str, parsed: UnifiedPCBModel, net_projections: list[PCBNetProjection], g5_request: Any | None, evidence: tuple[PCBFoundationEvidence, ...], g5_evidence: tuple[PCBFoundationEvidence, ...], conflicts: list[PCBConflict]) -> None:
        if g5_request is None:
            return
        expected = {item.name: {(endpoint.reference, endpoint.pin_number) for endpoint in item.endpoints} for item in getattr(g5_request, "nets", ())}
        actual = {item.name: {(node.reference, node.pin) for node in item.nodes} for item in parsed.nets}
        for name in sorted(set(expected) & set(actual)):
            if expected[name] != actual[name]:
                net_id = next((item.net_id for item in net_projections if item.name == name), _stable_id("net", project_id, name))
                conflicts.append(
                    PCBConflict.create(
                        conflict_id=_stable_id("conflict", project_id, "net", name),
                        category="connectivity",
                        subject=name,
                        design_truth_value=repr(sorted(expected[name])),
                        pcb_value=repr(sorted(actual[name])),
                        reason="Current G5 request connectivity differs from the parsed PCB artifact.",
                        affected_objects=(net_id,),
                        evidence_refs=tuple(dict.fromkeys((*evidence, *g5_evidence))),
                    )
                )


class PCBConstraintCompiler:
    _VERIFIER: ClassVar[dict[PCBConstraintType, PCBVerifier]] = {
        PCBConstraintType.DECOUPLING: PCBVerifier.DECOUPLING,
        PCBConstraintType.RETURN_PATH: PCBVerifier.RETURN_PATH,
        PCBConstraintType.SWITCHING_POWER: PCBVerifier.POWER_LOOP,
        PCBConstraintType.DIFFERENTIAL_PAIR: PCBVerifier.DIFFERENTIAL_PAIR,
        PCBConstraintType.FOOTPRINT_PIN_MAPPING: PCBVerifier.FOOTPRINT_PIN_MAPPING,
        PCBConstraintType.DATASHEET_LAYOUT: PCBVerifier.DATASHEET_COMPLIANCE,
        PCBConstraintType.MANUFACTURING: PCBVerifier.DFM,
        PCBConstraintType.TESTABILITY: PCBVerifier.DFT,
        PCBConstraintType.GENERAL_PLACEMENT: PCBVerifier.GENERAL_PLACEMENT,
        PCBConstraintType.GENERAL_ROUTING: PCBVerifier.GENERAL_ROUTING,
    }

    def compile(
        self,
        state: PCBEngineeringState,
        *,
        project_requirements: tuple[PCBFoundationEvidence, ...] = (),
        datasheet_evidence: tuple[PCBFoundationEvidence, ...] = (),
        interface_evidence: tuple[PCBFoundationEvidence, ...] = (),
        engineering_knowledge: tuple[PCBFoundationEvidence, ...] = (),
        power_stage_evidence: tuple[PCBPowerStageEvidence, ...] = (),
        power_loop_rules: tuple[PCBPowerLoopRule, ...] = (),
        differential_pair_rules: tuple[PCBDifferentialPairRule, ...] = (),
        differential_reference_requirement: str | None = None,
        differential_impedance_ohms: float | None = None,
    ) -> PCBConstraintSet:
        checked = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        extra = tuple(project_requirements) + tuple(datasheet_evidence) + tuple(interface_evidence) + tuple(engineering_knowledge)
        checked_power_stage_evidence = tuple(
            PCBPowerStageEvidence.model_validate(item.model_dump(mode="python"))
            for item in power_stage_evidence
        )
        checked_power_loop_rules = tuple(
            PCBPowerLoopRule.model_validate(item.model_dump(mode="python"))
            for item in power_loop_rules
        )
        checked_differential_pair_rules = tuple(
            PCBDifferentialPairRule.model_validate(item.model_dump(mode="python"))
            for item in differential_pair_rules
        )
        power_evidence = tuple(
            item
            for stage in checked_power_stage_evidence
            for item in (*stage.evidence_refs,)
        )
        rule_evidence = tuple(
            item
            for rule in checked_power_loop_rules
            for item in (*rule.evidence_refs,)
        )
        differential_rule_evidence = tuple(
            item
            for rule in checked_differential_pair_rules
            for item in rule.evidence_refs
        )
        all_evidence = tuple(dict.fromkeys((*checked.evidence_refs, *extra, *power_evidence, *rule_evidence, *differential_rule_evidence)))
        if any(item.project_id != checked.project_id for item in all_evidence):
            raise ValueError("constraint evidence project binding mismatch")
        has_design_truth = checked.design_truth_fingerprint is not None
        has_routes = bool(checked.routing)
        has_planes = bool(checked.planes)
        has_power = bool(checked.power_nets)
        has_differential = bool(checked.differential_pairs)
        has_conflict = bool(checked.conflicts)
        values: list[tuple[PCBConstraintType, PCBApplicability, tuple[str, ...], tuple[str, ...], PCBConstraintSource, tuple[PCBFoundationEvidence, ...]]] = [
            (PCBConstraintType.DECOUPLING, PCBApplicability.UNKNOWN if not has_design_truth else (PCBApplicability.REQUIRED if has_power else PCBApplicability.NOT_APPLICABLE), ("power_domain_decoupling_evidence", "pad_distance_rule_if_evidence_bound"), tuple(checked.power_nets), PCBConstraintSource.DESIGN_DECISION if has_design_truth else PCBConstraintSource.ARTIFACT, all_evidence),
            (PCBConstraintType.RETURN_PATH, PCBApplicability.REQUIRED if has_routes and has_planes else PCBApplicability.UNKNOWN if has_routes else PCBApplicability.NOT_APPLICABLE, ("continuous_reference_plane", "valid_layer_transition_return"), tuple(item.net_id for item in checked.nets if item.net_id), PCBConstraintSource.G5_RECEIPT, all_evidence),
            (
                PCBConstraintType.SWITCHING_POWER,
                PCBApplicability.REQUIRED
                if any(
                    item.topology in {
                        PCBPowerTopology.BUCK,
                        PCBPowerTopology.BOOST,
                        PCBPowerTopology.BUCK_BOOST,
                    }
                    for item in checked_power_stage_evidence
                )
                else PCBApplicability.UNKNOWN
                if checked_power_stage_evidence or checked.high_current_nets
                else PCBApplicability.NOT_APPLICABLE
                if has_design_truth
                else PCBApplicability.UNKNOWN,
                ("minimize_hot_loop", "cin_close_to_vin_gnd", "fb_keepout_from_sw"),
                tuple(checked.high_current_nets),
                PCBConstraintSource.DESIGN_DECISION if has_design_truth else PCBConstraintSource.ARTIFACT,
                all_evidence,
            ),
            (PCBConstraintType.DIFFERENTIAL_PAIR, PCBApplicability.REQUIRED if has_differential else PCBApplicability.NOT_APPLICABLE, ("positive_negative_net_pair", "interface_bound"), tuple(item.pair_id for item in checked.differential_pairs), PCBConstraintSource.INTERFACE_SPEC if interface_evidence else PCBConstraintSource.DESIGN_DECISION if has_design_truth else PCBConstraintSource.ARTIFACT, tuple(interface_evidence) or all_evidence),
            (PCBConstraintType.FOOTPRINT_PIN_MAPPING, PCBApplicability.UNKNOWN if has_conflict else PCBApplicability.REQUIRED if checked.footprints else PCBApplicability.UNKNOWN, ("pin_pad_mapping_is_evidence_bound",), tuple(item.footprint_id for item in checked.footprints), PCBConstraintSource.G5_RECEIPT, all_evidence),
            (PCBConstraintType.DATASHEET_LAYOUT, PCBApplicability.REQUIRED if datasheet_evidence else PCBApplicability.UNKNOWN, ("datasheet_recommended_layout",), tuple(item.component_id for item in checked.components), PCBConstraintSource.DATASHEET if datasheet_evidence else PCBConstraintSource.ARTIFACT, tuple(datasheet_evidence) or all_evidence),
            (PCBConstraintType.MANUFACTURING, PCBApplicability.REQUIRED if checked.source_artifact is not None else PCBApplicability.UNKNOWN, ("manufacturing_capability_and_clearance",), (checked.board.board_id,), PCBConstraintSource.G5_RECEIPT, all_evidence),
            (PCBConstraintType.TESTABILITY, PCBApplicability.UNKNOWN, ("test_access_and_observability",), tuple(item.component_id for item in checked.components), PCBConstraintSource.PROJECT_REQUIREMENT if project_requirements else PCBConstraintSource.ARTIFACT, tuple(project_requirements) or all_evidence),
            (PCBConstraintType.GENERAL_PLACEMENT, PCBApplicability.REQUIRED if checked.placement else PCBApplicability.UNKNOWN, ("placement_is_revision_bound",), tuple(item.component_id for item in checked.placement), PCBConstraintSource.G5_RECEIPT, all_evidence),
            (PCBConstraintType.GENERAL_ROUTING, PCBApplicability.REQUIRED if checked.routing else PCBApplicability.UNKNOWN, ("routing_is_revision_bound",), tuple(item.net_id for item in checked.routing), PCBConstraintSource.G5_RECEIPT, all_evidence),
        ]
        constraints: list[PCBConstraint] = []
        for constraint_type, applicability, requirements, targets, source, refs in values:
            checked_refs = tuple(sorted(set(refs), key=lambda item: item.evidence_id))
            constraint_id = _stable_id("constraint", checked.project_id, checked.revision, constraint_type.value)
            constraint_values = {
                "constraint_id": constraint_id,
                "constraint_type": constraint_type,
                "scope": "PCB",
                "target_ids": tuple(sorted(set(targets))),
                "requirements": requirements,
                "source": source,
                "evidence_refs": checked_refs,
                "confidence": 0.5 if applicability is PCBApplicability.UNKNOWN else 1.0,
                "applicability": applicability,
                "revision": checked.revision,
                "source_fingerprint": _bundle_fingerprint(checked_refs),
                "intent_source_binding": checked.intent_source_binding,
                "element_bindings": checked.element_bindings,
            }
            if constraint_type is PCBConstraintType.SWITCHING_POWER:
                bound_stage_evidence = tuple(
                    PCBPowerStageEvidence.create(
                        **{
                            **item.model_dump(mode="python", exclude={"fingerprint"}),
                            "constraint_ids": tuple(dict.fromkeys((*item.constraint_ids, constraint_id))),
                        }
                    )
                    for item in checked_power_stage_evidence
                )
                constraint_values.update(
                    power_stage_evidence=bound_stage_evidence,
                    power_loop_rules=checked_power_loop_rules,
                )
            if constraint_type is PCBConstraintType.DIFFERENTIAL_PAIR:
                pair_bindings = tuple(
                    PCBDifferentialPairBinding.create(
                        pair_id=item.pair_id,
                        interface_id=item.interface_id,
                        positive_net_id=item.positive_net,
                        negative_net_id=item.negative_net,
                        positive_signal_id=next(
                            (signal.signal_id for signal in checked.signal_classes if signal.net_id == item.positive_net),
                            None,
                        ),
                        negative_signal_id=next(
                            (signal.signal_id for signal in checked.signal_classes if signal.net_id == item.negative_net),
                            None,
                        ),
                        evidence_refs=item.evidence_refs,
                    )
                    for item in checked.differential_pairs
                )
                constraint_values.update(
                    differential_pair_bindings=pair_bindings,
                    differential_pair_rules=checked_differential_pair_rules,
                    differential_reference_requirement=differential_reference_requirement,
                    differential_impedance_ohms=differential_impedance_ohms,
                )
            constraints.append(PCBConstraint.create(**constraint_values))
        return PCBConstraintSet.create(
            project_id=checked.project_id,
            revision=checked.revision,
            state_fingerprint=checked.fingerprint,
            constraints=tuple(constraints),
            evidence_refs=tuple(sorted(set(all_evidence), key=lambda item: item.evidence_id)),
            intent_source_binding=checked.intent_source_binding,
            element_bindings=checked.element_bindings,
        )

    def build_plan(self, state: PCBEngineeringState, constraint_set: PCBConstraintSet) -> PCBVerificationPlan:
        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_set = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        if checked_set.project_id != checked_state.project_id or checked_set.revision != checked_state.revision or checked_set.state_fingerprint != checked_state.fingerprint:
            raise ValueError("PCB verification plan source binding mismatch")
        required: list[str] = []
        optional: list[str] = []
        not_applicable: list[str] = []
        unknown: list[str] = []
        for item in checked_set.constraints:
            verifier = self._VERIFIER[item.constraint_type].value
            if checked_state.status is PCBFoundationStatus.STALE or checked_set.status is PCBFoundationStatus.STALE:
                unknown.append(verifier)
            elif item.constraint_type in {
                PCBConstraintType.GENERAL_PLACEMENT,
                PCBConstraintType.GENERAL_ROUTING,
            }:
                # These generic placeholders have no independent machine
                # evaluator.  G5 DRC plus the typed B1-B5 verifiers own the
                # applicable placement and routing evidence, so retaining the
                # placeholders as REQUIRED would create a false missing-result
                # execution debt in Final Closure.
                not_applicable.append(verifier)
            elif item.applicability is PCBApplicability.REQUIRED:
                required.append(verifier)
            elif item.applicability is PCBApplicability.OPTIONAL:
                optional.append(verifier)
            elif item.applicability is PCBApplicability.NOT_APPLICABLE:
                not_applicable.append(verifier)
            else:
                unknown.append(verifier)
        status = PCBFoundationStatus.STALE if checked_state.status is PCBFoundationStatus.STALE or checked_set.status is PCBFoundationStatus.STALE else PCBFoundationStatus.CURRENT
        return PCBVerificationPlan.create(
            project_id=checked_state.project_id,
            revision=checked_state.revision,
            pcb_state_fingerprint=checked_state.fingerprint,
            constraint_set_fingerprint=checked_set.fingerprint,
            required_verifiers=tuple(sorted(set(required))),
            optional_verifiers=tuple(sorted(set(optional))),
            not_applicable_verifiers=tuple(sorted(set(not_applicable))),
            unknown_applicability=tuple(sorted(set(unknown))),
            evidence_refs=tuple(
                sorted(
                    set(checked_state.evidence_refs) | set(checked_set.evidence_refs),
                    key=lambda item: item.evidence_id,
                )
            ),
            status=status,
            stale_reason=None if status is PCBFoundationStatus.CURRENT else "PCB state or constraint set is stale",
            affected_objects=checked_state.affected_objects,
            required_reverification=checked_state.required_reverification,
        )


__all__ = (
    "Applicability",
    "PCBApplicability",
    "PCBArtifactReference",
    "PCBArtifactType",
    "PCBBoardProjection",
    "PCBComponentProjection",
    "PCBConflict",
    "PCBConnectivityStatus",
    "PCBConstraint",
    "PCBConstraintCompiler",
    "PCBConstraintEnforcementMode",
    "PCBConstraintSet",
    "PCBConstraintSource",
    "PCBConstraintType",
    "PCBElementBinding",
    "PCBEngineeringFactResolution",
    "PCBDecouplingRelation",
    "PCBDifferentialPairBinding",
    "PCBDifferentialPairProjection",
    "PCBDifferentialPairRule",
    "PCBDifferentialPairRuleMetric",
    "PCBEngineeringProjector",
    "PCBEngineeringState",
    "PCBFootprintProjection",
    "PCBFoundationEvidence",
    "PCBFoundationEvidenceSource",
    "PCBFoundationStatus",
    "PCBGeometryQuality",
    "PCBInterfaceProjection",
    "PCBIntentBindingStatus",
    "PCBIntentSourceBinding",
    "PCBKeepoutProjection",
    "PCBNetProjection",
    "PCBPadProjection",
    "PCBPlacementProjection",
    "PCBPlaneProjection",
    "PCBPolygonProjection",
    "PCBPowerLoopMetric",
    "PCBPowerLoopRule",
    "PCBPowerPinEvidence",
    "PCBPowerPinRole",
    "PCBPowerStageEvidence",
    "PCBPowerTopology",
    "PCBPowerFactRole",
    "PCBPowerFactSet",
    "PCBReadbackObjectKind",
    "PCBReadbackObjectReference",
    "PCBRequiredEngineeringFact",
    "PCBReferenceDiscontinuityType",
    "PCBRoutePathProjection",
    "PCBRouteSegmentProjection",
    "PCBSignalClass",
    "PCBSignalClassProjection",
    "PCBSourceBinding",
    "PCBSWDFactRole",
    "PCBSWDFactSet",
    "PCBStackupProjection",
    "PCBUnknown",
    "PCBVerificationPlan",
    "PCBVerifier",
    "PCBViaProjection",
)
