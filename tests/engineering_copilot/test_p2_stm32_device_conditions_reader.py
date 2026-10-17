"""Approved DS5319 finite envelope; acquired bytes are opt-in, never project facts."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.engineering_copilot import schematic_parameter_readers as readers
from embedded_copilot.engineering_copilot.schematic_evidence import ParameterConditions

DIGEST = "1e54ce18e6c34c5b4a78986f26e245e143748c721009fc9af5953795c30686bc"
URI = "https://www.st.com/resource/en/datasheet/stm32f103r8.pdf"


@pytest.fixture
def mapped_stm32(tmp_path):
    roots = os.environ.get("EMBRIX_D2_REAL_ORIGINAL_ROOTS")
    if not roots:
        pytest.skip("NOT_RUN: acquired DS5319 original root required")
    matches = [Path(root) / "stm32f103r8.pdf" for root in roots.split(os.pathsep)
               if (Path(root) / "stm32f103r8.pdf").is_file()]
    assert len(matches) == 1
    raw = matches[0].read_bytes()
    assert len(raw) == 1965230 and hashlib.sha256(raw).hexdigest() == DIGEST
    source = ComponentSourceRecord.create(
        source_id="mcu-source", source_uri="local:stm32f103c8-source", source_revision="r1",
        manufacturer="STMicroelectronics", mpn="STM32F103C8T6", category="MCU",
        package="LQFP48", description="Isolated test source, not formal Selection",
        symbol="Fixture:STM32F103C8T6", footprint="Fixture:LQFP48", supported_roles=("MCU",),
    )
    original = source.model_dump_json().encode("utf-8")
    (tmp_path / ".embrix").mkdir()
    (tmp_path / "source.json").write_bytes(original)
    (tmp_path / "stm32f103r8.pdf").write_bytes(raw)
    documents = [dict(kind=kind, document_uri=uri, document_revision=rev, relative_path=path,
        content_sha256=readers._sha(data), source_record_id=source.source_id,
        source_record_fingerprint=source.fingerprint, manufacturer=source.manufacturer,
        mpn=source.mpn, package=source.package)
        for kind,uri,rev,path,data in (
            ("COMPONENT_SOURCE_JSON",source.source_uri,source.source_revision,"source.json",original),
            ("PDF",URI,"DS5319 Rev 20","stm32f103r8.pdf",raw))]
    mapping = dict(schema_version=1, tenant_id="tenant", project_id="project", documents=documents)
    path = tmp_path / ".embrix" / "parameter-documents.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return readers.ProjectMappedParameterPort(lambda *_:tmp_path),source,mapping,path


def test_stm32_exact_explicit_source_map_derives_only_operating_envelope(mapped_stm32):
    port,source,mapping,path = mapped_stm32
    before = path.read_bytes(), (path.parent.parent / "source.json").read_bytes()
    result = port.read_device_conditions(source, tenant_id="tenant", project_id="project")
    assert result == ParameterConditions(voltage_min="2", voltage_max="3.6", temperature_min="-40",
        temperature_max="85", mode="I2C_STANDARD", package="LQFP48")
    assert result.covers(ParameterConditions(voltage_min="3.20",voltage_max="3.40",
        temperature_min="20",temperature_max="30",mode="I2C_STANDARD",package="LQFP48"))
    assert source.source_uri == "local:stm32f103c8-source"
    assert mapping["documents"][1]["document_uri"] == URI
    assert before == (path.read_bytes(), (path.parent.parent / "source.json").read_bytes())
    # Classification carries no peripheral clock/configuration or 100 kHz guarantee.
    assert set(ParameterConditions.model_fields) == {"voltage_min","voltage_max","temperature_min",
        "temperature_max","mode","package"}


@pytest.mark.parametrize("fault", ("tenant","project","manufacturer","mpn","suffix7","package",
    "uri","revision","source_pair","duplicate_pdf","source_bytes","pdf_bytes",
    "map_during_io","source_during_io","pdf_during_io","page_count","coverage","voltage",
    "temperature","standard_mode","ordering"))
def test_stm32_rejects_unknown_identity_original_and_io_drift(mapped_stm32, monkeypatch, fault):
    port,source,mapping,path = mapped_stm32
    scope = dict(tenant_id="tenant",project_id="project")
    if fault in {"tenant","project"}:
        scope[f"{fault}_id"] = "wrong"
    elif fault in {"manufacturer","mpn","suffix7","package"}:
        changes = {"manufacturer":"ST", "mpn":"STM32F103R8T6","suffix7":"STM32F103C8T7","package":"LQFP64"}
        field = "mpn" if fault == "suffix7" else fault
        source = ComponentSourceRecord.create(**(source.model_dump(mode="python",exclude={"fingerprint"}) | {field:changes[fault]}))
    elif fault in {"uri","revision","source_pair","duplicate_pdf"}:
        entry = mapping["documents"][1]
        if fault == "duplicate_pdf":
            mapping["documents"].append(dict(entry, document_uri="other:DS5319"))
        else:
            field,value = {"uri":("document_uri","https://www.st.com/resource/en/datasheet/stm32f103c8.pdf"),
                "revision":("document_revision","DS5319 Rev 19"),
                "source_pair":("source_record_fingerprint",readers._sha(b"other"))}[fault]
            entry[field] = value
        path.write_text(json.dumps(mapping),encoding="utf-8")
    elif fault in {"source_bytes","pdf_bytes"}:
        target = path.parent.parent / ("source.json" if fault == "source_bytes" else "stm32f103r8.pdf")
        target.write_bytes(target.read_bytes() + b"changed")
    elif fault.endswith("during_io"):
        original = readers._original
        calls = 0
        def drift(root,entry):
            nonlocal calls
            result = original(root,entry)
            calls += 1
            if calls == 2:
                if fault == "map_during_io":
                    mapping["documents"][1]["document_uri"] = "changed:DS5319"
                    path.write_text(json.dumps(mapping),encoding="utf-8")
                else:
                    target = root / ("source.json" if fault == "source_during_io" else entry.relative_path)
                    target.write_bytes(target.read_bytes() + b"changed")
            return result
        monkeypatch.setattr(readers,"_original",drift)
    else:
        real_open = readers.fitz.open
        with real_open(path.parent.parent / "stm32f103r8.pdf") as document:
            texts = [document[i].get_text() for i in range(len(document))]
        index,old = {"coverage":(0,"STM32F103C8"),"voltage":(37,"3.6"),
            "temperature":(37,"85"),"standard_mode":(67,"Standard mode"),
            "ordering":(103,"6 = Industrial temperature range")}.get(fault,(0,""))
        if fault != "page_count": texts[index] = texts[index].replace(old,"UNKNOWN")
        class Document:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def __len__(self):return 113 if fault == "page_count" else 114
            def __getitem__(self,i):
                class Page:
                    def get_text(self):return texts[i]
                return Page()
        monkeypatch.setattr(readers.fitz,"open",lambda **_:Document())
    with pytest.raises(ValueError):
        port.read_device_conditions(source,**scope)