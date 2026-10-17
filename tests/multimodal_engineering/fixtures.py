from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.multimodal_engineering import (
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
    SchematicObservationContent,
    VisualBoundingBox,
    VisualEvidenceLocatorType,
    VisualEvidenceReference,
    WaveformObservationContent,
)

NOW = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)

TYPE_DATA = {
    EngineeringArtifactType.PDF: ("manual.pdf", "application/pdf"),
    EngineeringArtifactType.IMAGE: ("photo.png", "image/png"),
    EngineeringArtifactType.PCB_IMAGE: ("board.png", "image/png"),
    EngineeringArtifactType.SCHEMATIC_IMAGE: ("schematic.png", "image/png"),
    EngineeringArtifactType.DATASHEET: ("datasheet.pdf", "application/pdf"),
    EngineeringArtifactType.WAVEFORM: ("waveform.png", "image/png"),
    EngineeringArtifactType.DEBUG_SCREENSHOT: ("debug.png", "image/png"),
}


def artifact(
    artifact_type: EngineeringArtifactType = EngineeringArtifactType.PCB_IMAGE,
) -> EngineeringArtifact:
    filename, media_type = TYPE_DATA[artifact_type]
    return EngineeringArtifact(
        project_id="project-1",
        artifact_id=f"artifact-{artifact_type.value.casefold()}",
        artifact_type=artifact_type,
        source_reference=f"artifact://multimodal/{artifact_type.value.casefold()}",
        filename=filename,
        media_type=media_type,
        byte_size=1024,
        content_fingerprint="sha256:" + "a" * 64,
        captured_at=NOW,
    )


def model_reference() -> MultimodalModelReference:
    return MultimodalModelReference(provider="fake", model_name="fake-vision")


def content_for(artifact_type: EngineeringArtifactType):
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


def evidence_for(item: EngineeringArtifact) -> VisualEvidenceReference:
    if item.artifact_type is EngineeringArtifactType.PDF:
        return VisualEvidenceReference(
            evidence_id="evidence-page-1",
            project_id=item.project_id,
            artifact_id=item.artifact_id,
            artifact_fingerprint=item.fingerprint,
            locator_type=VisualEvidenceLocatorType.PDF_PAGE,
            page_number=1,
            table_reference=None,
            region=None,
            label="GPIO section",
        )
    if item.artifact_type is EngineeringArtifactType.DATASHEET:
        return VisualEvidenceReference(
            evidence_id="evidence-table-1",
            project_id=item.project_id,
            artifact_id=item.artifact_id,
            artifact_fingerprint=item.fingerprint,
            locator_type=VisualEvidenceLocatorType.DATASHEET_TABLE,
            page_number=12,
            table_reference="Table 3-1",
            region=None,
            label="Operating voltage table",
        )
    return VisualEvidenceReference(
        evidence_id="evidence-region-1",
        project_id=item.project_id,
        artifact_id=item.artifact_id,
        artifact_fingerprint=item.fingerprint,
        locator_type=VisualEvidenceLocatorType.IMAGE_REGION,
        page_number=None,
        table_reference=None,
        region=VisualBoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.8),
        label="visible engineering region",
    )


def observation(
    artifact_type: EngineeringArtifactType = EngineeringArtifactType.PCB_IMAGE,
) -> EngineeringObservation:
    item = artifact(artifact_type)
    return EngineeringObservation.create(
        artifact_reference=item,
        observation_type=ObservationType.for_artifact(artifact_type),
        structured_content=content_for(artifact_type),
        confidence=0.8,
        evidence_refs=(evidence_for(item),),
        model_reference=model_reference(),
    )


def question() -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id="request-vision-1",
        trace_id="trace-vision-1",
        project_id="project-1",
        requester="engineer-1",
        question="What components and interfaces are visible?",
        constraints=(),
        requested_at=NOW,
    )


def base_context() -> EngineeringContextSnapshot:
    item = EngineeringContextEvidence.create(
        evidence_id="knowledge-1",
        project_id="project-1",
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="board",
        summary="Board uses an ESP32 controller",
        fact_key=None,
        fact_value=None,
        source_reference="knowledge:board-1",
        source_fingerprint="sha256:" + "b" * 64,
        confidence=0.9,
        verification_status="VERIFIED",
    )
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description=question().question,
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(item,),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.9,
        provenance=(item.source(),),
        verification_status="VERIFIED",
    )
