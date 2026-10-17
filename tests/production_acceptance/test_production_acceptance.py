from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.production_acceptance import (
    AcceptanceCapability,
    AcceptanceError,
    AcceptanceFinalState,
    AcceptanceLineageEvent,
    AcceptanceLineagePhase,
    AcceptanceManifest,
    ApprovalArtifact,
    BuildReceipt,
    PreflightStatus,
    create_acceptance_run,
    preflight,
    promote_build_artifact,
    require_approval,
    validate_lineage,
    verify_assets,
    write_evidence_bundle,
)


def _fingerprint(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _manifest(root: Path) -> AcceptanceManifest:
    files = {
        "firmware/CMakeLists.txt": b"project(sensor_gateway)\n",
        "hardware/sensor-gateway.kicad_pro": b"{}\n",
        "hardware/sensor-gateway.kicad_sch": b"(kicad_sch)\n",
        "hardware/sensor-gateway.kicad_pcb": b"(kicad_pcb)\n",
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return AcceptanceManifest.model_validate(
        {
            "schema_version": 1,
            "golden_project": "ESP32-S3 Sensor Gateway",
            "project_id": "esp32-s3-sensor-gateway",
            "workspace_id": "workspace-esp32s3-golden",
            "target_id": "esp32-s3",
            "firmware_project": {
                "asset_id": "firmware-project",
                "relative_path": "firmware/CMakeLists.txt",
                "fingerprint": _fingerprint(files["firmware/CMakeLists.txt"]),
            },
            "firmware_artifact_relative_path": "firmware/build/gateway.bin",
            "kicad_project": {
                "asset_id": "kicad-project",
                "relative_path": "hardware/sensor-gateway.kicad_pro",
                "fingerprint": _fingerprint(files["hardware/sensor-gateway.kicad_pro"]),
            },
            "kicad_schematic": {
                "asset_id": "kicad-schematic",
                "relative_path": "hardware/sensor-gateway.kicad_sch",
                "fingerprint": _fingerprint(files["hardware/sensor-gateway.kicad_sch"]),
            },
            "kicad_board": {
                "asset_id": "kicad-board",
                "relative_path": "hardware/sensor-gateway.kicad_pcb",
                "fingerprint": _fingerprint(files["hardware/sensor-gateway.kicad_pcb"]),
            },
            "toolchain": {
                "python_version": "3.11",
                "esp_idf_version": "v5.3",
                "kicad_version": "10.",
                "pcbnew_version": "10.",
                "glm_provider_id": "glm",
                "glm_text_profile_id": "operator-approved-text",
                "glm_vision_profile_id": "operator-approved-vision",
            },
            "hardware": {
                "board_model": "ESP32-S3-DevKit",
                "board_revision": "rev-a",
                "usb_vid_pid": "303A:1001",
                "uart_port": "COM7",
                "uart_framing": "8N1",
                "i2c_sensor_model": "SHT4X",
                "i2c_sensor_address": "0x44",
                "oled_model": "SSD1306",
                "oled_address": "0x3c",
                "gpio_fixture_id": "gpio-fixture-1",
                "power_fixture_id": "power-fixture-1",
            },
            "operator_id": "operator-1",
            "approver_id": "reviewer-1",
        }
    )


def _run(manifest: AcceptanceManifest):
    return create_acceptance_run(
        manifest=manifest,
        run_id="RUN-ESP32S3-0001",
        started_at="2026-08-10T00:00:00Z",
    )


def _approval(
    manifest: AcceptanceManifest,
    capability: AcceptanceCapability,
    *,
    artifact_fingerprint: str | None = None,
    expires_at: str = "2026-08-10T01:00:00Z",
) -> ApprovalArtifact:
    return ApprovalArtifact(
        approval_id=f"approval-{capability.value.lower()}",
        acceptance_run_id="RUN-ESP32S3-0001",
        project_id=manifest.project_id,
        workspace_id=manifest.workspace_id,
        target_id=manifest.target_id,
        capability=capability,
        proposal_fingerprint="sha256:" + "a" * 64,
        artifact_fingerprint=artifact_fingerprint,
        reviewer_id=manifest.approver_id,
        issued_at="2026-08-10T00:00:00Z",
        expires_at=expires_at,
    )


class _Probe:
    def version(self, tool_id: str) -> str | None:
        return {
            "python": "Python 3.11.9",
            "esp_idf": "ESP-IDF v5.3.1",
            "kicad": "KiCad 10.0.2",
        }.get(tool_id)

    def module_version(self, module_name: str) -> str | None:
        return "10.0.2" if module_name == "pcbnew" else None

    def device_identity(self, uart_port: str):
        assert uart_port == "COM7"
        return {
            "usb_vid_pid": "303A:1001",
            "uart_port": "COM7",
            "board_model": "ESP32-S3-DevKit",
            "board_revision": "rev-a",
        }


def test_manifest_is_strict_and_binds_only_the_golden_project(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    assert manifest.fingerprint.startswith("sha256:")

    invalid = manifest.model_dump(mode="json")
    invalid["extra"] = "not allowed"
    with pytest.raises(ValueError):
        AcceptanceManifest.model_validate(invalid)

    invalid = manifest.model_dump(mode="json")
    invalid["target_id"] = "esp32"
    with pytest.raises(ValueError, match="ESP32-S3"):
        AcceptanceManifest.model_validate(invalid)


def test_preflight_uses_only_bound_assets_and_marks_missing_tools_unavailable(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    (tmp_path / "unregistered-private-file.txt").write_text(
        "not scanned", encoding="utf-8"
    )

    assert verify_assets(manifest=manifest, workspace_root=tmp_path) == (
        PreflightStatus.AVAILABLE,
        PreflightStatus.AVAILABLE,
        PreflightStatus.AVAILABLE,
        PreflightStatus.AVAILABLE,
    )
    checks = preflight(manifest=manifest, workspace_root=tmp_path, probe=_Probe())
    assert checks["python"] is PreflightStatus.AVAILABLE
    assert checks["device"] is PreflightStatus.AVAILABLE
    assert checks["glm"] is PreflightStatus.UNVERIFIED

    (tmp_path / "hardware" / "sensor-gateway.kicad_pcb").write_text(
        "changed", encoding="utf-8"
    )
    assert (
        verify_assets(manifest=manifest, workspace_root=tmp_path)[3]
        is PreflightStatus.MISMATCH
    )


def test_approval_rejects_expiry_and_flash_fingerprint_mismatch(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    run = _run(manifest)
    require_approval(
        approval=_approval(manifest, AcceptanceCapability.BUILD),
        run=run,
        manifest=manifest,
        capability=AcceptanceCapability.BUILD,
        proposal_fingerprint="sha256:" + "a" * 64,
        now="2026-08-10T00:30:00Z",
    )
    with pytest.raises(AcceptanceError, match="approval rejected"):
        require_approval(
            approval=_approval(
                manifest,
                AcceptanceCapability.BUILD,
                expires_at="2026-08-10T00:10:00Z",
            ),
            run=run,
            manifest=manifest,
            capability=AcceptanceCapability.BUILD,
            proposal_fingerprint="sha256:" + "a" * 64,
            now="2026-08-10T00:30:00Z",
        )

    artifact = _promoted_artifact(manifest, run, tmp_path)
    with pytest.raises(AcceptanceError, match="flash artifact"):
        require_approval(
            approval=_approval(
                manifest,
                AcceptanceCapability.FLASH,
                artifact_fingerprint="sha256:" + "b" * 64,
            ),
            run=run,
            manifest=manifest,
            capability=AcceptanceCapability.FLASH,
            proposal_fingerprint="sha256:" + "a" * 64,
            now="2026-08-10T00:30:00Z",
            artifact=artifact,
        )


def test_artifact_promotion_requires_success_and_binds_flashable_fingerprint(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    run = _run(manifest)
    artifact = _promoted_artifact(manifest, run, tmp_path)
    assert artifact.project_id == manifest.project_id
    assert artifact.acceptance_run_id == run.run_id

    receipt = _receipt(manifest, run, status="FAILED")
    with pytest.raises(AcceptanceError, match="build receipt"):
        promote_build_artifact(
            run=run,
            manifest=manifest,
            receipt=receipt,
            workspace_root=tmp_path,
        )


def test_evidence_is_redacted_idempotent_and_uses_only_final_acceptance_states(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path / "workspace")
    run = _run(manifest)
    names = (
        "project_reference.json",
        "workspace_timeline.json",
        "build_receipt.json",
        "flash_receipt.json",
        "hil_observation.json",
        "optimization_review.json",
        "final_environment_snapshot.json",
    )
    projections = {
        name: {"summary": "safe", "raw_uart_log": "C:\\private\\boot token=abc"}
        for name in names
    }
    written = write_evidence_bundle(
        evidence_root=tmp_path / "evidence",
        run=run,
        final_state=AcceptanceFinalState.UNVERIFIED,
        projections=projections,
    )
    assert len(written) == 7
    first = written[0].read_text(encoding="utf-8")
    assert "private" not in first.casefold()
    assert "token" not in first.casefold()
    assert "[REDACTED]" in first
    assert (
        write_evidence_bundle(
            evidence_root=tmp_path / "evidence",
            run=run,
            final_state=AcceptanceFinalState.UNVERIFIED,
            projections=projections,
        )
        == written
    )

    changed = dict(projections)
    changed[names[0]] = {"summary": "different"}
    with pytest.raises(AcceptanceError, match="evidence run conflict"):
        write_evidence_bundle(
            evidence_root=tmp_path / "evidence",
            run=run,
            final_state=AcceptanceFinalState.UNVERIFIED,
            projections=changed,
        )

    with pytest.raises(ValueError):
        AcceptanceFinalState("PASS")


def test_lineage_requires_initial_analysis_chain_and_known_parent_fingerprints(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    run = _run(manifest)
    phases = (
        AcceptanceLineagePhase.REQUIREMENT,
        AcceptanceLineagePhase.KNOWLEDGE_ANALYSIS,
        AcceptanceLineagePhase.FIRMWARE_PROPOSAL,
        AcceptanceLineagePhase.EDA_VALIDATION,
        AcceptanceLineagePhase.OPTIMIZATION_PROPOSAL,
    )
    events = tuple(
        AcceptanceLineageEvent(
            event_id=f"event-{index}",
            acceptance_run_id=run.run_id,
            project_id=run.project_id,
            phase=phase,
            event_fingerprint="sha256:" + f"{index + 1:x}" * 64,
            parent_fingerprints=() if index == 0 else ("sha256:" + f"{index:x}" * 64,),
            recorded_at=f"2026-08-10T00:0{index}:00Z",
        )
        for index, phase in enumerate(phases)
    )
    assert validate_lineage(run=run, events=events) == events

    broken = list(events)
    broken[1] = broken[1].model_copy(
        update={"parent_fingerprints": ("sha256:" + "f" * 64,)}
    )
    with pytest.raises(AcceptanceError, match="lineage is rejected"):
        validate_lineage(run=run, events=tuple(broken))


def _receipt(
    manifest: AcceptanceManifest,
    run,
    *,
    status: str,
    artifact_fingerprint: str | None = None,
) -> BuildReceipt:
    return BuildReceipt(
        receipt_id="build-receipt-1",
        acceptance_run_id=run.run_id,
        project_id=manifest.project_id,
        workspace_id=manifest.workspace_id,
        target_id=manifest.target_id,
        proposal_fingerprint="sha256:" + "a" * 64,
        status=status,
        artifact_fingerprint=artifact_fingerprint,
        completed_at="2026-08-10T00:10:00Z",
        provenance_fingerprint="sha256:" + "d" * 64,
        receipt_fingerprint="sha256:" + "c" * 64,
    )


def _promoted_artifact(manifest: AcceptanceManifest, run, root: Path):
    target = root / "firmware" / "build" / "gateway.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = b"firmware-binary"
    target.write_bytes(content)
    return promote_build_artifact(
        run=run,
        manifest=manifest,
        receipt=_receipt(
            manifest,
            run,
            status="SUCCEEDED",
            artifact_fingerprint=_fingerprint(content),
        ),
        workspace_root=root,
    )
