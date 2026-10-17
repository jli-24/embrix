"""Real private-original fixtures for the finite read-only SHTC3 profile."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.engineering_copilot.schematic_library_readers import (
    read_shtc3_library_originals,
)

PRIVATE = Path(
    os.environ.get("EMBRIX_SHTC3_PRIVATE_LIBRARY_ROOT", "missing-private-library")
)
PDF = Path(os.environ.get("EMBRIX_SHTC3_PDF_ORIGINAL", "missing-shtc3.pdf"))
SYMBOL_ID = "SHTC3_EP:SHTC3_EP"
FOOTPRINT_ID = "SHTC3_EP:Sensirion_DFN-4-1EP_2x2mm_P1mm_EP0.7x1.6mm"
PDF_URI = (
    "https://sensirion.com/en/media/documents/643F9C8E/63A5A436/Datasheet_SHTC3.pdf"
)
PDF_SHA = "sha256:dbe979c975fe520663606b5c278d2b9130f4e402e6d9d943ca06c4e4f38e63d9"


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _source() -> ComponentSourceRecord:
    return ComponentSourceRecord.create(
        source_id="shtc3-source",
        source_uri="local:shtc3-source",
        source_revision="r1",
        manufacturer="Sensirion",
        mpn="SHTC3",
        category="SENSOR",
        package="DFN-4 2x2 mm",
        description="SHTC3 original",
        symbol=SYMBOL_ID,
        footprint=FOOTPRINT_ID,
        supported_roles=("SENSOR",),
    )


def _project(root: Path):
    if not PRIVATE.is_dir() or not PDF.is_file():
        pytest.skip("Original private candidate/PDF not available")
    source = _source()
    (root / ".embrix").mkdir(exist_ok=True)
    symbol = (PRIVATE / "SHTC3_EP.kicad_sym").read_bytes()
    footprint = (
        PRIVATE
        / "footprints"
        / "SHTC3_EP.pretty"
        / (FOOTPRINT_ID.split(":")[1] + ".kicad_mod")
    ).read_bytes()
    pdf = PDF.read_bytes()
    assert (_sha(symbol), _sha(footprint), _sha(pdf)) == (
        "sha256:decbb807bf2ae25a5757d3f64bf596dfc17ca1ae048f71d9dee0940ee6dd9b63",
        "sha256:e53f4556db4573c0d93aa546f606220c063cc075b17aaa8d29ab44e20ae228ef",
        PDF_SHA,
    )
    for path, content in [
        ("symbol.kicad_sym", symbol),
        ("footprint.kicad_mod", footprint),
        ("shtc3.pdf", pdf),
    ]:
        (root / path).write_bytes(content)
    libraries = [
        {
            "kind": kind,
            "library_id": identity,
            "uri": uri,
            "relative_path": path,
            "content_sha256": _sha(content),
            "source_record_id": source.source_id,
            "source_record_fingerprint": source.fingerprint,
        }
        for kind, identity, uri, path, content in [
            (
                "KICAD_SYMBOL_LIBRARY",
                SYMBOL_ID,
                "library:symbol",
                "symbol.kicad_sym",
                symbol,
            ),
            (
                "KICAD_FOOTPRINT",
                FOOTPRINT_ID,
                "library:footprint",
                "footprint.kicad_mod",
                footprint,
            ),
        ]
    ]
    documents = [
        {
            "kind": "PDF",
            "document_uri": PDF_URI,
            "document_revision": "Version 4 - December 2022",
            "relative_path": "shtc3.pdf",
            "content_sha256": PDF_SHA,
            "source_record_id": source.source_id,
            "source_record_fingerprint": source.fingerprint,
            "manufacturer": "Sensirion",
            "mpn": "SHTC3",
            "package": "DFN-4 2x2 mm",
        }
    ]
    library_map = {
        "schema_version": 1,
        "tenant_id": "tenant",
        "project_id": "project",
        "libraries": libraries,
    }
    parameter_map = {
        "schema_version": 1,
        "tenant_id": "tenant",
        "project_id": "project",
        "documents": documents,
    }
    (root / ".embrix" / "library-documents.json").write_text(
        json.dumps(library_map), encoding="utf-8"
    )
    (root / ".embrix" / "parameter-documents.json").write_text(
        json.dumps(parameter_map), encoding="utf-8"
    )
    kwargs = {
        "root": root,
        "tenant_id": "tenant",
        "project_id": "project",
        "source": source,
        "symbol_library_id": SYMBOL_ID,
        "symbol_uri": "library:symbol",
        "symbol_sha256": _sha(symbol),
        "footprint_library_id": FOOTPRINT_ID,
        "footprint_uri": "library:footprint",
        "footprint_sha256": _sha(footprint),
    }
    return kwargs, library_map, parameter_map


def test_real_originals_positive(tmp_path):
    kwargs, _, _ = _project(tmp_path)
    result = read_shtc3_library_originals(**kwargs)
    assert result.symbol_bytes == (tmp_path / "symbol.kicad_sym").read_bytes()
    assert result.footprint_bytes == (tmp_path / "footprint.kicad_mod").read_bytes()
    assert result.pdf_bytes == (tmp_path / "shtc3.pdf").read_bytes()
    assert result.pdf_sha256 == PDF_SHA
    assert [
        (x.number, x.name, x.locator.page, x.locator.table, x.locator.row)
        for x in result.manufacturer_terminals
    ] == [
        (str(n), name, 6, "Table 7", str(n))
        for n, name in [(1, "VDD"), (2, "SCL"), (3, "SDA"), (4, "VSS")]
    ]
    assert result.manufacturer_ep.locator.section == "4 Interface Specifications"
    assert (
        result.manufacturer_ep.locator.clause
        == "CENTER_PAD_INTERNAL_VSS_AND_OPTIONAL_PCB_GND"
    )
    assert result.library_symbol_pins == (
        ("1", "VDD"),
        ("2", "SCL"),
        ("3", "SDA"),
        ("4", "VSS"),
        ("5", "VSS"),
    )
    assert result.library_pad_ids == ("1", "2", "3", "4", "5")
    assert result.non_electrical_paste_pad_count == 5


@pytest.mark.parametrize(
    "field,value",
    [("tenant_id", "wrong"), ("project_id", "wrong"), ("schema_version", 2)],
)
def test_wrong_library_scope_or_version_fails(tmp_path, field, value):
    kwargs, mapping, _ = _project(tmp_path)
    mapping[field] = value
    (tmp_path / ".embrix" / "library-documents.json").write_text(
        json.dumps(mapping), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "PDF"),
        ("library_id", "Other:Part"),
        ("uri", "wrong"),
        ("content_sha256", "sha256:" + "0" * 64),
        ("source_record_id", "wrong"),
        ("source_record_fingerprint", "sha256:" + "0" * 64),
        ("relative_path", "../symbol.kicad_sym"),
    ],
)
def test_library_entry_mismatch_fails(tmp_path, field, value):
    kwargs, mapping, _ = _project(tmp_path)
    mapping["libraries"][0][field] = value
    (tmp_path / ".embrix" / "library-documents.json").write_text(
        json.dumps(mapping), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)


def test_duplicate_same_path_and_unknown_field_fail(tmp_path):
    kwargs, mapping, _ = _project(tmp_path)
    path = tmp_path / ".embrix" / "library-documents.json"
    for changed in (
        mapping["libraries"] + [mapping["libraries"][0]],
        [
            dict(mapping["libraries"][0], relative_path="footprint.kicad_mod"),
            mapping["libraries"][1],
        ],
        [dict(mapping["libraries"][0], extra=1), mapping["libraries"][1]],
    ):
        path.write_text(json.dumps({**mapping, "libraries": changed}), encoding="utf-8")
        with pytest.raises(ValueError):
            read_shtc3_library_originals(**kwargs)
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)


def test_pdf_identity_and_raw_drift_fail(tmp_path):
    kwargs, _, mapping = _project(tmp_path)
    path = tmp_path / ".embrix" / "parameter-documents.json"
    for field, value in [
        ("document_uri", "wrong"),
        ("content_sha256", "sha256:" + "0" * 64),
        ("source_record_id", "wrong"),
    ]:
        changed = json.loads(json.dumps(mapping))
        changed["documents"][0][field] = value
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError):
            read_shtc3_library_originals(**kwargs)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    (tmp_path / "shtc3.pdf").write_bytes(b"not the reviewed original")
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)


def test_absent_reparse_and_unknown_symbol_fail(tmp_path):
    kwargs, mapping, _ = _project(tmp_path)
    manifest = tmp_path / ".embrix" / "library-documents.json"
    manifest.unlink()
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)
    manifest.write_text(json.dumps(mapping), encoding="utf-8")
    symbol = tmp_path / "symbol.kicad_sym"
    symbol.write_bytes(b"(kicad_symbol_lib (version 20251024))")
    mapping["libraries"][0]["content_sha256"] = kwargs["symbol_sha256"] = _sha(
        symbol.read_bytes()
    )
    manifest.write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)
    symbol.unlink()
    try:
        symbol.symlink_to(tmp_path / "footprint.kicad_mod")
    except (OSError, NotImplementedError):
        pytest.skip("symlink unavailable")
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)


@pytest.mark.parametrize(
    "changed", ["library_map", "symbol", "footprint", "pdf_map", "pdf"]
)
def test_drift_during_pdf_parse_fails(tmp_path, monkeypatch, changed):
    kwargs, _, _ = _project(tmp_path)
    from embedded_copilot.engineering_copilot import (
        schematic_library_readers as readers,
    )

    original = readers._pdf_terminals
    names = {
        "library_map": ".embrix/library-documents.json",
        "symbol": "symbol.kicad_sym",
        "footprint": "footprint.kicad_mod",
        "pdf_map": ".embrix/parameter-documents.json",
        "pdf": "shtc3.pdf",
    }
    target = tmp_path / names[changed]

    def mutate(content):
        result = original(content)
        target.write_bytes(target.read_bytes() + b" ")
        return result

    monkeypatch.setattr(readers, "_pdf_terminals", mutate)
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)


def test_multiple_matching_pdf_and_library_fail(tmp_path):
    kwargs, library_map, parameter_map = _project(tmp_path)
    parameter_map["documents"].append(
        {**parameter_map["documents"][0], "document_uri": "local:second-shtc3-pdf"}
    )
    (tmp_path / ".embrix" / "parameter-documents.json").write_text(
        json.dumps(parameter_map), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)
    parameter_map["documents"].pop()
    (tmp_path / ".embrix" / "parameter-documents.json").write_text(
        json.dumps(parameter_map), encoding="utf-8"
    )
    library_map["libraries"].append(
        {**library_map["libraries"][0], "uri": "library:second-symbol"}
    )
    (tmp_path / ".embrix" / "library-documents.json").write_text(
        json.dumps(library_map), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        read_shtc3_library_originals(**kwargs)


@pytest.mark.parametrize(
    "scope",
    ["", True, "x" * 1025, "x" * 129, " tenant", "ten ant"],
    ids=[
        "empty",
        "boolean",
        "oversize",
        "catalog_limit",
        "leading_space",
        "inner_space",
    ],
)
def test_matching_invalid_scope_is_rejected(tmp_path, scope):
    kwargs, library_map, parameter_map = _project(tmp_path)
    for mapping in (library_map, parameter_map):
        mapping["tenant_id"] = scope
        mapping["project_id"] = scope
    kwargs["tenant_id"] = scope
    kwargs["project_id"] = scope
    (tmp_path / ".embrix" / "library-documents.json").write_text(
        json.dumps(library_map), encoding="utf-8"
    )
    (tmp_path / ".embrix" / "parameter-documents.json").write_text(
        json.dumps(parameter_map), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        read_shtc3_library_originals(**kwargs)


def test_explicit_junction_in_library_path_is_rejected():
    root_name = os.environ.get("EMBRIX_P3_REPARSE_TEST_ROOT")
    if not root_name:
        pytest.skip("NOT_RUN: explicit isolated junction root required")
    root = Path(root_name)
    assert root.is_absolute() and root.is_dir()
    assert (root / "junction").is_dir() and (root / "target").is_dir()
    kwargs, library_map, _ = _project(root)
    (root / "target" / "symbol.kicad_sym").write_bytes(
        (root / "symbol.kicad_sym").read_bytes()
    )
    library_map["libraries"][0]["relative_path"] = "junction/symbol.kicad_sym"
    (root / ".embrix" / "library-documents.json").write_text(
        json.dumps(library_map), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="MAPPING_PATH_INVALID"):
        read_shtc3_library_originals(**kwargs)
