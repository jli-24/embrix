from datetime import UTC, datetime
import json
import sqlite3

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_validation_proposal import DeterministicValidationRules, ValidationProposalService
from embedded_copilot.engineering_validation_proposal.adapters.sqlite import SQLiteV64Store
from .test_service import _Clock, _Context, _Evidence, _evidence, _request


def test_sqlite_round_trip_and_restart(tmp_path) -> None:
    database = tmp_path / "v63-state.sqlite3"
    store = SQLiteV64Store(database)
    service = ValidationProposalService(
        evidence=_Evidence({("tenant-1", "project-1", "evidence-1"): _evidence()}),
        context=_Context(), rules=DeterministicValidationRules(), store=store, clock=_Clock(),
    )
    result = service.validate(_request())
    restored = SQLiteV64Store(database)
    assert restored.get_result("tenant-1", "project-1", result.validation_id) == result
    assert int(__import__("sqlite3").connect(database).execute("PRAGMA user_version").fetchone()[0]) == 2


def test_sqlite_round_trip_preserves_multi_finding_fingerprint_after_restart(tmp_path) -> None:
    database = tmp_path / "v64-multi-finding.sqlite3"
    store = SQLiteV64Store(database)
    service = ValidationProposalService(
        evidence=_Evidence({("tenant-1", "project-1", "evidence-1"): _evidence(facts=(
            ("firmware.sensor_address", "0x69"),
            ("hardware.sensor_address", "0x68"),
            ("requirement.sample_hz_min", "100"),
            ("firmware.sample_hz", "50"),
        ))}),
        context=_Context(), rules=DeterministicValidationRules(), store=store, clock=_Clock(),
    )

    result = service.validate(_request())
    restarted = SQLiteV64Store(database)
    restored = restarted.get_result("tenant-1", "project-1", result.validation_id)

    assert restored == result
    assert restored.validation_fingerprint == result.validation_fingerprint
    assert tuple(item.finding_id for item in restored.findings) == tuple(sorted(item.finding_id for item in restored.findings))


def test_sqlite_tampered_validation_result_is_rejected(tmp_path) -> None:
    database = tmp_path / "v64-tamper.sqlite3"
    store = SQLiteV64Store(database)
    result = ValidationProposalService(
        evidence=_Evidence({("tenant-1", "project-1", "evidence-1"): _evidence()}),
        context=_Context(), rules=DeterministicValidationRules(), store=store, clock=_Clock(),
    ).validate(_request())

    with sqlite3.connect(database) as connection:
        body = json.loads(connection.execute(
            "SELECT body FROM v64_validation_results WHERE tenant_id=? AND project_id=? AND validation_id=?",
            ("tenant-1", "project-1", result.validation_id),
        ).fetchone()[0])
        body["limitations"] = ["tampered semantic limitation"]
        connection.execute(
            "UPDATE v64_validation_results SET body=? WHERE tenant_id=? AND project_id=? AND validation_id=?",
            (json.dumps(body), "tenant-1", "project-1", result.validation_id),
        )

    with pytest.raises(ValidationError):
        SQLiteV64Store(database).get_result("tenant-1", "project-1", result.validation_id)


def test_tombstone_hides_v64_objects(tmp_path) -> None:
    store = SQLiteV64Store(tmp_path / "state.sqlite3")
    result = ValidationProposalService(
        evidence=_Evidence({("tenant-1", "project-1", "evidence-1"): _evidence()}),
        context=_Context(), rules=DeterministicValidationRules(), store=store, clock=_Clock(),
    ).validate(_request())
    with store._connect() as connection:
        connection.execute("INSERT INTO v63_tombstones(tenant_id, project_id, tombstoned_at) VALUES (?, ?, ?)", ("tenant-1", "project-1", datetime.now(UTC).isoformat()))
    assert store.get_result("tenant-1", "project-1", result.validation_id) is None
