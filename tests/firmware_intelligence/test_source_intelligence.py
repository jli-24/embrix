from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.firmware_intelligence import (
    FirmwareIntelligenceService,
    FirmwareSourceProvider,
    FirmwareSourceSnapshot,
    FirmwareSourceUnavailable,
)
from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.firmware_agent_adapter import FirmwareAgentApplicationAdapter
from embedded_copilot.application.desktop_composition import (
    _FirmwareChatProjectProjection,
)
from embedded_copilot.desktop_copilot_chat import (
    ChatProjectReference,
    ChatReferenceKind,
)


def _project(root: Path, name: str, *, source: str) -> Path:
    project = root / name
    folder = project / "firmware" / "Core" / "Src"
    folder.mkdir(parents=True)
    (folder / "i2c.c").write_text(
        "#include \"sensor.h\"\n"
        "#define SENSOR_ADDR 0x68\n"
        "struct Device { int state; };\n"
        "enum I2cState { I2C_READY };\n"
        "int i2c_speed = 400000;\n"
        "void MX_I2C1_Init(void) { }\n",
        encoding="utf-8",
    )
    (folder / "sensor.c").write_text(source, encoding="utf-8")
    include = project / "firmware" / "Core" / "Inc"
    include.mkdir()
    (include / "sensor.h").write_text("int sensor_read(void);\n", encoding="utf-8")
    (project / "firmware" / "compile_commands.json").write_text("[]", encoding="utf-8")
    (project / "firmware" / "firmware.elf").write_bytes(b"ELF")
    return project


def _service(root: Path) -> FirmwareIntelligenceService:
    mapping = {
        "project-a": root / "project-a",
        "project-b": root / "project-b",
    }
    return FirmwareIntelligenceService(
        source_provider=FirmwareSourceProvider(lambda project_id: mapping[project_id])
    )


def test_source_provider_and_parser_produce_project_bound_firmware_evidence(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "project-a",
        source=(
            "int sensor_read(void) {\n"
            "  return HAL_I2C_Master_Transmit();\n"
            "}\n"
        ),
    )
    project_b = tmp_path / "project-b"
    (project_b / "firmware" / "Core" / "Src").mkdir(parents=True)
    (project_b / "firmware" / "Core" / "Src" / "other.c").write_text(
        "int other(void) { return 0; }\n", encoding="utf-8"
    )
    service = _service(tmp_path)

    index = service.index("project-a")
    kinds = {(item.symbol_name, item.symbol_type) for item in index.symbols}
    assert {
        ("MX_I2C1_Init", "function"),
        ("sensor_read", "function"),
        ("Device", "struct"),
        ("I2cState", "enum"),
        ("SENSOR_ADDR", "macro"),
        ("sensor.h", "include"),
        ("i2c_speed", "variable"),
    } <= kinds
    assert any(
        item.caller == "sensor_read" and item.callee == "HAL_I2C_Master_Transmit"
        for item in index.calls
    )

    result = service.analyze("project-a", "I2C在哪里初始化？")
    evidence = next(item for item in result.evidence if item.subject == "MX_I2C1_Init")
    assert evidence.project_id == "project-a"
    assert evidence.source_type.value == "FIRMWARE_SOURCE"
    assert evidence.source_reference == "Core/Src/i2c.c"
    assert "C:" not in evidence.summary
    assert "void MX_I2C1_Init" not in evidence.summary

    other = service.index("project-b")
    assert all(item.project_id == "project-b" for item in other.symbols)
    assert not any(item.symbol_name == "MX_I2C1_Init" for item in other.symbols)


def test_analyze_indexes_the_snapshot_already_captured_for_the_request(
    tmp_path: Path,
) -> None:
    project_root = _project(
        tmp_path,
        "project-a",
        source="int sensor_read(void) { return HAL_I2C_Master_Transmit(); }\n",
    )
    captured = FirmwareSourceProvider(lambda _: project_root).snapshot("project-a")

    class CountingProvider(FirmwareSourceProvider):
        def __init__(self) -> None:
            super().__init__(lambda _: project_root)
            self.calls = 0

        def snapshot(
            self, project_id: str, *, allow_empty_source: bool = False
        ) -> FirmwareSourceSnapshot:
            del project_id, allow_empty_source
            self.calls += 1
            return captured

    provider = CountingProvider()
    result = FirmwareIntelligenceService(source_provider=provider).analyze(
        "project-a", "sensor_read在哪里？"
    )

    assert provider.calls == 1
    assert result.source_fingerprint == captured.fingerprint


def test_sensor_analysis_reports_missing_debug_evidence_without_unique_root_cause(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "project-a",
        source="int sensor_read(void) { return HAL_I2C_Master_Transmit(); }\n",
    )
    _project(tmp_path, "project-b", source="int other(void) { return 0; }\n")
    result = _service(tmp_path).analyze("project-a", "sensor_read为什么失败？")

    assert any("sensor_read -> HAL_I2C_Master_Transmit" in item for item in result.analysis)
    assert "missing debug evidence; no unique failure cause is asserted" in result.limitations
    assert result.evidence


def test_inventory_question_emits_only_parser_derived_file_evidence(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "project-a",
        source="int sensor_read(void) { return HAL_I2C_Master_Transmit(); }\n",
    )
    _project(tmp_path, "project-b", source="int other(void) { return 0; }\n")

    result = _service(tmp_path).analyze("project-a", "工程有哪些Firmware文件？")

    files = {item.source_reference for item in result.evidence}
    assert {"Core/Src/i2c.c", "Core/Src/sensor.c", "Core/Inc/sensor.h"} <= files
    assert all(item.source_type is ContextSourceType.FIRMWARE_SOURCE for item in result.evidence)
    assert all(item.fact_key == "parsed_symbols" for item in result.evidence)
    assert all("void " not in item.summary for item in result.evidence)
    assert all("C:" not in item.summary for item in result.evidence)


def test_chat_projection_exposes_only_parser_derived_firmware_references(
    tmp_path: Path,
) -> None:
    _project(
        tmp_path,
        "project-a",
        source="int sensor_read(void) { return HAL_I2C_Master_Transmit(); }\n",
    )
    _project(tmp_path, "project-b", source="int other(void) { return 0; }\n")

    class CatalogProjection:
        def project(self, tenant_id: str, project_id: str) -> ChatProjectReference:
            if tenant_id != "tenant-a" or project_id != "project-a":
                raise ValueError("project binding is invalid")
            return ChatProjectReference(
                tenant_id=tenant_id,
                project_id=project_id,
                project_name_reference="Firmware Project",
                workspace_reference="project:project-a",
            )

        def references(self, tenant_id: str, project_id: str) -> tuple[object, ...]:
            self.project(tenant_id, project_id)
            return ()

    projection = _FirmwareChatProjectProjection(
        CatalogProjection(), _service(tmp_path)
    )

    references = projection.references("tenant-a", "project-a")

    firmware = tuple(
        item for item in references if item.reference_kind is ChatReferenceKind.EVIDENCE
    )
    assert any(
        item.display_label == "FIRMWARE_SOURCE · Core/Src/i2c.c · MX_I2C1_Init · L6"
        for item in firmware
    )
    assert any("sensor_read" in item.display_label for item in firmware)
    assert all(item.project_id == "project-a" for item in firmware)
    assert all("C:" not in item.display_label for item in firmware)
    assert all("void " not in item.display_label for item in firmware)

    with pytest.raises(ValueError, match="project binding"):
        projection.references("tenant-a", "project-b")


def test_sensor_analysis_correlates_only_existing_debug_timeout_evidence(tmp_path: Path) -> None:
    _project(
        tmp_path,
        "project-a",
        source="int sensor_read(void) { return HAL_I2C_Master_Transmit(); }\n",
    )
    _project(tmp_path, "project-b", source="int other(void) { return 0; }\n")
    debug = EngineeringContextEvidence.create(
        evidence_id="debug-i2c-timeout",
        project_id="project-a",
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="i2c timeout observation",
        summary="observed HAL_TIMEOUT on I2C1",
        source_reference="knowledge:debug-i2c-timeout",
        source_fingerprint="sha256:" + "d" * 64,
        confidence=1.0,
        verification_status=ContextVerificationStatus.PROJECTED,
    )

    result = _service(tmp_path).analyze(
        "project-a", "sensor_read为什么失败？", context_evidence=(debug,)
    )

    assert any("HAL_TIMEOUT" in item for item in result.analysis)
    assert "missing debug evidence; no unique failure cause is asserted" not in result.limitations
    assert all(item.project_id == "project-a" for item in result.evidence)


def test_debug_evidence_with_another_project_is_rejected(tmp_path: Path) -> None:
    _project(tmp_path, "project-a", source="int sensor_read(void) { return 0; }\n")
    _project(tmp_path, "project-b", source="int other(void) { return 0; }\n")
    other = EngineeringContextEvidence.create(
        evidence_id="debug-other",
        project_id="project-b",
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="timeout",
        summary="observed HAL_TIMEOUT",
        source_reference="knowledge:debug-other",
        source_fingerprint="sha256:" + "e" * 64,
        confidence=1.0,
        verification_status=ContextVerificationStatus.PROJECTED,
    )

    with pytest.raises(ValueError, match="debug evidence binding"):
        _service(tmp_path).analyze(
            "project-a", "sensor_read为什么失败？", context_evidence=(other,)
        )


def test_firmware_agent_trace_is_emitted_only_after_real_source_analysis(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _project(
        tmp_path,
        "project-a",
        source="int sensor_read(void) { return HAL_I2C_Master_Transmit(); }\n",
    )
    _project(tmp_path, "project-b", source="int other(void) { return 0; }\n")
    request = ApplicationQuestionRequest(
        request_id="request-firmware-trace",
        trace_id="trace-firmware-runtime",
        project_id="project-a",
        requester="engineer-1",
        question="I2C在哪里初始化？",
        constraints=(),
        requested_at=datetime(2026, 8, 13, tzinfo=UTC),
    )
    context = EngineeringContextSnapshot.create(
        project_id="project-a",
        task_description=request.question,
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status=ContextVerificationStatus.VERIFIED,
    )
    adapter = FirmwareAgentApplicationAdapter(_service(tmp_path))

    with caplog.at_level(logging.INFO, logger="embedded_copilot.firmware_agent_adapter"):
        result = asyncio.run(adapter.aanalyze(request, context))

    records = {
        getattr(record, "event_name", None): record
        for record in caplog.records
        if getattr(record, "trace_id", None) == request.trace_id
    }
    assert result.context_evidence
    assert records["agent_selected"].agent_name == "firmware"
    executed = records["agent_execution"]
    assert executed.agent_name == "firmware"
    assert executed.project_id == "project-a"
    assert executed.source_provider == "FirmwareSourceProvider"
    assert executed.parser == "TreeSitterCodeParser"
    assert executed.evidence_count == len(result.context_evidence)


def test_provider_ignores_unsupported_files_and_exposes_binary_metadata_only(tmp_path: Path) -> None:
    project = _project(tmp_path, "project-a", source="int sensor_read(void) { return 0; }\n")
    _project(tmp_path, "project-b", source="int other(void) { return 0; }\n")
    (project / "firmware" / "notes.pdf").write_bytes(b"not source")
    provider = FirmwareSourceProvider(lambda project_id: tmp_path / project_id)
    snapshot = provider.snapshot("project-a")

    assert all(not item.reference.relative_path.endswith(".pdf") for item in snapshot.sources)
    artifact = snapshot.artifacts[0]
    assert artifact.reference.relative_path == "firmware.elf"
    assert not hasattr(artifact, "content")


def test_provider_accepts_direct_stm32_core_layout_and_preserves_relative_provenance(
    tmp_path: Path,
) -> None:
    project = tmp_path / "direct-stm32"
    (project / "Core" / "Inc").mkdir(parents=True)
    (project / "Core" / "Src").mkdir()
    (project / "Core" / "Inc" / "sensor.h").write_text(
        "int sensor_read(void);\n", encoding="utf-8"
    )
    (project / "Core" / "Src" / "main.c").write_text(
        "void MX_I2C1_Init(void) {}\n"
        "int sensor_read(void) { return HAL_I2C_Master_Transmit(); }\n",
        encoding="utf-8",
    )

    service = FirmwareIntelligenceService(
        source_provider=FirmwareSourceProvider(lambda _project_id: project)
    )
    snapshot = service.source_snapshot("direct-stm32")
    result = service.analyze("direct-stm32", "sensor_read调用了什么HAL接口？")

    assert {item.reference.relative_path for item in snapshot.sources} == {
        "Core/Inc/sensor.h",
        "Core/Src/main.c",
    }
    assert all(item.reference.relative_path.startswith("Core/") for item in snapshot.sources)
    assert any(
        item.subject == "sensor_read"
        and item.fact_key == "CALLS"
        and item.fact_value == "HAL_I2C_Master_Transmit"
        and item.source_reference == "Core/Src/main.c"
        and item.source_fingerprint == result.source_fingerprint
        and "firmware_source_parser:v1" in item.provenance
        and "static_call_relation" in item.provenance
        for item in result.evidence
    )
    assert all(item.project_id == "direct-stm32" for item in result.evidence)


def test_empty_project_has_empty_snapshot_without_fake_evidence(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    provider = FirmwareSourceProvider(lambda _project_id: project)

    snapshot = provider.snapshot("empty", allow_empty_source=True)

    assert snapshot.sources == ()
    assert snapshot.artifacts == ()
    assert provider.is_empty_project("empty") is True


def test_missing_symbol_produces_zero_firmware_evidence(tmp_path: Path) -> None:
    _project(tmp_path, "project-a", source="int sensor_read(void) { return 0; }\n")

    result = _service(tmp_path).analyze("project-a", "usb_audio_stream_start在哪里？")

    assert result.evidence == ()


def test_provider_fails_closed_for_symlink_and_missing_firmware_root(tmp_path: Path) -> None:
    project = tmp_path / "project-a"
    project.mkdir()
    provider = FirmwareSourceProvider(lambda _project_id: project)
    with pytest.raises(FirmwareSourceUnavailable):
        provider.snapshot("project-a")

    target = tmp_path / "outside.c"
    target.write_text("int outside(void) { return 0; }\n", encoding="utf-8")
    firmware = project / "firmware"
    firmware.mkdir()
    try:
        os.symlink(target, firmware / "outside.c")
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(FirmwareSourceUnavailable):
        provider.snapshot("project-a")
