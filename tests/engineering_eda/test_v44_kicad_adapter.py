from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_eda import (
    EDAArtifactReference,
    EDAArtifactType,
    EDAUnavailable,
    EDAValidationKind,
    EDAValidationReport,
    EDAValidationStatus,
    KiCad10ValidationAdapter,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


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
        content_fingerprint="sha256:" + "1" * 64,
        captured_at=NOW,
    )


class Backend:
    exact_version = "10.0.1"

    def __init__(self) -> None:
        self.argv = ()

    def run_validation(self, value, kind, argv):
        self.argv = argv
        return EDAValidationReport(
            report_id="report-1",
            project_id=value.project_id,
            artifact=value,
            validation_kind=kind,
            findings=(),
            status=EDAValidationStatus.PASSED,
            tool_version="kicad-10.0.1",
            validated_at=NOW,
        )


def test_kicad_cli_uses_fixed_drc_argv() -> None:
    backend = Backend()
    result = KiCad10ValidationAdapter(backend).validate(
        artifact(), EDAValidationKind.DRC
    )
    assert result.status is EDAValidationStatus.PASSED
    assert backend.argv == (
        "kicad-cli",
        "pcb",
        "drc",
        "--format",
        "json",
        "--exit-code-violations",
    )


def test_unknown_kicad_version_is_rejected() -> None:
    backend = Backend()
    backend.exact_version = "9.0.0"
    with pytest.raises(EDAUnavailable):
        KiCad10ValidationAdapter(backend)
