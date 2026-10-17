"""Finite offline UM10204 Rev.7 original-reader verification."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from embedded_copilot.datasheet_runtime.parser.pdf_structure import (
    PdfPageStructure,
    PdfStructure,
    TableStructure,
)
from embedded_copilot.engineering_copilot import schematic_parameter_readers as readers
from embedded_copilot.engineering_copilot.schematic_evidence import ParameterLocator

DIGEST = "dc91f00f65584e06ef36e26c93bf9d91a95fb3c8a1830a9223e53caf678b36af"
REVISION = "Rev. 7.0 — 1 October 2021"
FACTS = (
    ("SCL clock frequency", "frequency_max_hz", "100000"),
    ("rise time of both SDA and SCL signals", "rise_time_max_s", "0.000001"),
    ("capacitive load for each bus line", "cbus_max_f", "0.0000000004"),
)


def _mapped(tmp_path: Path, content: bytes):
    (tmp_path / ".embrix").mkdir()
    (tmp_path / "um10204.pdf").write_bytes(content)
    entry = {
        "kind": "PDF",
        "document_uri": "test-original://um10204.pdf",
        "document_revision": REVISION,
        "relative_path": "um10204.pdf",
        "content_sha256": readers._sha(content),
        "source_record_id": "mapping-placeholder-only",
        "source_record_fingerprint": readers._sha(b"mapping-placeholder-only"),
        "manufacturer": "NXP",
        "mpn": "UM10204",
        "package": "I2C_BUS_SPECIFICATION",
    }
    mapping = {
        "schema_version": 1,
        "tenant_id": "tenant",
        "project_id": "project",
        "documents": [entry],
    }
    path = tmp_path / ".embrix" / "parameter-documents.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return readers.ProjectMappedParameterPort(lambda _tenant, _project: tmp_path), mapping, path


def _request(mapping, row, field, value):
    entry = mapping["documents"][0]
    return {
        "tenant_id": "tenant",
        "project_id": "project",
        "document_uri": entry["document_uri"],
        "document_revision": entry["document_revision"],
        "content_sha256": entry["content_sha256"],
        "locator": ParameterLocator(page=44, table="Table 11", row=row),
        "mode": "I2C_STANDARD",
        field: value,
    }


@pytest.fixture
def stub_original(monkeypatch):
    header = (
        "Symbol", "Parameter", "Conditions", "Standar d-mode", "",
        "Fast-mode", "", "Fast-mode Plus", "", "Unit",
    )
    units = ("", "", "", "Min", "Max", "Min", "Max", "Min", "Max", "")
    rows = (
        ("fSCL", "SCL clock frequency", "", "-", "100", "-", "400", "-", "1000", "kHz"),
        ("tr", "rise time of both SDA and SCL signals", "", "-", "1000", "20", "300", "-", "120", "ns"),
        ("Cb", "capacitive load for each bus line", "", "-", "400", "-", "400", "-", "550", "pF"),
    )
    pages = tuple(
        PdfPageStructure(
            page_number=i + 1,
            text=(
                "NXP Semiconductors UM10204\nRev. 7.0 — 1 October 2021\n"
                "Table 11. Standard, Fast, and Fast-mode Plus"
            ) if i == 43 else "",
            tables=(TableStructure(rows=(header, units, *rows)),) if i == 43 else (),
        )
        for i in range(44)
    )

    def structure(content, page):
        assert (content, page) == (b"stub-original", 44)
        return PdfStructure(page_count=44, pages=pages)

    monkeypatch.setattr(readers, "_pdf_structure", structure)


@pytest.mark.parametrize("row,field,value", FACTS)
def test_standard_fact_reads_only_its_exact_row(tmp_path, stub_original, row, field, value):
    port, mapping, _ = _mapped(tmp_path, b"stub-original")
    request = _request(mapping, row, field, value)
    fact = port.read_standard_fact(**request)
    assert fact.document_uri == request["document_uri"]
    assert fact.document_revision == REVISION
    assert fact.content_sha256 == request["content_sha256"]
    assert fact.locator == request["locator"]
    assert fact.mode == "I2C_STANDARD"
    assert {key for key in ("frequency_max_hz", "rise_time_max_s", "cbus_max_f")
            if getattr(fact, key) is not None} == {field}
    assert getattr(fact, field) == value


def test_standard_fact_rejects_wrong_identity_locator_value_and_multi_fact(tmp_path, stub_original):
    port, mapping, _ = _mapped(tmp_path, b"stub-original")
    request = _request(mapping, *FACTS[0])
    bad = [
        {"document_uri": "unknown://um10204"},
        {"document_revision": "Rev. 6"},
        {"content_sha256": readers._sha(b"wrong")},
        {"locator": ParameterLocator(page=44, table="Table 11", row="unknown")},
        {"locator": ParameterLocator(page=43, table="Table 11", row=FACTS[0][0])},
        {"frequency_max_hz": "999"},
        {"frequency_max_hz": "100000", "rise_time_max_s": FACTS[1][2]},
        {"mode": "UNKNOWN"},
    ]
    for update in bad:
        with pytest.raises(ValueError):
            port.read_standard_fact(**{**request, **update})
    mapping["documents"][0]["document_revision"] = "Rev. 6"
    (tmp_path / ".embrix" / "parameter-documents.json").write_text(
        json.dumps(mapping), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        port.read_standard_fact(**request)


def test_standard_fact_rejects_changed_map_and_original(tmp_path, stub_original, monkeypatch):
    port, mapping, path = _mapped(tmp_path, b"stub-original")
    request = _request(mapping, *FACTS[0])
    original = readers._pdf_fact

    def change_map(*args):
        result = original(*args)
        mapping["documents"][0]["relative_path"] = "moved.pdf"
        path.write_text(json.dumps(mapping), encoding="utf-8")
        return result

    monkeypatch.setattr(readers, "_pdf_fact", change_map)
    with pytest.raises(ValueError, match="DOCUMENT_CHANGED"):
        port.read_standard_fact(**request)

    monkeypatch.setattr(readers, "_pdf_fact", original)
    mapping["documents"][0]["relative_path"] = "um10204.pdf"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    (tmp_path / "um10204.pdf").write_bytes(b"changed-original")
    with pytest.raises(ValueError, match="CONTENT_MISMATCH"):
        port.read_standard_fact(**request)


@pytest.mark.parametrize("row,field,value", FACTS)
def test_acquired_um10204_original_if_available(tmp_path, row, field, value):
    roots = os.environ.get("EMBRIX_D2_REAL_ORIGINAL_ROOTS")
    if not roots:
        pytest.skip("NOT_RUN: acquired UM10204 original roots required")
    matches = [
        Path(root) / "um10204.pdf"
        for root in roots.split(os.pathsep)
        if (Path(root) / "um10204.pdf").is_file()
    ]
    assert len(matches) == 1, "Missing or ambiguous acquired UM10204 original"
    original = matches[0].read_bytes()
    assert hashlib.sha256(original).hexdigest() == DIGEST
    port, mapping, _ = _mapped(tmp_path, original)
    request = _request(mapping, row, field, value)
    assert getattr(port.read_standard_fact(**request), field) == value
    with pytest.raises(ValueError):
        port.read_standard_fact(**{**request, field: "999"})