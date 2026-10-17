from __future__ import annotations

import asyncio
import copy
from datetime import datetime

from pydantic import SecretStr

from ..audit import MultimodalApplicationAuditEvent
from ..contracts import identifier, utc_datetime
from ..exceptions import MultimodalAuditConflict, MultimodalAuditUnavailable
from ..models import (
    ComponentObservationContent,
    DebugObservationContent,
    DocumentObservationContent,
    EngineeringArtifact,
    EngineeringArtifactType,
    EngineeringObservation,
    ImageObservationContent,
    MultimodalModelReference,
    ObservationType,
    PCBObservationContent,
    ResolvedMediaReference,
    ResolvedPDFPage,
    SchematicObservationContent,
    VisualBoundingBox,
    VisualEvidenceLocatorType,
    VisualEvidenceReference,
    WaveformObservationContent,
    validate_artifact,
)


def _clone(model):
    return type(model).model_validate(copy.deepcopy(model.model_dump(mode="python")))


def _content(artifact_type: EngineeringArtifactType):
    return {
        EngineeringArtifactType.PDF: DocumentObservationContent(
            title="Reference manual",
            sections=("GPIO",),
            tables=("Pin matrix",),
            limitations=("Revision must be checked",),
        ),
        EngineeringArtifactType.IMAGE: ImageObservationContent(
            objects=("development board",),
            visible_text=("ESP32",),
            issues=(),
        ),
        EngineeringArtifactType.PCB_IMAGE: PCBObservationContent(
            components=("ESP32", "USB-UART", "LDO"),
            interfaces=("UART", "SPI"),
            visible_labels=("U1",),
            issues=("visual review required",),
        ),
        EngineeringArtifactType.SCHEMATIC_IMAGE: SchematicObservationContent(
            components=("MCU",),
            nets=("3V3",),
            interfaces=("UART",),
            issues=(),
        ),
        EngineeringArtifactType.DATASHEET: ComponentObservationContent(
            component_name="ESP32",
            voltages=("3.3 V",),
            interfaces=("UART", "SPI"),
            gpios=("GPIO12",),
            package="QFN",
            limitations=("Absolute maximum is not an operating condition",),
        ),
        EngineeringArtifactType.WAVEFORM: WaveformObservationContent(
            channels=("CH1",),
            measurements=("3.3 V peak",),
            anomalies=("possible ringing",),
        ),
        EngineeringArtifactType.DEBUG_SCREENSHOT: DebugObservationContent(
            error_codes=("E001",),
            messages=("connection timed out",),
            indicators=("red status icon",),
        ),
    }[artifact_type]


def _evidence(artifact: EngineeringArtifact) -> VisualEvidenceReference:
    common = {
        "project_id": artifact.project_id,
        "artifact_id": artifact.artifact_id,
        "artifact_fingerprint": artifact.fingerprint,
    }
    if artifact.artifact_type is EngineeringArtifactType.PDF:
        return VisualEvidenceReference(
            evidence_id="evidence-page-1",
            locator_type=VisualEvidenceLocatorType.PDF_PAGE,
            page_number=1,
            table_reference=None,
            region=None,
            label="GPIO section",
            **common,
        )
    if artifact.artifact_type is EngineeringArtifactType.DATASHEET:
        return VisualEvidenceReference(
            evidence_id="evidence-table-1",
            locator_type=VisualEvidenceLocatorType.DATASHEET_TABLE,
            page_number=12,
            table_reference="Table 3-1",
            region=None,
            label="Operating voltage table",
            **common,
        )
    return VisualEvidenceReference(
        evidence_id="evidence-region-1",
        locator_type=VisualEvidenceLocatorType.IMAGE_REGION,
        page_number=None,
        table_reference=None,
        region=VisualBoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.8),
        label="visible engineering region",
        **common,
    )


class FakeMultimodalProvider:
    def __init__(
        self,
        *,
        fixed_observation: EngineeringObservation | None = None,
        failure: Exception | None = None,
        cancel_async: bool = False,
    ) -> None:
        self._reference = MultimodalModelReference(
            provider="fake", model_name="fake-vision"
        )
        self._fixed = (
            _clone(fixed_observation) if fixed_observation is not None else None
        )
        self._failure = failure
        self._cancel_async = cancel_async
        self.call_count = 0

    @property
    def model_reference(self) -> MultimodalModelReference:
        return _clone(self._reference)

    def _result(self, artifact: EngineeringArtifact) -> EngineeringObservation:
        checked = validate_artifact(artifact)
        self.call_count += 1
        if self._failure is not None:
            raise self._failure
        if self._fixed is not None:
            return _clone(self._fixed)
        return EngineeringObservation.create(
            artifact_reference=checked,
            observation_type=ObservationType.for_artifact(checked.artifact_type),
            structured_content=_content(checked.artifact_type),
            confidence=0.8,
            evidence_refs=(_evidence(checked),),
            model_reference=self.model_reference,
        )

    def analyze(self, artifact: EngineeringArtifact) -> EngineeringObservation:
        return self._result(artifact)

    async def aanalyze(self, artifact: EngineeringArtifact) -> EngineeringObservation:
        if self._cancel_async:
            raise asyncio.CancelledError
        return self._result(artifact)


class FakeMultimodalMediaResolver:
    def resolve(self, artifact: EngineeringArtifact) -> ResolvedMediaReference:
        checked = validate_artifact(artifact)
        return ResolvedMediaReference(
            project_id=checked.project_id,
            artifact_id=checked.artifact_id,
            artifact_fingerprint=checked.fingerprint,
            location=SecretStr(f"https://media.invalid/{checked.artifact_id}"),
        )


class FakePDFPageExtractor:
    def __init__(self, *, page_count: int = 1) -> None:
        if type(page_count) is not int or not 1 <= page_count <= 32:
            raise ValueError("page_count is invalid")
        self._page_count = page_count

    def extract_pages(
        self, artifact: EngineeringArtifact
    ) -> tuple[ResolvedPDFPage, ...]:
        checked = validate_artifact(artifact)
        if checked.artifact_type not in {
            EngineeringArtifactType.PDF,
            EngineeringArtifactType.DATASHEET,
        }:
            raise ValueError("PDF page extraction requires a PDF artifact")
        return tuple(
            ResolvedPDFPage(
                project_id=checked.project_id,
                artifact_id=checked.artifact_id,
                artifact_fingerprint=checked.fingerprint,
                page_number=number,
                location=SecretStr(
                    f"https://media.invalid/{checked.artifact_id}/page-{number}.png"
                ),
            )
            for number in range(1, self._page_count + 1)
        )


class FakeMultimodalApplicationAuditSink:
    def __init__(self, *, fail_writes: bool = False) -> None:
        self._events: dict[str, MultimodalApplicationAuditEvent] = {}
        self._fail_writes = fail_writes

    @property
    def events(self) -> tuple[MultimodalApplicationAuditEvent, ...]:
        return tuple(_clone(item) for item in self._events.values())

    def append(
        self, event: MultimodalApplicationAuditEvent
    ) -> MultimodalApplicationAuditEvent:
        if self._fail_writes:
            raise MultimodalAuditUnavailable()
        checked = _clone(event)
        existing = self._events.get(checked.event_id)
        if existing is not None and existing != checked:
            raise MultimodalAuditConflict()
        self._events.setdefault(checked.event_id, checked)
        return _clone(self._events[checked.event_id])

    def get(self, event_id: str) -> MultimodalApplicationAuditEvent | None:
        checked_id = identifier(event_id, field="event_id")
        found = self._events.get(checked_id)
        return None if found is None else _clone(found)


class FixedMultimodalClock:
    def __init__(self, value: datetime) -> None:
        self._value = utc_datetime(value, field="clock_value")

    def now(self) -> datetime:
        return self._value


__all__ = (
    "FakeMultimodalApplicationAuditSink",
    "FakeMultimodalMediaResolver",
    "FakeMultimodalProvider",
    "FakePDFPageExtractor",
    "FixedMultimodalClock",
)
