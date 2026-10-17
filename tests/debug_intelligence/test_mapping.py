from embedded_copilot.debug_intelligence import HardFaultMappingService


def test_hardfault_mapping_requires_matching_source_fingerprint() -> None:
    result = HardFaultMappingService().map(
        tenant_id="tenant-1",
        project_id="project-1",
        artifact_id="hardfault-1",
        pc="0x08001234",
        map_text="0x08001200 0x00000080 sensor_read firmware/Core/Src/sensor.c:4 sha256:" + "a" * 64,
        source_fingerprints={"firmware/Core/Src/sensor.c": "sha256:" + "b" * 64},
        map_source_reference="artifact://debug/firmware.map",
    )
    assert result.status == "STALE_MAPPING"
    assert not result.evidence


def test_hardfault_mapping_returns_project_bound_source_line() -> None:
    source_fp = "sha256:" + "a" * 64
    result = HardFaultMappingService().map(
        tenant_id="tenant-1",
        project_id="project-1",
        artifact_id="hardfault-1",
        pc="0x08001234",
        map_text="0x08001200 0x00000080 sensor_read firmware/Core/Src/sensor.c:4 " + source_fp,
        source_fingerprints={"firmware/Core/Src/sensor.c": source_fp},
        map_source_reference="artifact://debug/firmware.map",
    )
    assert result.status == "MAPPED"
    assert result.evidence[0].fact_value == "firmware/Core/Src/sensor.c:L4"
    assert result.evidence[0].project_id == "project-1"


def test_hardfault_mapping_rejects_stale_elf_metadata() -> None:
    source_fp = "sha256:" + "a" * 64
    result = HardFaultMappingService().map(
        tenant_id="tenant-1",
        project_id="project-1",
        artifact_id="hardfault-1",
        pc="0x08001234",
        map_text="0x08001200 0x00000080 sensor_read firmware/Core/Src/sensor.c:4 " + source_fp,
        source_fingerprints={"firmware/Core/Src/sensor.c": source_fp},
        map_source_reference="artifact://debug/firmware.map",
        elf_metadata='{"source":"firmware/Core/Src/sensor.c","source_fingerprint":"sha256:' + "b" * 64 + '"}',
    )
    assert result.status == "STALE_MAPPING"
    assert not result.evidence
