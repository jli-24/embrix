from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import (
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from embedded_copilot.schemas.result import ContractModel


PCBMetadataScalar: TypeAlias = str | int | float | bool | None
_ABSOLUTE_METADATA_PATH = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|/|file://)", re.IGNORECASE
)


class _FrozenPCBMetadata(Mapping[str, PCBMetadataScalar]):
    __slots__ = ("_items",)

    def __init__(self, items: Iterator[tuple[str, PCBMetadataScalar]]) -> None:
        self._items = tuple(items)

    def __getitem__(self, key: str) -> PCBMetadataScalar:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __deepcopy__(self, memo: dict[int, object]) -> "_FrozenPCBMetadata":
        return self


def _isolate_tuple(value: object) -> object:
    return tuple(copy.deepcopy(value)) if isinstance(value, (list, tuple)) else value


def _validate_structure_metadata(value: object) -> dict[str, PCBMetadataScalar]:
    copied = copy.deepcopy(value)
    if not isinstance(copied, Mapping):
        raise ValueError("PCB metadata must be a mapping")
    result: dict[str, PCBMetadataScalar] = {}
    for raw_key, raw_value in copied.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("PCB metadata keys must be non-empty strings")
        key = raw_key.strip()
        if "path" in re.findall(r"[a-z0-9]+", key.casefold()):
            raise ValueError("PCB metadata path keys are forbidden")
        if raw_value is not None and not isinstance(
            raw_value, (str, int, float, bool)
        ):
            raise ValueError("PCB metadata values must be scalar")
        normalized: PCBMetadataScalar = raw_value
        if isinstance(normalized, str):
            normalized = normalized.strip()
            if not normalized:
                raise ValueError("PCB metadata strings must not be blank")
            if _ABSOLUTE_METADATA_PATH.match(normalized):
                raise ValueError("PCB metadata absolute paths are forbidden")
        if isinstance(normalized, float) and not math.isfinite(normalized):
            raise ValueError("PCB metadata numbers must be finite")
        result[key] = normalized
    return dict(sorted(result.items()))


class _PCBStructureModel(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class PCBNetType(StrEnum):
    POWER = "power"
    GROUND = "ground"
    SIGNAL = "signal"
    CLOCK = "clock"
    UNKNOWN = "unknown"


class PCBStructureEvidence(_PCBStructureModel):
    rule_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    outcome: Literal["present", "missing", "connected", "floating"]
    evidence: tuple[str, ...]

    @field_validator("rule_id", "category", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("evidence", mode="before")
    @classmethod
    def isolate_evidence(cls, value: object) -> object:
        return _isolate_tuple(value)

    @model_validator(mode="after")
    def validate_evidence(self) -> "PCBStructureEvidence":
        if not self.evidence or any(not item.strip() for item in self.evidence):
            raise ValueError("PCB structure evidence must not be empty")
        return self


class PCBPosition(_PCBStructureModel):
    x_mm: float
    y_mm: float

    @field_validator("x_mm", "y_mm")
    @classmethod
    def validate_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("PCB coordinates must be finite")
        return value


class PCBPin(_PCBStructureModel):
    number: str = Field(min_length=1)
    pad_type: str = Field(min_length=1)
    net_name: str | None = Field(default=None, min_length=1)
    # Optional pad center in board coordinates.  Older parser callers may omit
    # it and therefore remain component-center/unknown quality at higher
    # projections.
    position: PCBPosition | None = None

    @field_validator("number", "pad_type", "net_name", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PCBComponent(_PCBStructureModel):
    reference: str = Field(min_length=1)
    # KiCad board files may omit a footprint's displayed value.  Preserve the
    # absence instead of converting it into an invented or invalid string.
    value: str | None = None
    footprint: str = Field(min_length=1)
    library: str | None = Field(default=None, min_length=1)
    position: PCBPosition
    rotation: float = 0.0
    layer: str = Field(min_length=1)
    pins: tuple[PCBPin, ...] = ()

    @field_validator(
        "reference", "value", "footprint", "library", "layer", mode="before"
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("rotation")
    @classmethod
    def validate_rotation(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("PCB rotation must be finite")
        return value

    @field_validator("position", "pins", mode="before")
    @classmethod
    def isolate_nested_state(cls, value: object) -> object:
        return _isolate_tuple(value) if isinstance(value, (list, tuple)) else copy.deepcopy(value)

    @model_validator(mode="after")
    def validate_unique_pins(self) -> "PCBComponent":
        numbers = [pin.number.casefold() for pin in self.pins]
        if len(numbers) != len(set(numbers)):
            raise ValueError("PCB component pin numbers must be unique")
        return self


class PCBNetNode(_PCBStructureModel):
    reference: str = Field(min_length=1)
    pin: str = Field(min_length=1)

    @field_validator("reference", "pin", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PCBNet(_PCBStructureModel):
    name: str = Field(min_length=1)
    net_type: PCBNetType
    nodes: tuple[PCBNetNode, ...] = ()

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("nodes", mode="before")
    @classmethod
    def isolate_nodes(cls, value: object) -> object:
        return _isolate_tuple(value)


class PCBLayer(_PCBStructureModel):
    name: str = Field(min_length=1)
    index: int = Field(ge=0)
    type: str = Field(min_length=1)

    @field_validator("name", "type", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PCBTrack(_PCBStructureModel):
    start: PCBPosition
    end: PCBPosition
    width_mm: float = Field(gt=0)
    layer: str = Field(min_length=1)
    net_name: str | None = Field(default=None, min_length=1)

    @field_validator("start", "end", mode="before")
    @classmethod
    def isolate_positions(cls, value: object) -> object:
        return copy.deepcopy(value)

    @field_validator("layer", "net_name", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class PCBVia(_PCBStructureModel):
    position: PCBPosition
    diameter_mm: float = Field(gt=0)
    drill_mm: float = Field(gt=0)
    layers: tuple[str, ...]
    net_name: str | None = Field(default=None, min_length=1)

    @field_validator("position", mode="before")
    @classmethod
    def isolate_position(cls, value: object) -> object:
        return copy.deepcopy(value)

    @field_validator("layers", mode="before")
    @classmethod
    def isolate_layers(cls, value: object) -> object:
        return _isolate_tuple(value)

    @field_validator("net_name", mode="before")
    @classmethod
    def strip_net_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_dimensions(self) -> "PCBVia":
        if self.drill_mm > self.diameter_mm:
            raise ValueError("PCB via drill must not exceed diameter")
        if not self.layers:
            raise ValueError("PCB via requires layers")
        return self


class PCBZone(_PCBStructureModel):
    name: str | None = Field(default=None, min_length=1)
    net_name: str | None = Field(default=None, min_length=1)
    layers: tuple[str, ...]
    polygon_points: tuple[tuple[float, float], ...] = ()

    @field_validator("name", "net_name", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("layers", mode="before")
    @classmethod
    def isolate_layers(cls, value: object) -> object:
        return _isolate_tuple(value)

    @field_validator("polygon_points", mode="before")
    @classmethod
    def isolate_polygon_points(cls, value: object) -> object:
        return _isolate_tuple(value)

    @model_validator(mode="after")
    def validate_layers(self) -> "PCBZone":
        if not self.layers:
            raise ValueError("PCB zone requires layers")
        return self


class UnifiedPCBModel(_PCBStructureModel):
    board_name: str = Field(min_length=1)
    source_format: Literal["kicad_pcb"]
    components: tuple[PCBComponent, ...] = ()
    nets: tuple[PCBNet, ...] = ()
    layers: tuple[PCBLayer, ...] = ()
    tracks: tuple[PCBTrack, ...] = ()
    vias: tuple[PCBVia, ...] = ()
    zones: tuple[PCBZone, ...] = ()
    metadata: Mapping[str, PCBMetadataScalar] = Field(
        default_factory=lambda: _FrozenPCBMetadata(iter(()))
    )

    @field_validator("board_name", mode="before")
    @classmethod
    def strip_board_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "components", "nets", "layers", "tracks", "vias", "zones", mode="before"
    )
    @classmethod
    def isolate_collections(cls, value: object) -> object:
        return _isolate_tuple(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> object:
        return _validate_structure_metadata(value)

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(
        cls, value: Mapping[str, PCBMetadataScalar]
    ) -> Mapping[str, PCBMetadataScalar]:
        return _FrozenPCBMetadata(iter(value.items()))

    @field_serializer("metadata")
    def serialize_metadata(
        self, value: Mapping[str, PCBMetadataScalar]
    ) -> dict[str, PCBMetadataScalar]:
        return dict(value)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> "UnifiedPCBModel":
        for values, label in (
            ([item.reference.casefold() for item in self.components], "components"),
            ([item.name.casefold() for item in self.nets], "nets"),
            ([item.name.casefold() for item in self.layers], "layers"),
            ([str(item.index) for item in self.layers], "layer indexes"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"PCB {label} must be unique")
        return self


def _strip_and_deduplicate(values: object) -> object:
    if not isinstance(values, list):
        return values
    result: list[object] = []
    seen: set[str] = set()
    for value in values:
        candidate = value.strip() if isinstance(value, str) else value
        if isinstance(candidate, str):
            if not candidate:
                raise ValueError("list values must not be empty")
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
        result.append(candidate)
    return result


def _deduplicate_issues(values: object) -> object:
    if not isinstance(values, list):
        return values
    result: list[object] = []
    seen: set[str] = set()
    for value in values:
        issue_id = value.id if isinstance(value, PCBIssue) else None
        if isinstance(value, dict):
            issue_id = value.get("id")
        if isinstance(issue_id, str):
            key = issue_id.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
        result.append(value)
    return result


class PCBRequirement(ContractModel):
    project_name: str = Field(min_length=1)
    platform: str | None = Field(default=None, min_length=1)
    components: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    @field_validator("project_name", "platform", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("components", "interfaces", "constraints", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        return _strip_and_deduplicate(value)


class PCBIssue(ContractModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    description: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator(
        "id",
        "category",
        "severity",
        "description",
        "recommendation",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, value: object) -> object:
        return _strip_and_deduplicate(value)


class PCBRuleEvaluation(ContractModel):
    issues: list[PCBIssue] = Field(default_factory=list)
    passed_rules: list[str] = Field(default_factory=list)

    @field_validator("issues", mode="before")
    @classmethod
    def normalize_issues(cls, value: object) -> object:
        return _deduplicate_issues(value)

    @field_validator("passed_rules", mode="before")
    @classmethod
    def normalize_passed_rules(cls, value: object) -> object:
        return _strip_and_deduplicate(value)


class PCBReviewReport(ContractModel):
    project_name: str = Field(min_length=1)
    platform: str | None = Field(default=None, min_length=1)
    issues: list[PCBIssue] = Field(default_factory=list)
    passed_rules: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("project_name", "platform", "summary", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("issues", mode="before")
    @classmethod
    def normalize_issues(cls, value: object) -> object:
        return _deduplicate_issues(value)

    @field_validator("passed_rules", "warnings", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        return _strip_and_deduplicate(value)


class PCBValidationResult(ContractModel):
    success: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("errors", "warnings", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        return _strip_and_deduplicate(value)

    @model_validator(mode="after")
    def validate_outcome(self) -> "PCBValidationResult":
        if self.success and self.errors:
            raise ValueError("successful PCB validation cannot contain errors")
        if not self.success and not self.errors:
            raise ValueError("failed PCB validation requires at least one error")
        return self
