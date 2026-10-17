from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from .contracts import (
    FingerprintedContract,
    MultimodalContract,
    canonical_fingerprint,
    deterministic_id,
    fingerprint,
    identifier,
    logical_reference,
    safe_text,
    tuple_text,
    utc_datetime,
)

_AUTO = "sha256:" + "0" * 64
_IMAGE_MIMES = frozenset({"image/png", "image/jpeg", "image/webp"})


class EngineeringArtifactType(StrEnum):
    PDF = "PDF"
    IMAGE = "IMAGE"
    PCB_IMAGE = "PCB_IMAGE"
    SCHEMATIC_IMAGE = "SCHEMATIC_IMAGE"
    DATASHEET = "DATASHEET"
    WAVEFORM = "WAVEFORM"
    DEBUG_SCREENSHOT = "DEBUG_SCREENSHOT"


class ObservationType(StrEnum):
    DOCUMENT = "DOCUMENT"
    IMAGE = "IMAGE"
    COMPONENT = "COMPONENT"
    PCB = "PCB"
    SCHEMATIC = "SCHEMATIC"
    WAVEFORM = "WAVEFORM"
    DEBUG = "DEBUG"

    @classmethod
    def for_artifact(cls, value: EngineeringArtifactType) -> ObservationType:
        return {
            EngineeringArtifactType.PDF: cls.DOCUMENT,
            EngineeringArtifactType.IMAGE: cls.IMAGE,
            EngineeringArtifactType.PCB_IMAGE: cls.PCB,
            EngineeringArtifactType.SCHEMATIC_IMAGE: cls.SCHEMATIC,
            EngineeringArtifactType.DATASHEET: cls.COMPONENT,
            EngineeringArtifactType.WAVEFORM: cls.WAVEFORM,
            EngineeringArtifactType.DEBUG_SCREENSHOT: cls.DEBUG,
        }[value]


class VisualEvidenceLocatorType(StrEnum):
    IMAGE_REGION = "IMAGE_REGION"
    PDF_PAGE = "PDF_PAGE"
    DATASHEET_TABLE = "DATASHEET_TABLE"


class EngineeringArtifact(FingerprintedContract):
    project_id: str
    artifact_id: str
    artifact_type: EngineeringArtifactType
    source_reference: str
    filename: str
    media_type: str
    byte_size: int = Field(gt=0, le=52_428_800)
    content_fingerprint: str
    captured_at: datetime

    @field_validator("project_id", "artifact_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return logical_reference(value, field="source_reference")

    @field_validator("filename", mode="before")
    @classmethod
    def _filename(cls, value: object) -> str:
        checked = safe_text(value, field="filename", maximum=160)
        if "/" in checked or "\\" in checked or ":" in checked:
            raise ValueError("filename must be a basename")
        return checked

    @field_validator("media_type", mode="before")
    @classmethod
    def _media_type(cls, value: object) -> str:
        return safe_text(value, field="media_type", maximum=64)

    @field_validator("content_fingerprint", mode="before")
    @classmethod
    def _content_fp(cls, value: object) -> str:
        return fingerprint(value, field="content_fingerprint")

    @field_validator("captured_at", mode="before")
    @classmethod
    def _captured_at(cls, value: object) -> datetime:
        return utc_datetime(value, field="captured_at")

    @model_validator(mode="after")
    def _type_binding(self):
        if self.artifact_type in {
            EngineeringArtifactType.PDF,
            EngineeringArtifactType.DATASHEET,
        }:
            if (
                self.media_type != "application/pdf"
                or not self.filename.casefold().endswith(".pdf")
            ):
                raise ValueError("PDF artifact binding mismatch")
        elif self.media_type not in _IMAGE_MIMES:
            raise ValueError("image artifact binding mismatch")
        return self


class VisualBoundingBox(FingerprintedContract):
    x_min: float = Field(ge=0.0, le=1.0)
    y_min: float = Field(ge=0.0, le=1.0)
    x_max: float = Field(ge=0.0, le=1.0)
    y_max: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self):
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("visual bounding box is invalid")
        return self


class VisualEvidenceReference(FingerprintedContract):
    evidence_id: str
    project_id: str
    artifact_id: str
    artifact_fingerprint: str
    locator_type: VisualEvidenceLocatorType
    page_number: int | None = Field(default=None, ge=1, le=100_000)
    table_reference: str | None = None
    region: VisualBoundingBox | None = None
    label: str

    @field_validator("evidence_id", "project_id", "artifact_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("artifact_fingerprint", mode="before")
    @classmethod
    def _artifact_fp(cls, value: object) -> str:
        return fingerprint(value, field="artifact_fingerprint")

    @field_validator("table_reference", mode="before")
    @classmethod
    def _table(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="table_reference")

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: object) -> str:
        return safe_text(value, field="label", maximum=256)

    @model_validator(mode="after")
    def _locator_binding(self):
        expected = {
            VisualEvidenceLocatorType.IMAGE_REGION: (
                self.page_number is None
                and self.table_reference is None
                and self.region is not None
            ),
            VisualEvidenceLocatorType.PDF_PAGE: (
                self.page_number is not None
                and self.table_reference is None
                and self.region is None
            ),
            VisualEvidenceLocatorType.DATASHEET_TABLE: (
                self.page_number is not None
                and self.table_reference is not None
                and self.region is None
            ),
        }[self.locator_type]
        if not expected:
            raise ValueError("visual evidence locator mismatch")
        return self


class MultimodalModelReference(FingerprintedContract):
    provider: str
    model_name: str

    @field_validator("provider", "model_name", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)


class _TupleContent(FingerprintedContract):
    @staticmethod
    def _items(value: object, field: str) -> tuple[str, ...]:
        return tuple_text(value, field=field)


class DocumentObservationContent(_TupleContent):
    content_type: Literal["DOCUMENT"] = "DOCUMENT"
    title: str
    sections: tuple[str, ...]
    tables: tuple[str, ...]
    limitations: tuple[str, ...]

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: object) -> str:
        return safe_text(value, field="title")

    @field_validator("sections", "tables", "limitations", mode="before")
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        return cls._items(value, info.field_name)


class ImageObservationContent(_TupleContent):
    content_type: Literal["IMAGE"] = "IMAGE"
    objects: tuple[str, ...]
    visible_text: tuple[str, ...]
    issues: tuple[str, ...]

    @field_validator("objects", "visible_text", "issues", mode="before")
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        return cls._items(value, info.field_name)


class ComponentObservationContent(_TupleContent):
    content_type: Literal["COMPONENT"] = "COMPONENT"
    component_name: str
    voltages: tuple[str, ...]
    interfaces: tuple[str, ...]
    gpios: tuple[str, ...]
    package: str
    limitations: tuple[str, ...]

    @field_validator("component_name", "package", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name)

    @field_validator("voltages", "interfaces", "gpios", "limitations", mode="before")
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        return cls._items(value, info.field_name)


class PCBObservationContent(_TupleContent):
    content_type: Literal["PCB"] = "PCB"
    components: tuple[str, ...]
    interfaces: tuple[str, ...]
    visible_labels: tuple[str, ...]
    issues: tuple[str, ...]

    @field_validator(
        "components", "interfaces", "visible_labels", "issues", mode="before"
    )
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        return cls._items(value, info.field_name)


class SchematicObservationContent(_TupleContent):
    content_type: Literal["SCHEMATIC"] = "SCHEMATIC"
    components: tuple[str, ...]
    nets: tuple[str, ...]
    interfaces: tuple[str, ...]
    issues: tuple[str, ...]

    @field_validator("components", "nets", "interfaces", "issues", mode="before")
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        return cls._items(value, info.field_name)


class WaveformObservationContent(_TupleContent):
    content_type: Literal["WAVEFORM"] = "WAVEFORM"
    channels: tuple[str, ...]
    measurements: tuple[str, ...]
    anomalies: tuple[str, ...]

    @field_validator("channels", "measurements", "anomalies", mode="before")
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        return cls._items(value, info.field_name)


class DebugObservationContent(_TupleContent):
    content_type: Literal["DEBUG"] = "DEBUG"
    error_codes: tuple[str, ...]
    messages: tuple[str, ...]
    indicators: tuple[str, ...]

    @field_validator("error_codes", "messages", "indicators", mode="before")
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        return cls._items(value, info.field_name)


StructuredObservationContent = Annotated[
    DocumentObservationContent
    | ImageObservationContent
    | ComponentObservationContent
    | PCBObservationContent
    | SchematicObservationContent
    | WaveformObservationContent
    | DebugObservationContent,
    Field(discriminator="content_type"),
]


class EngineeringObservation(MultimodalContract):
    observation_id: str = ""
    artifact_reference: EngineeringArtifact
    observation_type: ObservationType
    structured_content: StructuredObservationContent
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[VisualEvidenceReference, ...]
    model_reference: MultimodalModelReference
    fingerprint: str = _AUTO

    @field_validator("observation_id", mode="before")
    @classmethod
    def _observation_id(cls, value: object) -> str:
        return "" if value == "" else identifier(value, field="observation_id")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_tuple(cls, value: object) -> object:
        if type(value) is not tuple:
            raise ValueError("evidence_refs must be a tuple")
        return value

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _binding(self):
        expected_type = ObservationType.for_artifact(
            self.artifact_reference.artifact_type
        )
        if self.observation_type is not expected_type:
            raise ValueError("observation type mismatch")
        if self.structured_content.content_type != self.observation_type.value:
            raise ValueError("structured content type mismatch")
        if not self.evidence_refs:
            raise ValueError("observation evidence is required")
        ids = tuple(item.evidence_id for item in self.evidence_refs)
        if len(ids) != len(set(ids)):
            raise ValueError("observation evidence must be unique")
        locator_keys = tuple(
            (
                item.locator_type,
                item.page_number,
                item.table_reference,
                item.region.fingerprint if item.region is not None else None,
                item.label,
            )
            for item in self.evidence_refs
        )
        if len(locator_keys) != len(set(locator_keys)):
            raise ValueError("observation evidence locator must be unique")
        if any(
            item.project_id != self.artifact_reference.project_id
            or item.artifact_id != self.artifact_reference.artifact_id
            or item.artifact_fingerprint != self.artifact_reference.fingerprint
            for item in self.evidence_refs
        ):
            raise ValueError("observation evidence binding mismatch")
        artifact_type = self.artifact_reference.artifact_type
        allowed_locators = (
            {
                VisualEvidenceLocatorType.PDF_PAGE,
                VisualEvidenceLocatorType.DATASHEET_TABLE,
            }
            if artifact_type is EngineeringArtifactType.DATASHEET
            else {VisualEvidenceLocatorType.PDF_PAGE}
            if artifact_type is EngineeringArtifactType.PDF
            else {VisualEvidenceLocatorType.IMAGE_REGION}
        )
        if any(
            item.locator_type not in allowed_locators for item in self.evidence_refs
        ):
            raise ValueError("observation evidence locator is incompatible")
        material = self.model_dump(
            mode="json", exclude={"observation_id", "fingerprint"}
        )
        derived = deterministic_id(
            "observation",
            json.dumps(
                material, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ),
        )
        if not self.observation_id:
            object.__setattr__(self, "observation_id", derived)
        elif self.observation_id != derived:
            raise ValueError("observation id mismatch")
        expected_fp = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected_fp)
        elif self.fingerprint != expected_fp:
            raise ValueError("observation fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringObservation:
        return cls(**values)


class ResolvedMediaReference(MultimodalContract):
    project_id: str
    artifact_id: str
    artifact_fingerprint: str
    location: SecretStr

    @field_validator("project_id", "artifact_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("artifact_fingerprint", mode="before")
    @classmethod
    def _artifact_fp(cls, value: object) -> str:
        return fingerprint(value, field="artifact_fingerprint")

    @model_validator(mode="after")
    def _https(self):
        location = self.location.get_secret_value()
        if not location.startswith("https://") or "\n" in location or "\r" in location:
            raise ValueError("resolved media location is invalid")
        return self


class ResolvedPDFPage(MultimodalContract):
    project_id: str
    artifact_id: str
    artifact_fingerprint: str
    page_number: int = Field(ge=1, le=100_000)
    location: SecretStr

    @field_validator("project_id", "artifact_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("artifact_fingerprint", mode="before")
    @classmethod
    def _artifact_fp(cls, value: object) -> str:
        return fingerprint(value, field="artifact_fingerprint")

    @model_validator(mode="after")
    def _https(self):
        if not self.location.get_secret_value().startswith("https://"):
            raise ValueError("resolved PDF page location is invalid")
        return self


def validate_artifact(value: object) -> EngineeringArtifact:
    if type(value) is not EngineeringArtifact:
        raise TypeError("engineering artifact is invalid")
    return EngineeringArtifact.model_validate(value.model_dump(mode="python"))


def validate_observation(value: object) -> EngineeringObservation:
    if type(value) is not EngineeringObservation:
        raise TypeError("engineering observation is invalid")
    return EngineeringObservation.model_validate(value.model_dump(mode="python"))


__all__ = tuple(
    name
    for name in globals()
    if name.endswith("Content")
    or name.startswith(
        ("Engineering", "MultimodalModel", "Observation", "Resolved", "Visual")
    )
    or name
    in {"StructuredObservationContent", "validate_artifact", "validate_observation"}
)
