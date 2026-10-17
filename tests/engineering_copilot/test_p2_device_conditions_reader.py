"""Finite, opt-in original-byte proof for P2 device conditions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.engineering_copilot.schematic_evidence import ParameterConditions
from embedded_copilot.engineering_copilot.schematic_parameter_readers import ProjectMappedParameterPort

DIGEST = "dbe979c975fe520663606b5c278d2b9130f4e402e6d9d943ca06c4e4f38e63d9"


@pytest.fixture
def mapped_original(tmp_path: Path):
    roots = os.environ.get("EMBRIX_D2_REAL_ORIGINAL_ROOTS")
    if not roots:
        pytest.skip("NOT_RUN: acquired Sensirion original root required")
    matches = [Path(root) / "shtc3.pdf" for root in roots.split(os.pathsep)
               if (Path(root) / "shtc3.pdf").is_file()]
    assert len(matches) == 1
    raw = matches[0].read_bytes()
    assert hashlib.sha256(raw).hexdigest() == DIGEST

    source = ComponentSourceRecord.create(
        source_id="shtc3-source", source_uri="local:shtc3-source", source_revision="r1",
        manufacturer="Sensirion", mpn="SHTC3", category="SENSOR",
        package="DFN-4 2x2 mm", description="Mapped test source",
        symbol="SHTC3_EP:SHTC3_EP", footprint="SHTC3_EP:Footprint",
        supported_roles=("SENSOR",),
    )
    original = source.model_dump_json().encode()
    (tmp_path / ".embrix").mkdir()
    (tmp_path / "source.json").write_bytes(original)
    shutil.copyfile(matches[0], tmp_path / "shtc3.pdf")
    facts = [
        {
            "kind": kind, "document_uri": uri, "document_revision": rev,
            "relative_path": path, "content_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "source_record_id": source.source_id,
            "source_record_fingerprint": source.fingerprint,
            "manufacturer": source.manufacturer, "mpn": source.mpn, "package": source.package,
        }
        for kind, uri, rev, path, data in (
            ("COMPONENT_SOURCE_JSON", source.source_uri, source.source_revision, "source.json", original),
            ("PDF", "sensirion:SHTC3", "Version 4 - December 2022", "shtc3.pdf", raw),
        )
    ]
    mapping = {"schema_version": 1, "tenant_id": "tenant", "project_id": "project", "documents": facts}
    path = tmp_path / ".embrix" / "parameter-documents.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    port = ProjectMappedParameterPort(lambda tenant, project: tmp_path)

    return port, source, mapping, path


def test_shtc3_conditions_are_rederived_from_current_mapped_original(mapped_original):
    port, source, mapping, path = mapped_original
    observed = port.read_device_conditions(source, tenant_id="tenant", project_id="project")
    assert observed == ParameterConditions(
        voltage_min="1.62", voltage_max="3.6", temperature_min="5",
        temperature_max="60", mode="I2C_STANDARD", package="DFN-4 2x2 mm",
    )
    assert observed.covers(ParameterConditions(
        voltage_min="3.20", voltage_max="3.40", temperature_min="20",
        temperature_max="30", mode="I2C_STANDARD", package="DFN-4 2x2 mm",
    ))

    mapping["documents"][1]["source_record_fingerprint"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(ValueError):
        port.read_device_conditions(source, tenant_id="tenant", project_id="project")

@pytest.mark.parametrize("fault", ("tenant", "project", "unknown_source", "unknown_package", "duplicate_pdf", "source_bytes", "pdf_bytes", "map_during_io", "pdf_during_io"))
def test_shtc3_conditions_reject_unknown_scope_and_original_drift(mapped_original, monkeypatch, fault):
    from embedded_copilot.engineering_copilot import schematic_parameter_readers as readers
    port, source, mapping, path = mapped_original
    scope = {"tenant_id": "tenant", "project_id": "project"}
    if fault in {"tenant", "project"}:
        scope[f"{fault}_id"] = "wrong"
    elif fault in {"unknown_source", "unknown_package"}:
        values = source.model_dump(mode="python")
        values["fingerprint"] = "sha256:" + "0" * 64
        values["mpn" if fault == "unknown_source" else "package"] = "UNKNOWN"
        source = ComponentSourceRecord.create(**values)
    elif fault == "duplicate_pdf":
        mapping["documents"].append({**mapping["documents"][1], "document_uri": "other:SHTC3"})
        path.write_text(json.dumps(mapping), encoding="utf-8")
    elif fault in {"source_bytes", "pdf_bytes"}:
        target = path.parent.parent / ("source.json" if fault == "source_bytes" else "shtc3.pdf")
        target.write_bytes(target.read_bytes() + b"changed")
    else:
        original = readers._original
        calls = 0
        def drift(root, entry):
            nonlocal calls
            observed = original(root, entry)
            calls += 1
            if calls == 2:
                if fault == "map_during_io":
                    mapping["documents"][1]["document_uri"] = "changed:SHTC3"
                    path.write_text(json.dumps(mapping), encoding="utf-8")
                else:
                    (root / entry.relative_path).write_bytes(observed + b"changed")
            return observed
        monkeypatch.setattr(readers, "_original", drift)
    with pytest.raises(ValueError):
        port.read_device_conditions(source, **scope)


@pytest.mark.parametrize("change", ({"voltage_min": "1.5"}, {"voltage_max": "4"},
    {"temperature_min": "0"}, {"temperature_max": "70"}, {"mode": "UNKNOWN"}, {"package": "other"}))
def test_shtc3_operating_envelope_does_not_cover_unknown_or_excessive_corner(mapped_original, change):
    port, source, _mapping, _path = mapped_original
    observed = port.read_device_conditions(source, tenant_id="tenant", project_id="project")
    required = ParameterConditions(voltage_min="3.20", voltage_max="3.40",
        temperature_min="20", temperature_max="30", mode="I2C_STANDARD", package=source.package)
    assert not observed.covers(required.model_copy(update=change))
