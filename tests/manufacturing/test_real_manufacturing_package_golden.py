from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.execution_worker import (
    ArtifactStore,
    ExecutionOperationService,
    ExecutionTarget,
    ExecutionWorker,
    OperationType,
    SQLiteExecutionStore,
    TargetBindingStatus,
)
from embedded_copilot.execution_worker.adapters import probe_kicad_cli_version
from embedded_copilot.execution_worker.registry import (
    ToolDefinition,
    ToolFamily,
    ToolResolution,
)
from embedded_copilot.manufacturing import (
    FabricationMode,
    FabricationReadinessService,
    KiCadManufacturingAdapter,
    ManufacturingCheck,
    ManufacturingCheckStatus,
    ManufacturingGapCategory,
    ManufacturingOutput,
    ManufacturingOutputType,
    ManufacturingPackageService,
    ManufacturingParameterApprovalState,
    ManufacturingParameterDecision,
    ManufacturingPipeline,
    ManufacturingSpecification,
    build_source_revision,
    render_fabrication_ready,
)
from tests.production_acceptance.kicad_toolchain import discover_test_kicad_cli

NOW = datetime(2026, 9, 9, tzinfo=UTC)


class _AllowEntryGate:
    def assert_current(self, **kwargs):
        return None


BOARD = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 \"F.Cu\" signal) (31 \"B.Cu\" signal) (36 \"B.SilkS\" user \"B.Silkscreen\") (37 \"F.SilkS\" user \"F.Silkscreen\") (44 \"Edge.Cuts\" user))
  (net 0 \"\")
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.05) (type default)) (fill none) (layer \"Edge.Cuts\"))
  (footprint \"Test:Package\" (layer \"F.Cu\") (at 5 5)
    (property \"Reference\" \"R1\" (at 0 0 0) (layer \"F.SilkS\"))
    (property \"Value\" \"10k\" (at 0 1 0) (layer \"F.Fab\"))
    (pad \"1\" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers \"*.Cu\" \"*.Mask\"))
  )
  (setup (pad_to_mask_clearance 0))
)"""


@pytest.mark.real_toolchain
def test_real_kicad_manufacturing_package_golden_requires_trusted_kicad_cli() -> None:
    """The real golden is opt-in and never substitutes a fake exporter."""
    executable = discover_test_kicad_cli()
    if executable is None:
        pytest.skip("KICAD_CLI_UNAVAILABLE: install and trust KiCad CLI for the real golden")
    assert str(executable).lower().endswith(("kicad-cli", "kicad-cli.exe"))


class _Targets:
    def __init__(self, target: ExecutionTarget) -> None:
        self.target = target

    def get_bound(self, *, tenant_id: str, project_id: str, target_id: str) -> ExecutionTarget:
        return self.target

    def revalidate(self, target: ExecutionTarget) -> ExecutionTarget:
        return target


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, **_: object) -> Path:
        return self.root


class _Registry:
    def __init__(self, executable: Path) -> None:
        self.executable = executable

    def resolve(self, tool_id: str, *, target_kind: str, operation: OperationType) -> ToolResolution:
        if operation is OperationType.MANUFACTURING_PACKAGE_BUILD:
            definition = ToolDefinition(
                "MANUFACTURING_PACKAGE_BUILD", "1", ToolFamily.KICAD_MANUFACTURING,
                operation, ("STM32F103C8T6",), (), 120, ("MANIFEST", "MANUFACTURING_PACKAGE"),
            )
            return ToolResolution(definition, None, True, "AVAILABLE", "NON_PROCESS_ADAPTER")
        definition = ToolDefinition(
            tool_id, "10.0.5", ToolFamily.KICAD_MANUFACTURING, operation,
            ("STM32F103C8T6",), ("kicad-cli.exe", "kicad-cli"), 180,
            ("GERBER",) if operation is OperationType.KICAD_GERBER_EXPORT else ("DRILL",),
        )
        return ToolResolution(definition, self.executable, True, "AVAILABLE", "TOOL_RESOLVED")


def test_real_manufacturing_package_golden(tmp_path: Path) -> None:
    executable_value = discover_test_kicad_cli()
    if executable_value is None:
        pytest.skip("real KiCad CLI is not configured; set EMBRIX_KICAD_CLI")
    executable = Path(executable_value).resolve(strict=True)
    root = tmp_path / "project"
    root.mkdir()
    pcb = root / "main.kicad_pcb"
    bom = root / "BOM.csv"
    schematic = root / "main.kicad_sch"
    elf = root / "main.elf"
    pcb.write_text(BOARD, encoding="utf-8")
    bom.write_text("Reference,Value\nR1,10k\n", encoding="utf-8")
    schematic.write_text("schematic", encoding="utf-8")
    elf.write_bytes(b"ELF")
    source = build_source_revision(
        project_id="project-real-manufacturing", project_revision="revision-1", requirement_revision="requirement-1",
        design_truth_id="design-truth-1", design_truth_fingerprint="sha256:" + "1" * 64,
        schematic_artifact_id="schematic-1", schematic_path=schematic, pcb_artifact_id="pcb-1", pcb_path=pcb,
        bom_artifact_id="bom-1", bom_path=bom, firmware_specification_id="firmware-spec-1",
        build_configuration_id="build-config-1", elf_artifact_id="elf-1", elf_path=elf,
        hw_fw_consistency_result_id="hwfw-1", virtual_assembly_id="virtual-assembly-1",
        virtual_assembly_fingerprint="sha256:" + "2" * 64, simulation_result_id="simulation-1",
        functional_verification_result_id="functional-1", functional_verification_fingerprint="sha256:" + "3" * 64,
        source_refs=("requirement-1", "design-truth-1", "pcb-1", "bom-1"),
    )
    version = probe_kicad_cli_version(str(executable), cwd=root)
    if version != "10.0.5":
        pytest.skip(f"trusted KiCad CLI version {version!r} is outside the v1.1 allow-list")
    specification = ManufacturingSpecification.create(
        specification_id="spec-real-manufacturing", project_id=source.project_id,
        project_revision=source.project_revision, fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2, copper_layers=("F.Cu", "B.Cu"), board_size_mm=(20.0, 10.0),
        source_refs=("requirement-1",), requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint, pcb_sha256=source.pcb_sha256, bom_sha256=source.bom_sha256,
    )
    parameter_decisions = tuple(
        ManufacturingParameterDecision.create(
            id=f"user-approved-{parameter}", project_id=source.project_id,
            parameter=parameter, value=value, source="user_approved",
            approval_state=ManufacturingParameterApprovalState.APPROVED, created_at=NOW,
        )
        for parameter, value in (
            ("board_thickness", "1.6mm"), ("surface_finish", "HASL"),
            ("copper_weight", "1oz"), ("solder_mask", "green"), ("silkscreen", "white"),
        )
    )
    target = ExecutionTarget(
        target_id="target-real-manufacturing", tenant_id="tenant-golden", project_id=source.project_id,
        target_kind="STM32F103C8T6", board_family="board-1", mcu_family="STM32F103C8T6",
        stable_identity_hash="stable-1", candidate_id="candidate-1", binding_status=TargetBindingStatus.BOUND,
        bound_by="operator-1", bound_at=NOW,
    )
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW)
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    worker = ExecutionWorker(
        worker_id="manufacturing-real-golden-worker", store=store, registry=_Registry(executable),
        workspace_resolver=_Workspace(root), targets=_Targets(target), artifact_store=artifact_store,
        manufacturing=KiCadManufacturingAdapter(), allowed_operation_types=(
            OperationType.KICAD_GERBER_EXPORT, OperationType.KICAD_DRILL_EXPORT, OperationType.MANUFACTURING_PACKAGE_BUILD,
        ), clock=lambda: NOW,
    )
    upstream = ManufacturingCheck.create(
        check_id="upstream-real", category="UPSTREAM", status="PASS", required=True,
        detail="upstream fabrication evidence is current", evidence_refs=("evidence-upstream",), source_refs=("requirement-1",),
    )
    pipeline = ManufacturingPipeline(
        operation_service=operations, worker=worker, readiness=FabricationReadinessService(), artifact_store=artifact_store,
        entry_gate=_AllowEntryGate(),
    )
    run_kwargs = {
        "source": source, "specification": specification, "project_root": root, "pcb_path": pcb, "bom_path": bom,
        "schematic_path": schematic, "elf_path": elf, "tenant_id": "tenant-golden", "requester_id": "engineer-golden",
        "target_id": target.target_id, "target_fingerprint": target.fingerprint, "workspace_snapshot_id": "snapshot-1",
        "workspace_fingerprint": "sha256:" + "4" * 64, "proposal_id": "proposal-1", "proposal_fingerprint": "sha256:" + "5" * 64,
        "conversation_id": "conversation-1", "output_root_relative": "manufacturing_package", "upstream_checks": (upstream,), "now": NOW,
        "parameter_decisions": parameter_decisions,
    }
    result = pipeline.run(**run_kwargs)
    restarted = pipeline.run(**run_kwargs)
    assert result.record.readiness.fabrication_ready
    assert result.record.specification is not None
    assert result.record.specification.status.value == "COMPLETE"
    assert {item.source for item in result.record.readiness.parameter_decisions} == {"user_approved"}
    assert restarted.record == result.record
    assert restarted.operation_ids == result.operation_ids
    package_operation = store.get_operation(
        "tenant-golden", source.project_id, result.operation_ids[-1]
    )
    assert package_operation is not None
    assert package_operation.output_workspace_relative == "manufacturing_package/package"
    package_root = root / "manufacturing_package"
    assert (package_root / "gerber").is_dir()
    assert (package_root / "drill").is_dir()
    assert (package_root / "pcb_source" / pcb.name).is_file()
    assert not (package_root / ".package-operation").exists()
    manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == source.project_id
    assert manifest["pcb_hash"] == source.pcb_sha256
    assert manifest["design_truth_hash"] == source.design_truth_fingerprint
    assert manifest["manufacturing_spec_hash"] == result.record.specification.fingerprint
    assert manifest["pcb_sha256"] == source.pcb_sha256
    assert manifest["design_truth_sha256"] == source.design_truth_fingerprint
    assert manifest["manufacturing_spec_sha256"] == result.record.specification.fingerprint
    assert manifest["created_at"] == NOW.isoformat()
    assert manifest["generated_at"] == NOW.isoformat()
    assert f"source_revision_fingerprint={source.fingerprint}" in manifest["notes"]
    assert "kicad_tool_id=KICAD_GERBER_EXPORT,KICAD_DRILL_EXPORT" in manifest["notes"]
    assert "kicad_tool_version=10.0.5" in manifest["notes"]
    assert any(item.startswith("generation_configuration=") for item in manifest["notes"])
    evidence = json.loads((package_root / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["project_id"] == source.project_id
    assert evidence["pcb_hash"] == source.pcb_sha256
    assert evidence["design_truth_hash"] == source.design_truth_fingerprint
    assert evidence["manufacturing_spec_hash"] == result.record.specification.fingerprint
    assert evidence["pcb_sha256"] == source.pcb_sha256
    assert evidence["design_truth_sha256"] == source.design_truth_fingerprint
    assert evidence["manufacturing_spec_sha256"] == result.record.specification.fingerprint
    assert evidence["output_exists"] is True
    assert evidence["pcb_hash_match"] is True
    assert evidence["revision_match"] is True
    assert evidence["evidence_created"] is True
    assert evidence["evidence_refs"]
    assert not any(item.output_type.value == "CPL" for item in result.record.manifest.outputs)
    final_report = render_fabrication_ready(result.record)
    assert "manufacturing_specification=APPROVED" in final_report
    assert "gerber=PASS" in final_report
    assert "drill=PASS" in final_report
    assert "FABRICATION_READY=true" in final_report
    assert hashlib.sha256(pcb.read_bytes()).hexdigest() == hashlib.sha256((package_root / "pcb_source" / pcb.name).read_bytes()).hexdigest()
    status, stale = FabricationReadinessService.current_status(
        result.record,
        pcb_path=pcb,
        bom_path=bom,
        project_revision=source.project_revision,
        requirement_revision=source.requirement_revision,
        package_root=package_root,
    )
    assert status.value == "PASS"
    assert stale == ()
    gerber_path = next(
        path for path in package_root.glob("gerber/*")
        if path.name != ".embrix-manufacturing-owner"
    )
    gerber_path.write_bytes(gerber_path.read_bytes() + b"tampered")
    stale_status, stale_inputs = FabricationReadinessService.current_status(
        result.record,
        pcb_path=pcb,
        bom_path=bom,
        project_revision=source.project_revision,
        requirement_revision=source.requirement_revision,
        package_root=package_root,
    )
    assert stale_status.value == "STALE"
    assert "GERBER" in stale_inputs


def test_manufacturing_package_rejects_modified_output_before_materialization(tmp_path: Path) -> None:
    from tests.manufacturing.test_core import _source

    source, pcb, _ = _source(tmp_path)
    expected = b"%TF.FileFunction,Copper,L1,Top*%\n"
    output = ManufacturingOutput.create(
        artifact_id="gerber-artifact",
        relative_path="gerber/F_Cu.gbr",
        output_type=ManufacturingOutputType.GERBER,
        layer="F.Cu",
        sha256="sha256:" + hashlib.sha256(expected).hexdigest(),
        source_revision_fingerprint=source.fingerprint,
        size_bytes=len(expected),
        required=True,
        validation=ManufacturingCheckStatus.PASS,
    )
    specification = ManufacturingSpecification.create(
        specification_id="spec-package-integrity",
        project_id=source.project_id,
        project_revision=source.project_revision,
        fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2,
        copper_layers=("F.Cu", "B.Cu"),
        board_size_mm=(20.0, 10.0),
        board_thickness_mm=1.6,
        copper_weight="1 oz",
        solder_mask="green",
        silkscreen="white",
        surface_finish="ENIG",
        source_refs=("requirement-1",),
        requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint,
        pcb_sha256=source.pcb_sha256,
        bom_sha256=source.bom_sha256,
    )
    package_root = tmp_path / "manufacturing_package"
    (package_root / "gerber").mkdir(parents=True)
    (package_root / "gerber" / "F_Cu.gbr").write_bytes(b"tampered")
    manifest, _ = ManufacturingPackageService().build(
        source=source,
        specification=specification,
        outputs=(output,),
        kicad_version="10.0.5",
        root_relative_path="manufacturing_package",
        now=NOW,
    )

    with pytest.raises(ValueError, match="fingerprint"):
        ManufacturingPackageService().materialize(
            root=package_root,
            pcb_path=pcb,
            source=source,
            specification=specification,
            manifest=manifest,
            evidence_refs=("approved-package-operation",),
            now=NOW,
        )


def test_pcb_only_package_rejects_cpl_output(tmp_path: Path) -> None:
    from tests.manufacturing.test_core import _source

    source, _, _ = _source(tmp_path)
    content = b"Ref,Val,Package,PosX,PosY,Rot,Side\nR1,10k,Package,1,1,0,F.Cu\n"
    output = ManufacturingOutput.create(
        artifact_id="cpl-artifact",
        relative_path="placement.csv",
        output_type=ManufacturingOutputType.CPL,
        sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        source_revision_fingerprint=source.fingerprint,
        size_bytes=len(content),
        required=False,
        validation=ManufacturingCheckStatus.PASS,
    )
    specification = ManufacturingSpecification.create(
        specification_id="spec-pcb-only-cpl",
        project_id=source.project_id,
        project_revision=source.project_revision,
        fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2,
        copper_layers=("F.Cu", "B.Cu"),
        board_size_mm=(20.0, 10.0),
        board_thickness_mm=1.6,
        copper_weight="1 oz",
        solder_mask="green",
        silkscreen="white",
        surface_finish="ENIG",
        source_refs=("requirement-1",),
        requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint,
        pcb_sha256=source.pcb_sha256,
        bom_sha256=source.bom_sha256,
    )

    with pytest.raises(ValueError, match="CPL"):
        ManufacturingPackageService().build(
            source=source,
            specification=specification,
            outputs=(output,),
            kicad_version="10.0.5",
            root_relative_path="manufacturing_package",
            now=NOW,
        )


def test_fingerprint_mismatch_blocks_manufacturing_readiness(tmp_path: Path) -> None:
    from tests.manufacturing.test_core import _source

    source, pcb, bom = _source(tmp_path)
    specification = ManufacturingSpecification.create(
        specification_id="spec-fingerprint-mismatch",
        project_id=source.project_id,
        project_revision=source.project_revision,
        fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2,
        copper_layers=("F.Cu", "B.Cu"),
        board_size_mm=(20.0, 10.0),
        board_thickness_mm=1.6,
        copper_weight="1 oz",
        solder_mask="green",
        silkscreen="white",
        surface_finish="ENIG",
        source_refs=("requirement-1",),
        requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint,
        pcb_sha256="sha256:" + "f" * 64,
        bom_sha256=source.bom_sha256,
    )
    record = FabricationReadinessService().evaluate(
        source=source,
        pcb_path=pcb,
        bom_path=bom,
        explicit_mode=FabricationMode.PCB_ONLY,
        mode_approval_ref="mode-approval-1",
        mode_approved_by="engineer-1",
        specification=specification,
        now=NOW,
        persist=False,
    )

    assert not record.readiness.fabrication_ready
    assert any(
        gap.category is ManufacturingGapCategory.REVISION
        for gap in record.readiness.blocking_gaps
    )


def test_package_build_rejects_unspecified_manufacturing_parameters(tmp_path: Path) -> None:
    from tests.manufacturing.test_core import _source

    source, _, _ = _source(tmp_path)
    specification = ManufacturingSpecification.create(
        specification_id="spec-incomplete-package",
        project_id=source.project_id,
        project_revision=source.project_revision,
        fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2,
        copper_layers=("F.Cu", "B.Cu"),
        source_refs=("requirement-1",),
    )

    with pytest.raises(ValueError, match="approved complete specification"):
        ManufacturingPackageService().build(
            source=source,
            specification=specification,
            outputs=(),
            kicad_version="10.0.5",
            root_relative_path="manufacturing_package",
            now=NOW,
        )
