"""Synthetic local-original fixtures; these are not manufacturer document evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.component_recommendation.contracts import (
    ComponentElectricalFact,
    ComponentSourceRecord,
)
from embedded_copilot.datasheet_runtime.parser.pdf_structure import (
    PdfPageStructure,
    PdfStructure,
)
from embedded_copilot.datasheet_runtime.parser.table import TableStructure
from embedded_copilot.engineering_copilot import schematic_parameter_readers as readers
from embedded_copilot.engineering_copilot.schematic_evidence import (
    AUTO,
    LocalParameterSnapshotPort,
    ParameterCandidate,
    ParameterConditions,
    ParameterLocator,
)
from embedded_copilot.engineering_copilot.schematic_parameter_readers import (
    ProjectMappedParameterPort,
)


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _source() -> ComponentSourceRecord:
    return ComponentSourceRecord.create(
        source_id="synthetic-source",
        source_uri="local:synthetic-source",
        source_revision="r1",
        manufacturer="Fixture",
        mpn="Fixture-1",
        category="SENSOR",
        package="PKG-1",
        description="Synthetic test record",
        symbol="Fixture:Symbol",
        footprint="Fixture:Footprint",
        supported_roles=("SENSOR",),
        electrical_facts=(
            ComponentElectricalFact.create(
                parameter="supply_voltage", minimum=1.62, maximum=3.6, unit="V"
            ),
        ),
    )


def _project(
    root: Path, source: ComponentSourceRecord
) -> tuple[ProjectMappedParameterPort, ParameterCandidate]:
    original = source.model_dump_json().encode("utf-8")
    (root / ".embrix").mkdir()
    (root / "source.json").write_bytes(original)
    mapping = {
        "schema_version": 1,
        "tenant_id": "tenant",
        "project_id": "project",
        "documents": [
            {
                "kind": "COMPONENT_SOURCE_JSON",
                "document_uri": source.source_uri,
                "document_revision": source.source_revision,
                "relative_path": "source.json",
                "content_sha256": _sha(original),
                "source_record_id": source.source_id,
                "source_record_fingerprint": source.fingerprint,
                "manufacturer": source.manufacturer,
                "mpn": source.mpn,
                "package": source.package,
            }
        ],
    }
    (root / ".embrix" / "parameter-documents.json").write_text(
        json.dumps(mapping), encoding="utf-8"
    )
    candidate = ParameterCandidate(
        evidence_id="evidence",
        tenant_id="tenant",
        project_id="project",
        conversation_id="conversation",
        source_record_id=source.source_id,
        source_record_fingerprint=source.fingerprint,
        manufacturer=source.manufacturer,
        mpn=source.mpn,
        package=source.package,
        document_uri=source.source_uri,
        document_revision=source.source_revision,
        content_sha256=_sha(original),
        locator=ParameterLocator(
            page=1, table="electrical_facts", row="supply_voltage"
        ),
        parameter_key="SUPPLY_MIN",
        bound_kind="MIN",
        value_decimal="1.62",
        unit="V",
        conditions=ParameterConditions(package=source.package),
    )
    return ProjectMappedParameterPort(lambda tenant, project: root), candidate


def _changed(candidate: ParameterCandidate, **changes: object) -> ParameterCandidate:
    data = candidate.model_dump(mode="python")
    data.update(changes)
    data["fingerprint"] = AUTO
    return ParameterCandidate.model_validate(data)


def _change_map(root: Path, update) -> None:
    path = root / ".embrix" / "parameter-documents.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    update(mapping)
    path.write_text(json.dumps(mapping), encoding="utf-8")


def test_synthetic_json_parameter_and_independent_full_source(
    tmp_path: Path, monkeypatch
) -> None:
    source = _source()
    port, minimum = _project(tmp_path, source)
    calls = []
    original_reader = LocalParameterSnapshotPort.read_component_source

    def tracked_reader(self, record, candidates):
        calls.append(record.source_id)
        return original_reader(self, record, candidates)

    monkeypatch.setattr(
        LocalParameterSnapshotPort, "read_component_source", tracked_reader
    )
    maximum = _changed(
        minimum,
        evidence_id="evidence-max",
        parameter_key="SUPPLY_MAX",
        bound_kind="MAX",
        value_decimal="3.6",
    )
    assert port.read_parameter(minimum) == minimum
    assert port.read_parameter(maximum) == maximum
    assert port.read_component_source(source, (minimum, maximum)) == source
    assert calls == [source.source_id]


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"value_decimal": "2"}, "LOCATOR_UNRESOLVED"),
        (
            {"conditions": ParameterConditions(mode="DC", package="PKG-1")},
            "LOCATOR_UNRESOLVED",
        ),
        ({"document_revision": "r2"}, "DOCUMENT_UNAVAILABLE"),
        ({"project_id": "other"}, "MAPPING_INVALID"),
        ({"content_sha256": "sha256:" + "0" * 64}, "SOURCE_MISMATCH"),
        ({"source_record_id": "other"}, "SOURCE_MISMATCH"),
        ({"mpn": "Other"}, "SOURCE_MISMATCH"),
        (
            {"package": "Other", "conditions": ParameterConditions(package="Other")},
            "SOURCE_MISMATCH",
        ),
        (
            {
                "locator": ParameterLocator(
                    page=1, table="electrical_facts", row="other"
                )
            },
            "LOCATOR_UNRESOLVED",
        ),
        (
            {
                "locator": ParameterLocator(
                    page=1,
                    table="electrical_facts",
                    row="supply_voltage",
                    section="extra",
                    clause="extra",
                )
            },
            "LOCATOR_UNRESOLVED",
        ),
    ],
)
def test_candidate_identity_and_facts_cannot_be_substituted(
    tmp_path: Path, changes: dict, code: str
) -> None:
    port, candidate = _project(tmp_path, _source())
    with pytest.raises(ValueError, match=code):
        port.read_parameter(_changed(candidate, **changes))


def test_mapping_rejects_other_project_and_extra_fact_field(tmp_path: Path) -> None:
    port, candidate = _project(tmp_path, _source())
    _change_map(tmp_path, lambda mapping: mapping.update(project_id="other"))
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        port.read_parameter(candidate)
    _change_map(
        tmp_path,
        lambda mapping: (
            mapping.update(project_id="project"),
            mapping["documents"][0].update(value_decimal="1.62"),
        ),
    )
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        port.read_parameter(candidate)


@pytest.mark.parametrize(
    "relative",
    [
        "../source.json",
        "/source.json",
        "C:/source.json",
        "\\\\host\\share\\source.json",
    ],
)
def test_mapping_rejects_unsafe_paths(tmp_path: Path, relative: str) -> None:
    port, candidate = _project(tmp_path, _source())
    _change_map(
        tmp_path, lambda mapping: mapping["documents"][0].update(relative_path=relative)
    )
    with pytest.raises(ValueError, match="MAPPING_PATH_INVALID"):
        port.read_parameter(candidate)


def test_missing_and_changed_original(tmp_path: Path) -> None:
    port, candidate = _project(tmp_path, _source())
    (tmp_path / "source.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="CONTENT_MISMATCH"):
        port.read_parameter(candidate)
    (tmp_path / "source.json").unlink()
    with pytest.raises(ValueError, match="DOCUMENT_UNAVAILABLE"):
        port.read_parameter(candidate)


def test_duplicate_mapping_and_wrong_source_rejected(tmp_path: Path) -> None:
    source = _source()
    port, candidate = _project(tmp_path, source)
    _change_map(
        tmp_path,
        lambda mapping: mapping["documents"].append(dict(mapping["documents"][0])),
    )
    with pytest.raises(ValueError, match="MAPPING_AMBIGUOUS"):
        port.read_parameter(candidate)
    _change_map(tmp_path, lambda mapping: mapping["documents"].pop())
    other = ComponentSourceRecord.create(
        **{
            **source.model_dump(mode="python", exclude={"fingerprint"}),
            "description": "Different synthetic source",
        }
    )
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        port.read_component_source(other, (candidate,))


def test_component_source_requires_independent_json_original(tmp_path: Path) -> None:
    source = _source()
    port, candidate = _project(tmp_path, source)
    _change_map(tmp_path, lambda mapping: mapping["documents"][0].update(kind="PDF"))
    with pytest.raises(ValueError, match="UNSUPPORTED_COMPONENT_SOURCE"):
        port.read_component_source(source, (candidate,))


def test_synthetic_pdf_table_profile_requires_exact_header_row_and_locator(
    monkeypatch,
) -> None:
    # Synthetic parser output exercises the finite table contract, not a real datasheet.
    rows = (
        (
            "Parameter",
            "Symbol",
            "Conditions",
            "",
            "Min",
            "Typ.",
            "Max",
            "Units",
            "Comments",
        ),
        ("Supply voltage", "V DD", "", "", "1.62", "3.3", "3.6", "V", "-"),
    )
    page = PdfPageStructure(
        page_number=3,
        text="Default conditions of 25 °C and 3.3 V supply voltage apply\n"
        "Table 3 Electrical specifications.\nVersion 4 – December 2022",
        tables=(TableStructure(rows=rows),),
    )
    structure = PdfStructure(
        page_count=6,
        pages=(
            PdfPageStructure(
                page_number=1,
                text="Datasheet SHTC3\nSmall DFN package: 2 × 2 × 0.75 mm3",
                tables=(),
            ),
            PdfPageStructure(page_number=2, text="", tables=()),
            page,
        ),
    )
    structure = PdfStructure(
        page_count=6,
        pages=(
            *structure.pages,
            PdfPageStructure(page_number=4, text="", tables=()),
            PdfPageStructure(page_number=5, text="", tables=()),
            PdfPageStructure(
                page_number=6,
                text="The SHTC3 comes in a 4-pin package\nTable 7 SHTC3 pin assignment",
                tables=(),
            ),
        ),
    )
    monkeypatch.setattr(readers, "_pdf_structure", lambda *_args, **_kwargs: structure)
    entry = SimpleNamespace(
        manufacturer="Sensirion",
        mpn="SHTC3",
        package="DFN-4 2x2 mm",
        document_revision="Version 4 - December 2022",
    )
    locator = ParameterLocator(page=3, table="Table 3", row="Supply voltage")
    assert readers._pdf_fact(entry, b"synthetic", locator, "SUPPLY_MAX") == (
        "MAX",
        "3.6",
        "V",
        "UNKNOWN",
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_DOCUMENT"):
        readers._pdf_fact(
            SimpleNamespace(**{**vars(entry), "document_revision": "r2"}),
            b"synthetic",
            locator,
            "SUPPLY_MAX",
        )
    with pytest.raises(ValueError, match="UNSUPPORTED_DOCUMENT"):
        readers._pdf_fact(
            SimpleNamespace(**{**vars(entry), "package": "UNKNOWN"}),
            b"synthetic",
            locator,
            "SUPPLY_MAX",
        )
    with pytest.raises(ValueError, match="LOCATOR_UNRESOLVED"):
        readers._pdf_fact(
            entry,
            b"synthetic",
            ParameterLocator(page=3, table="Table 3", row="Other"),
            "SUPPLY_MAX",
        )
    page_bad = PdfPageStructure(
        page_number=3,
        text=page.text,
        tables=(TableStructure(rows=(rows[0], rows[1], rows[1])),),
    )
    monkeypatch.setattr(
        readers,
        "_pdf_structure",
        lambda *_args, **_kwargs: PdfStructure(
            page_count=6, pages=(*structure.pages[:2], page_bad, *structure.pages[3:])
        ),
    )
    with pytest.raises(ValueError, match="LOCATOR_UNRESOLVED"):
        readers._pdf_fact(entry, b"synthetic", locator, "SUPPLY_MAX")


def test_synthetic_yageo_profile_rejects_duplicate_row_and_wrong_case(
    monkeypatch,
) -> None:
    text = (
        "RC0603FR-0710KL\nSpecifications\nCase Code (EIA / mm) 0603 / 1608\n"
        "Resistance 10 kOhms\nResistance Tolerance 1%\nGenerated 10/03/2026"
    )
    page = PdfPageStructure(page_number=1, text=text, tables=())
    monkeypatch.setattr(
        readers,
        "_pdf_structure",
        lambda *_args, **_kwargs: PdfStructure(page_count=1, pages=(page,)),
    )
    entry = SimpleNamespace(
        manufacturer="YAGEO",
        mpn="RC0603FR-0710KL",
        package="0603 / 1608",
        document_revision="Generated 10/03/2026",
    )
    locator = ParameterLocator(page=1, table="Specifications", row="Resistance")
    assert readers._pdf_fact(entry, b"synthetic", locator, "PULLUP_RESISTANCE") == (
        "EXACT",
        "10000",
        "ohm",
        "UNKNOWN",
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_DOCUMENT"):
        readers._pdf_fact(
            SimpleNamespace(**{**vars(entry), "package": "0805 / 2012"}),
            b"synthetic",
            locator,
            "PULLUP_RESISTANCE",
        )
    with pytest.raises(ValueError, match="UNSUPPORTED_DOCUMENT"):
        readers._pdf_fact(
            SimpleNamespace(**{**vars(entry), "document_revision": "r2"}),
            b"synthetic",
            locator,
            "PULLUP_RESISTANCE",
        )
    monkeypatch.setattr(
        readers,
        "_pdf_structure",
        lambda *_args, **_kwargs: PdfStructure(
            page_count=1,
            pages=(
                PdfPageStructure(
                    page_number=1, text=text + "\nResistance 10 kOhms", tables=()
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="LOCATOR_UNRESOLVED"):
        readers._pdf_fact(entry, b"synthetic", locator, "PULLUP_RESISTANCE")


def test_synthetic_nxp_specification_sentinel_and_revision(monkeypatch) -> None:
    # This sentinel identifies a bus specification, never a physical package.
    header = (
        "Symbol",
        "Parameter",
        "Conditions",
        "Standar d-mode",
        "",
        "Fast-mode",
        "",
        "Fast-mode Plus",
        "",
        "Unit",
    )
    units = ("", "", "", "Min", "Max", "Min", "Max", "Min", "Max", "")
    rise = (
        "tr",
        "rise time of both SDA and SCL signals",
        "",
        "-",
        "1000",
        "20",
        "300",
        "-",
        "120",
        "ns",
    )
    pages = tuple(
        PdfPageStructure(
            page_number=i + 1,
            text=(
                "NXP Semiconductors UM10204\nRev. 7.0 — 1 October 2021\n"
                "Table 11. Standard, Fast, and Fast-mode Plus"
            )
            if i == 43
            else "",
            tables=(TableStructure(rows=(header, units, rise)),) if i == 43 else (),
        )
        for i in range(44)
    )
    monkeypatch.setattr(
        readers,
        "_pdf_structure",
        lambda *_args, **_kwargs: PdfStructure(page_count=44, pages=pages),
    )
    entry = SimpleNamespace(
        manufacturer="NXP",
        mpn="UM10204",
        package="I2C_BUS_SPECIFICATION",
        document_revision="Rev. 7.0 — 1 October 2021",
    )
    locator = ParameterLocator(
        page=44, table="Table 11", row="rise time of both SDA and SCL signals"
    )
    assert readers._pdf_fact(entry, b"synthetic", locator, "RISE_TIME_MAX") == (
        "MAX",
        "0.000001",
        "s",
        "I2C_STANDARD",
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_DOCUMENT"):
        readers._pdf_fact(
            SimpleNamespace(**{**vars(entry), "package": "SENSOR-PACKAGE"}),
            b"synthetic",
            locator,
            "RISE_TIME_MAX",
        )
    with pytest.raises(ValueError, match="UNSUPPORTED_DOCUMENT"):
        readers._pdf_fact(
            SimpleNamespace(**{**vars(entry), "document_revision": "Rev. 6"}),
            b"synthetic",
            locator,
            "RISE_TIME_MAX",
        )

    # Truncated extraction must reject with the reader's fail-closed error.
    truncated = PdfPageStructure(
        page_number=44, text=pages[43].text, tables=(TableStructure(rows=(header,)),)
    )
    monkeypatch.setattr(
        readers,
        "_pdf_structure",
        lambda *_args, **_kwargs: PdfStructure(
            page_count=44, pages=(*pages[:43], truncated)
        ),
    )
    with pytest.raises(ValueError, match="LOCATOR_UNRESOLVED"):
        readers._pdf_fact(entry, b"synthetic", locator, "RISE_TIME_MAX")
