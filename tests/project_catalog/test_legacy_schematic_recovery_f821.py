from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
from tests.engineering_copilot.test_schematic_production_g3 import (
    _runtime_with_approved_plan,
)


def test_legacy_schematic_conversation_recovers_when_production_state_is_absent(
    tmp_path: Path,
) -> None:
    runtime, _project, session, detail = _runtime_with_approved_plan(tmp_path)
    try:
        assert detail.schematic_production is not None
        assert detail.schematic_production.status.value == "AWAITING_APPROVAL"
        assert detail.design_truth is not None
        assert detail.hardware_design_spec is not None
        assert detail.implementation_spec is not None
        assert detail.execution_authorization is not None
        assert detail.execution_intents

        legacy = detail.model_dump(mode="json")
        legacy.pop("schematic_production")
        legacy["fingerprint"] = ZERO
        database = tmp_path / "desktop.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE conversations SET body = ? WHERE conversation_id = ?",
                (json.dumps(legacy, ensure_ascii=False), session.conversation.conversation_id),
            )

        restored = SQLiteProjectCatalogRepository(database).conversation(
            session.conversation.conversation_id
        )
        assert restored is not None
        assert restored.schematic_production is not None
        assert restored.schematic_production.status.value == "AWAITING_APPROVAL"
    finally:
        runtime.close()
