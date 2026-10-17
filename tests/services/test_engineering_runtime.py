from __future__ import annotations

import asyncio
from pathlib import Path

import fitz

from embedded_copilot import __version__
from embedded_copilot.engineering import config as engineering_config
from embedded_copilot.engineering.adapter import EngineeringSupervisorAdapter
from embedded_copilot.engineering.agent_adapters import HardwareAgentInputAdapter
from embedded_copilot.engineering.config import EngineeringExtensionSettings
from embedded_copilot.hardware.agent import HardwareAgent
from embedded_copilot.hardware_design.adapter import (
    HardwareBlueprintProjectionAgentAdapter,
)
from embedded_copilot.input.models import (
    AttachmentType,
    UnifiedInputContext,
    UserAttachment,
)
from embedded_copilot.services.analysis import AnalysisCommand
from embedded_copilot.services.config import Settings
from embedded_copilot.services.execution import ExecutionStatus
from embedded_copilot.services.runtime import build_analysis_service
from embedded_copilot.supervisor.agent import SupervisorAgent


def test_extension_settings_are_independent_and_disabled_by_default(
    monkeypatch,
) -> None:
    monkeypatch.delenv("EMBEDDED_COPILOT_ENGINEERING_INPUT_ROOT", raising=False)

    settings = EngineeringExtensionSettings.from_environment()

    assert settings.input_root is None


def test_runtime_uses_legacy_supervisor_without_extension_root(monkeypatch) -> None:
    monkeypatch.delenv("EMBEDDED_COPILOT_ENGINEERING_INPUT_ROOT", raising=False)

    service = build_analysis_service(Settings(_env_file=None))

    assert isinstance(service._supervisor, SupervisorAgent)
    assert not isinstance(service._supervisor, EngineeringSupervisorAdapter)
    hardware = service._supervisor._dispatcher.get_agent("HardwareAgent")
    assert isinstance(hardware, HardwareBlueprintProjectionAgentAdapter)
    assert isinstance(hardware._delegate, HardwareAgent)


def test_runtime_composes_transparent_extension_when_root_is_configured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EMBEDDED_COPILOT_ENGINEERING_INPUT_ROOT", str(tmp_path))

    service = build_analysis_service(Settings(_env_file=None))

    assert isinstance(service._supervisor, EngineeringSupervisorAdapter)
    assert isinstance(service._supervisor._delegate, SupervisorAgent)
    hardware = service._supervisor._delegate._dispatcher.get_agent("HardwareAgent")
    assert isinstance(hardware, HardwareBlueprintProjectionAgentAdapter)
    assert isinstance(hardware._delegate, HardwareAgentInputAdapter)
    assert isinstance(hardware._delegate._delegate, HardwareAgent)


def test_runtime_keeps_legacy_composition_when_optional_backend_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EMBEDDED_COPILOT_ENGINEERING_INPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(engineering_config, "real_pdf_backend_available", lambda: False)

    service = build_analysis_service(Settings(_env_file=None))

    assert isinstance(service._supervisor, SupervisorAgent)
    assert not isinstance(service._supervisor, EngineeringSupervisorAdapter)


def test_runtime_extension_produces_firmware_report_without_raw_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "main.c"
    source.write_text(
        """
        static void worker(void *arg) { for (;;) { do_work(); } }
        void app_main(void) { xTaskCreate(worker, "worker", 2048, 0, 5, 0); }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("EMBEDDED_COPILOT_ENGINEERING_INPUT_ROOT", str(tmp_path))
    service = build_analysis_service(Settings(_env_file=None))
    command = AnalysisCommand(
        request="Review ESP32 firmware code.",
        required_agents=("firmware",),
        input_context=UnifiedInputContext(
            attachments=(
                UserAttachment(
                    id="source-1",
                    filename=source.name,
                    media_type=AttachmentType.SOURCE_CODE,
                    content_type="text/x-c",
                    size_bytes=source.stat().st_size,
                    metadata={"category": "source_code", "format": "c"},
                ),
            )
        ),
    )

    async def scenario() -> None:
        await service.start()
        try:
            snapshot = await service.submit(command)
            for _ in range(100):
                current = service.get_status(snapshot.execution_id)
                if current.status in {
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.FAILED,
                }:
                    break
                await asyncio.sleep(0.005)
            assert (
                service.get_status(snapshot.execution_id).status
                is ExecutionStatus.COMPLETED
            )
            report = service.get_report(snapshot.execution_id)
            assert report.firmware_section is not None
            assert any(
                "freertos-task-starvation" in item
                for item in report.firmware_section.structure
            )
            serialized = report.model_dump_json()
            assert "do_work" not in serialized
            assert str(tmp_path) not in serialized
        finally:
            await service.close()

    asyncio.run(scenario())


def test_runtime_extension_crosschecks_real_pdf_and_firmware(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf = tmp_path / "esp32-s3.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(
        fitz.Rect(36, 36, 560, 800),
        """ESP32-S3 Series Datasheet
Espressif Systems
Part Number: ESP32-S3
Package: QFN-56
Family: ESP32-S3
CPU: Xtensa LX7
Operating voltage: 3.0 V to 3.6 V
Active current: 20 mA to 40 mA
Operating temperature: -40 C to 85 C
Memory: 512 KB SRAM, external Flash support
Interfaces: UART, SPI, I2C, USB, DVP camera interface
Pin No. Pin Name Function
12 GPIO8 Embedded Flash
""",
        fontsize=9,
    )
    document.save(pdf)
    document.close()
    source = tmp_path / "camera.c"
    source.write_text(
        """
        void camera_init(void) {
            camera_config_t config = { .pin_d0 = GPIO_NUM_8 };
            gpio_config_t io = { .pin_bit_mask = 1ULL << GPIO_NUM_8 };
            gpio_config(&io);
        }
        void app_main(void) { camera_init(); }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("EMBEDDED_COPILOT_ENGINEERING_INPUT_ROOT", str(tmp_path))
    service = build_analysis_service(Settings(_env_file=None))
    attachments = (
        UserAttachment(
            id="datasheet-1",
            filename=pdf.name,
            media_type=AttachmentType.DOCUMENT,
            content_type="application/pdf",
            size_bytes=pdf.stat().st_size,
            metadata={"category": "document", "format": "pdf"},
        ),
        UserAttachment(
            id="source-1",
            filename=source.name,
            media_type=AttachmentType.SOURCE_CODE,
            content_type="text/x-c",
            size_bytes=source.stat().st_size,
            metadata={"category": "source_code", "format": "c"},
        ),
    )

    async def scenario() -> None:
        await service.start()
        try:
            snapshot = await service.submit(
                AnalysisCommand(
                    request="Cross-check ESP32-S3 Datasheet and Firmware GPIO use.",
                    required_agents=("firmware", "hardware"),
                    input_context=UnifiedInputContext(attachments=attachments),
                )
            )
            for _ in range(100):
                status = service.get_status(snapshot.execution_id).status
                if status in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}:
                    break
                await asyncio.sleep(0.005)
            assert (
                service.get_status(snapshot.execution_id).status
                is ExecutionStatus.COMPLETED
            )
            report = service.get_report(snapshot.execution_id)
            assert report.firmware_section is not None
            assert report.hardware_section is not None
            assert any(
                "[HIGH] datasheet-firmware-gpio-conflict" in item
                for item in report.hardware_section.constraints
            )
            serialized = report.model_dump_json()
            assert "camera_config_t" not in serialized
            assert str(tmp_path) not in serialized
            assert __version__ == "7.1.0"
        finally:
            await service.close()

    asyncio.run(scenario())
