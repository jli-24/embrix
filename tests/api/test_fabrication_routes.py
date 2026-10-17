from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.fabrication_routes import router
from embedded_copilot.api.main import create_app
from embedded_copilot.manufacturing import (
    FabricationReadinessService,
    FabricationStore,
    build_source_revision,
)
from embedded_copilot.services.config import Settings

BOARD = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 \"F.Cu\" signal) (31 \"B.Cu\" signal) (44 \"Edge.Cuts\" user))
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.05) (type default)) (fill none) (layer \"Edge.Cuts\"))
  (footprint \"Test:Package\" (layer \"F.Cu\") (property \"Reference\" \"R1\") (property \"Value\" \"10k\"))
)"""


class _Catalog:
    def __init__(self, root: Path) -> None:
        self.root = root

    def knowledge_snapshot_root(self, project_id: str, *, tenant_id: str) -> Path:
        if project_id != "project-1" or tenant_id != "tenant-1":
            raise ValueError("project identity mismatch")
        return self.root


def _app(tmp_path: Path) -> FastAPI:
    application = FastAPI()
    application.state.project_catalog_runtime = _Catalog(tmp_path)
    application.state.fabrication_store = FabricationStore(tmp_path / "records")
    application.state.fabrication_readiness_service = FabricationReadinessService(
        store=application.state.fabrication_store
    )
    application.state.fabrication_pipeline = None
    application.include_router(router)
    return application


def _body(tmp_path: Path) -> dict[str, object]:
    pcb = tmp_path / "main.kicad_pcb"
    bom = tmp_path / "BOM.csv"
    schematic = tmp_path / "main.kicad_sch"
    elf = tmp_path / "main.elf"
    pcb.write_text(BOARD, encoding="utf-8")
    bom.write_text("Reference,Value\nR1,10k\n", encoding="utf-8")
    schematic.write_text("schematic", encoding="utf-8")
    elf.write_bytes(b"ELF")
    source = build_source_revision(
        project_id="project-1", project_revision="revision-1", requirement_revision="requirement-1",
        design_truth_id="truth-1", design_truth_fingerprint="sha256:" + "1" * 64,
        schematic_artifact_id="schematic-1", schematic_path=schematic,
        pcb_artifact_id="pcb-1", pcb_path=pcb, bom_artifact_id="bom-1", bom_path=bom,
        firmware_specification_id="firmware-spec-1", build_configuration_id="build-config-1",
        elf_artifact_id="elf-1", elf_path=elf, hw_fw_consistency_result_id="hwfw-1",
        virtual_assembly_id="virtual-assembly-1", virtual_assembly_fingerprint="sha256:" + "2" * 64,
        simulation_result_id="simulation-1", functional_verification_result_id="functional-1",
        functional_verification_fingerprint="sha256:" + "3" * 64,
        source_refs=("requirement-1", "pcb-1", "bom-1"),
    )
    return {
        "project_id": "project-1",
        "source": source.model_dump(mode="json"),
        "pcb_relative_path": pcb.name,
        "bom_relative_path": bom.name,
        "schematic_relative_path": schematic.name,
        "elf_relative_path": elf.name,
    }


HEADERS = {"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-1"}


def test_main_composition_registers_fabrication_api_without_agent_route() -> None:
    paths = set(create_app(settings=Settings(_env_file=None)).openapi()["paths"])
    assert "/api/v1/copilot/fabrication/readiness" in paths
    assert "/api/v1/copilot/fabrication/package" in paths
    assert "/fabrication-agent" not in paths


def test_main_composition_binds_configured_real_kicad_to_one_manufacturing_pipeline(tmp_path: Path) -> None:
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    settings = Settings(
        _env_file=None,
        project_root=project_root,
        desktop_state_database=tmp_path / "desktop.sqlite3",
        desktop_project_metadata_directory=tmp_path / "metadata",
        kicad_cli_path=Path(r"D:\ruanjian\32\kicad\bin\kicad-cli.exe"),
    )

    application = create_app(settings=settings)

    with TestClient(application):
        assert application.state.settings.kicad_cli_path == settings.kicad_cli_path
        assert application.state.fabrication_pipeline is not None


def test_fabrication_api_evaluates_without_inventing_mode_and_exposes_safe_queries(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        evaluated = client.post("/api/v1/copilot/fabrication/readiness", headers=HEADERS, json=_body(tmp_path))
        result_id = evaluated.json()["readiness"]["result_id"]
        baseline = client.get(
            f"/api/v1/copilot/fabrication/{result_id}/baseline?project_id=project-1", headers=HEADERS
        )
        manifest = client.get(
            f"/api/v1/copilot/fabrication/{result_id}/manifest?project_id=project-1", headers=HEADERS
        )

    assert evaluated.status_code == 200
    assert evaluated.json()["readiness"]["mode_resolution"]["status"] == "FABRICATION_MODE_REQUIRED"
    assert evaluated.json()["readiness"]["fabrication_ready"] is False
    assert str(tmp_path) not in evaluated.text
    assert baseline.status_code == 404
    assert manifest.status_code == 404


def test_fabrication_package_api_fails_closed_when_worker_pipeline_is_unavailable(tmp_path: Path) -> None:
    body = _body(tmp_path)
    body.update(
        {
            "specification": {
                "specification_id": "spec-1", "project_id": "project-1", "project_revision": "revision-1",
                "fabrication_mode": "PCB_ONLY", "layer_count": 2, "copper_layers": ["F.Cu", "B.Cu"],
                "source_refs": ["requirement-1"],
            },
            "target_id": "target-1", "target_fingerprint": "sha256:" + "4" * 64,
            "workspace_snapshot_id": "snapshot-1", "workspace_fingerprint": "sha256:" + "5" * 64,
            "proposal_id": "proposal-1", "proposal_fingerprint": "sha256:" + "6" * 64,
            "conversation_id": "conversation-1",
        }
    )
    with TestClient(_app(tmp_path)) as client:
        response = client.post("/api/v1/copilot/fabrication/package", headers=HEADERS, json=body)
    assert response.status_code == 503
    assert response.json()["error"] == "FABRICATION_PACKAGE_UNAVAILABLE"
