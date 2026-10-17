from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.execution_worker import (
    KiCadCliSchematicParseAdapter,
    probe_kicad_cli_version,
)
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from tests.engineering_copilot.test_schematic_planning_g2 import _catalog
from tests.engineering_copilot.test_schematic_production_g3v import (
    _generate_unvalidated,
)
from tests.production_acceptance.kicad_toolchain import discover_test_kicad_cli


def _configured_kicad_cli() -> Path | None:
    return discover_test_kicad_cli()


def test_real_g3v_schematic_parse_persists_and_survives_restart(tmp_path: Path) -> None:
    executable = _configured_kicad_cli()
    if executable is None:
        pytest.skip("real KiCad CLI is not configured; set EMBRIX_KICAD_CLI")

    runtime, project, session, generated, artifact = _generate_unvalidated(tmp_path)
    receipt = generated.schematic_production.generation_receipt
    version = probe_kicad_cli_version(str(executable), cwd=artifact.parent)
    evidence = KiCadCliSchematicParseAdapter(
        executable=str(executable),
        kicad_version=version,
    ).execute(
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        operation_id=receipt.operation_id,
        artifact=artifact,
        expected_artifact_fingerprint=receipt.artifact_fingerprint,
    )

    assert evidence.validation_status == "PARSE_VERIFIED"
    assert evidence.exit_code == 0
    assert evidence.artifact_unchanged is True
    assert evidence.kicad_cli_path == str(executable.resolve())
    assert evidence.kicad_version == "10.0.5"
    assert evidence.command_identity == "kicad-cli sch export netlist"
    verified = runtime.record_schematic_parse_validation(evidence=evidence)
    verified_receipt = verified.schematic_production.generation_receipt
    assert verified_receipt.validation_status == "PARSE_VERIFIED"
    assert verified_receipt.parse_validation == evidence

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "desktop.sqlite3",
        clock=type(
            "Clock", (), {"now": lambda self: datetime(2026, 9, 17, 12, tzinfo=UTC)}
        )(),
        initialize_async=False,
        hardware_component_source=_catalog(),
    )
    restored = restarted.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    restored_receipt = restored.schematic_production.generation_receipt
    assert restored_receipt.validation_status == "PARSE_VERIFIED"
    assert restored_receipt.parse_validation == evidence
    assert restored_receipt.artifact_fingerprint == evidence.artifact_fingerprint
