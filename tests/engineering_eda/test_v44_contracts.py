from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_eda import (
    EDAArtifactReference,
    EDAArtifactType,
    EDAChangeSet,
    EDASide,
    MoveFootprintChange,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)
FP = "sha256:" + "1" * 64


def artifact() -> EDAArtifactReference:
    return EDAArtifactReference(
        project_id="project-1",
        artifact_id="board-1",
        artifact_type=EDAArtifactType.BOARD,
        revision="r1",
        format_version="20240108",
        basename="main.kicad_pcb",
        byte_size=1024,
        source_reference="artifact://board-1",
        content_fingerprint=FP,
        captured_at=NOW,
    )


def test_artifact_is_strict_frozen_and_tamper_evident() -> None:
    value = artifact()
    with pytest.raises(ValidationError):
        value.revision = "r2"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        EDAArtifactReference.model_validate(
            {**value.model_dump(mode="python"), "fingerprint": "sha256:" + "2" * 64}
        )


def test_changeset_is_human_authored_and_board_only() -> None:
    change = MoveFootprintChange(
        reference="C12", x_mm=12.5, y_mm=7.25, rotation_deg=90.0, side=EDASide.FRONT
    )
    result = EDAChangeSet(
        change_set_id="change-1",
        project_id="project-1",
        proposal_fingerprint=FP,
        target_artifact=artifact(),
        before_fingerprint=FP,
        changes=(change,),
        authored_by="engineer-1",
        authored_at=NOW,
    )
    assert result.target_artifact.artifact_type is EDAArtifactType.BOARD
    assert result.changes == (change,)
