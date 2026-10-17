from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.multimodal_engineering import (
    EngineeringArtifact,
    EngineeringArtifactType,
    EngineeringObservation,
    ImageObservationContent,
    ObservationType,
    PCBObservationContent,
    VisualBoundingBox,
    VisualEvidenceLocatorType,
    VisualEvidenceReference,
)

from .fixtures import (
    NOW,
    TYPE_DATA,
    artifact,
    content_for,
    evidence_for,
    model_reference,
)


@pytest.mark.parametrize("artifact_type", tuple(EngineeringArtifactType))
def test_all_artifact_types_are_strict_frozen_and_deterministic(artifact_type) -> None:
    item = artifact(artifact_type)
    clone = EngineeringArtifact.model_validate(item.model_dump(mode="python"))
    assert clone == item
    assert clone.fingerprint == item.fingerprint
    with pytest.raises(ValidationError):
        item.filename = "changed.png"  # type: ignore[misc]


def test_artifact_normalizes_nfc_and_utc_and_rejects_extra_or_path() -> None:
    _, media_type = TYPE_DATA[EngineeringArtifactType.IMAGE]
    item = EngineeringArtifact(
        project_id="project-1",
        artifact_id="image-1",
        artifact_type=EngineeringArtifactType.IMAGE,
        source_reference="artifact://image/1",
        filename="  cafe\u0301.png  ",
        media_type=media_type,
        byte_size=1,
        content_fingerprint="sha256:" + "1" * 64,
        captured_at=datetime.fromisoformat("2026-08-10T23:00:00+08:00"),
    )
    assert item.filename == "café.png"
    assert item.captured_at == NOW.replace(hour=15)
    material = item.model_dump(mode="python")
    material["extra"] = "forbidden"
    with pytest.raises(ValidationError):
        EngineeringArtifact.model_validate(material)
    material = item.model_dump(mode="python")
    material["filename"] = "C:\\private\\image.png"
    with pytest.raises(ValidationError):
        EngineeringArtifact.model_validate(material)


@pytest.mark.parametrize(
    ("locator", "page", "table", "region"),
    (
        (VisualEvidenceLocatorType.PDF_PAGE, 1, None, None),
        (VisualEvidenceLocatorType.DATASHEET_TABLE, 2, "Table 1", None),
        (
            VisualEvidenceLocatorType.IMAGE_REGION,
            None,
            None,
            VisualBoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0),
        ),
    ),
)
def test_visual_evidence_locator_contract(locator, page, table, region) -> None:
    item = artifact(
        EngineeringArtifactType.DATASHEET
        if locator is VisualEvidenceLocatorType.DATASHEET_TABLE
        else EngineeringArtifactType.PDF
        if locator is VisualEvidenceLocatorType.PDF_PAGE
        else EngineeringArtifactType.IMAGE
    )
    evidence = VisualEvidenceReference(
        evidence_id="evidence-1",
        project_id=item.project_id,
        artifact_id=item.artifact_id,
        artifact_fingerprint=item.fingerprint,
        locator_type=locator,
        page_number=page,
        table_reference=table,
        region=region,
        label="visible source",
    )
    assert evidence.fingerprint.startswith("sha256:")


def test_evidence_rejects_mixed_locator_and_invalid_box() -> None:
    item = artifact(EngineeringArtifactType.IMAGE)
    with pytest.raises(ValidationError):
        VisualBoundingBox(x_min=0.8, y_min=0.0, x_max=0.2, y_max=1.0)
    with pytest.raises(ValidationError):
        VisualEvidenceReference(
            evidence_id="evidence-1",
            project_id=item.project_id,
            artifact_id=item.artifact_id,
            artifact_fingerprint=item.fingerprint,
            locator_type=VisualEvidenceLocatorType.IMAGE_REGION,
            page_number=1,
            table_reference=None,
            region=VisualBoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0),
            label="invalid mixed locator",
        )


@pytest.mark.parametrize("artifact_type", tuple(EngineeringArtifactType))
def test_observation_typed_union_and_artifact_binding(artifact_type) -> None:
    item = artifact(artifact_type)
    result = EngineeringObservation.create(
        artifact_reference=item,
        observation_type=ObservationType.for_artifact(artifact_type),
        structured_content=content_for(artifact_type),
        confidence=0.8,
        evidence_refs=(evidence_for(item),),
        model_reference=model_reference(),
    )
    assert result.observation_id.startswith("observation-")
    assert result.evidence_refs[0].artifact_fingerprint == item.fingerprint
    assert (
        EngineeringObservation.model_validate(result.model_dump(mode="python"))
        == result
    )


def test_observation_rejects_missing_evidence_wrong_content_and_tamper() -> None:
    item = artifact(EngineeringArtifactType.PCB_IMAGE)
    with pytest.raises(ValidationError):
        EngineeringObservation.create(
            artifact_reference=item,
            observation_type=ObservationType.PCB,
            structured_content=PCBObservationContent(
                components=("ESP32",), interfaces=(), visible_labels=(), issues=()
            ),
            confidence=0.8,
            evidence_refs=(),
            model_reference=model_reference(),
        )
    with pytest.raises(ValidationError):
        EngineeringObservation.create(
            artifact_reference=item,
            observation_type=ObservationType.PCB,
            structured_content=ImageObservationContent(
                objects=("board",), visible_text=(), issues=()
            ),
            confidence=0.8,
            evidence_refs=(evidence_for(item),),
            model_reference=model_reference(),
        )
    valid = EngineeringObservation.create(
        artifact_reference=item,
        observation_type=ObservationType.PCB,
        structured_content=content_for(item.artifact_type),
        confidence=0.8,
        evidence_refs=(evidence_for(item),),
        model_reference=model_reference(),
    )
    material = valid.model_dump(mode="python")
    material["confidence"] = 0.7
    with pytest.raises(ValidationError):
        EngineeringObservation.model_validate(material)


def test_observation_rejects_incompatible_or_duplicate_locator() -> None:
    item = artifact(EngineeringArtifactType.IMAGE)
    page = VisualEvidenceReference(
        evidence_id="evidence-page-1",
        project_id=item.project_id,
        artifact_id=item.artifact_id,
        artifact_fingerprint=item.fingerprint,
        locator_type=VisualEvidenceLocatorType.PDF_PAGE,
        page_number=1,
        table_reference=None,
        region=None,
        label="wrong locator",
    )
    with pytest.raises(ValidationError):
        EngineeringObservation.create(
            artifact_reference=item,
            observation_type=ObservationType.IMAGE,
            structured_content=content_for(item.artifact_type),
            confidence=0.8,
            evidence_refs=(page,),
            model_reference=model_reference(),
        )

    first = evidence_for(item)
    duplicate = VisualEvidenceReference(
        **{
            **first.model_dump(mode="python", exclude={"fingerprint"}),
            "evidence_id": "evidence-region-duplicate",
        }
    )
    with pytest.raises(ValidationError):
        EngineeringObservation.create(
            artifact_reference=item,
            observation_type=ObservationType.IMAGE,
            structured_content=content_for(item.artifact_type),
            confidence=0.8,
            evidence_refs=(first, duplicate),
            model_reference=model_reference(),
        )


def test_public_contracts_reject_lists_and_unsafe_payload_text() -> None:
    with pytest.raises(ValidationError):
        PCBObservationContent(
            components=["ESP32"],  # type: ignore[arg-type]
            interfaces=(),
            visible_labels=(),
            issues=(),
        )
    with pytest.raises(ValidationError):
        ImageObservationContent(objects=("base64:AAAA",), visible_text=(), issues=())
