from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from embedded_copilot.component_recommendation import catalog

APPROVED_ID = "st-stm32f103c8t6-lqfp48-ds5319-r20"


def test_approved_resource_has_one_complete_real_st_pinout_without_project_supply() -> None:
    source = catalog.load_approved_component_source()
    assert tuple(item.source_id for item in source.records) == (APPROVED_ID,)
    record = source.records[0]
    assert record.mpn == "STM32F103C8T6"
    assert record.source_revision == "DS5319 Rev 20"
    assert tuple(int(pin.number) for pin in record.pin_facts) == tuple(range(1, 49))
    assert all(pin.pad_number == pin.number for pin in record.pin_facts)
    assert {pin.number for pin in record.pin_facts if pin.is_power} == {
        "1", "8", "9", "23", "24", "35", "36", "47", "48",
    }
    assert all(fact.nominal_voltage is None for fact in record.power_facts if fact.role == "consumer")


@pytest.mark.parametrize("mutation", [
    "missing", "bad-json", "unknown-version", "wrong-id", "duplicate-id",
    "bad-record-fingerprint", "file-tamper",
])
def test_approved_resource_fails_closed_on_missing_or_changed_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    original = catalog._approved_resource_path().read_bytes()
    target = tmp_path / "approved_components_v1.json"
    if mutation == "missing":
        pass
    elif mutation == "bad-json":
        target.write_bytes(b"{")
    elif mutation == "file-tamper":
        target.write_bytes(original + b" ")
    else:
        payload = json.loads(original)
        if mutation == "unknown-version":
            payload["schema_version"] = 2
        elif mutation == "wrong-id":
            payload["records"][0]["source_id"] = "unapproved"
        elif mutation == "duplicate-id":
            payload["records"].append(payload["records"][0])
        else:
            payload["records"][0]["fingerprint"] = "sha256:" + "0" * 64
        target.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(catalog, "_APPROVED_RESOURCE_SHA256", hashlib.sha256(target.read_bytes()).hexdigest())
    monkeypatch.setattr(catalog, "_approved_resource_path", lambda: target)
    with pytest.raises((OSError, TypeError, ValueError)):
        catalog.load_approved_component_source()
