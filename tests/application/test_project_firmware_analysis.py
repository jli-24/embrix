from __future__ import annotations

from pathlib import Path

from embedded_copilot.application.project_firmware_analysis import (
    ProjectFirmwareAnalysisPort,
)


class _Catalog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple[str, str]] = []

    def knowledge_snapshot_root(self, project_id: str, *, tenant_id: str) -> Path:
        self.calls.append((tenant_id, project_id))
        if tenant_id != "tenant-1" or project_id != "project-1":
            raise ValueError("project identity mismatch")
        return self.root


def test_project_firmware_port_exposes_parser_evidence_only(tmp_path) -> None:
    firmware = tmp_path / "firmware" / "Core" / "Src"
    firmware.mkdir(parents=True)
    (firmware / "i2c.c").write_text(
        "void MX_I2C1_Init(void) {}\nint sensor_read(void) { return MX_I2C1_Init(); }\n",
        encoding="utf-8",
    )
    catalog = _Catalog(tmp_path)
    port = ProjectFirmwareAnalysisPort(project_catalog=catalog)

    result = port.analyze(
        tenant_id="tenant-1",
        request=type("Request", (), {"project_id": "project-1", "question": "I2C init"})(),
    )

    assert result["project_id"] == "project-1"
    assert result["evidence"]
    assert all("content" not in item for item in result["evidence"])
    assert all("C:" not in str(item) for item in result["evidence"])
    assert catalog.calls == [("tenant-1", "project-1")]
