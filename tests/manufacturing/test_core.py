from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker import (
    ArtifactStore,
    ArtifactIdentity,
    ExecutionOperationService,
    ExecutionTarget,
    ExecutionWorker,
    KiCadManufacturingOperation,
    OperationType,
    SQLiteExecutionStore,
    TargetBindingStatus,
)
from embedded_copilot.execution_worker.ports import ExecutionContext
from embedded_copilot.execution_worker.registry import (
    ToolDefinition,
    ToolFamily,
    ToolResolution,
)
from embedded_copilot.manufacturing import (
    FabricationGap,
    FabricationMode,
    FabricationModeResolver,
    FabricationModeStatus,
    FabricationReadinessService,
    FabricationStore,
    KiCadManufacturingAdapter,
    ManufacturingCheck,
    ManufacturingCheckStatus,
    ManufacturingExecutionBridge,
    ManufacturingGapCategory,
    ManufacturingPipeline,
    ManufacturingSpecification,
    OutputFile,
    UpstreamGate,
    build_source_revision,
    output_records,
    read_board_facts,
    verify_manufacturing_exports,
    validate_bom,
)

FP = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 9, tzinfo=UTC)
BOARD = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen") (44 "Edge.Cuts" user))
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts"))
  (footprint "Test:Package" (layer "F.Cu") (property "Reference" "R1") (property "Value" "10k"))
)"""


class _AllowEntryGate:
    def assert_current(self, **kwargs):
        return None


class _RejectEntryGate:
    def assert_current(self, **kwargs):
        raise RuntimeError("G8_ENTRY_GATE_REJECTED")


def _write_sources(tmp_path: Path, *, board: str = BOARD) -> tuple[Path, Path, Path, Path]:
    pcb = tmp_path / "main.kicad_pcb"
    bom = tmp_path / "BOM.csv"
    schematic = tmp_path / "main.kicad_sch"
    elf = tmp_path / "main.elf"
    pcb.write_text(board, encoding="utf-8")
    bom.write_text("Reference,Value,Symbol,Description,Source\nR1,10k,R,Resistor,local\n", encoding="utf-8")
    schematic.write_text("schematic", encoding="utf-8")
    elf.write_bytes(b"ELF")
    return pcb, bom, schematic, elf


def _source(tmp_path: Path, *, board: str = BOARD):
    pcb, bom, schematic, elf = _write_sources(tmp_path, board=board)
    return build_source_revision(
        project_id="project-1",
        project_revision="revision-1",
        requirement_revision="requirement-1",
        design_truth_id="design-truth-1",
        design_truth_fingerprint=FP,
        schematic_artifact_id="schematic-1",
        schematic_path=schematic,
        pcb_artifact_id="pcb-1",
        pcb_path=pcb,
        bom_artifact_id="bom-1",
        bom_path=bom,
        firmware_specification_id="firmware-spec-1",
        build_configuration_id="build-config-1",
        elf_artifact_id="elf-1",
        elf_path=elf,
        hw_fw_consistency_result_id="hwfw-1",
        virtual_assembly_id="virtual-assembly-1",
        virtual_assembly_fingerprint=FP,
        simulation_result_id="simulation-1",
        functional_verification_result_id="functional-1",
        functional_verification_fingerprint=FP,
        source_refs=("requirement-1", "pcb-1", "bom-1"),
    ), pcb, bom


def test_mode_is_required_without_explicit_intent_even_when_bom_exists() -> None:
    resolution = FabricationModeResolver().resolve(project_id="project-1", requirement_context={"bom": "R1,C1"})
    assert resolution.status is FabricationModeStatus.REQUIRED
    assert resolution.mode is None


def test_mode_resolver_accepts_only_explicit_token() -> None:
    resolution = FabricationModeResolver().resolve(project_id="project-1", requirement_context={"manufacturing_mode": "PCB_ASSEMBLY"})
    assert resolution.status is FabricationModeStatus.RESOLVED
    assert resolution.mode is FabricationMode.PCB_ASSEMBLY


def test_board_facts_are_read_from_kicad_source(tmp_path: Path) -> None:
    pcb, _, _, _ = _write_sources(tmp_path)
    facts = read_board_facts(pcb)
    assert facts.layer_count == 2
    assert facts.copper_layers == ("F.Cu", "B.Cu")
    assert facts.board_thickness_mm == 1.6
    assert facts.board_dimensions_mm == (20.0, 10.0)
    assert facts.outline_valid
    assert facts.component_refs == ("R1",)


def test_open_edge_cuts_is_rejected(tmp_path: Path) -> None:
    pcb, _, _, _ = _write_sources(tmp_path, board=BOARD.replace("(gr_rect (start 0 0) (end 20 10) (stroke (width 0.05) (type default)) (fill none) (layer \"Edge.Cuts\"))", ""))
    assert not read_board_facts(pcb).outline_valid


def test_missing_mode_is_persisted_as_blocked_not_ready(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    store = FabricationStore(tmp_path / "records")
    record = FabricationReadinessService(store=store).evaluate(
        source=source,
        pcb_path=pcb,
        bom_path=bom,
        persist=True,
        now=NOW,
    )
    assert record.readiness.mode_resolution.status is FabricationModeStatus.REQUIRED
    assert record.readiness.overall_status.value == "BLOCKED"
    assert not record.readiness.fabrication_ready
    restored = FabricationStore(tmp_path / "records").get(project_id="project-1", result_id=record.readiness.result_id)
    assert restored == record


def test_fabrication_store_rejects_changed_record(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    store = FabricationStore(tmp_path / "records")
    record = FabricationReadinessService(store=store).evaluate(source=source, pcb_path=pcb, bom_path=bom, now=NOW)
    with pytest.raises(ValueError, match="fingerprint|immutable"):
        store.save(record.model_copy(update={"evidence": record.evidence.model_copy(update={"tool_observed": True})}))


def test_pcba_bom_requires_explicit_mpn_and_manufacturer(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    result = validate_bom(bom, read_board_facts(pcb), source, FabricationMode.PCB_ASSEMBLY)
    assert isinstance(result[0], FabricationGap)
    assert result[0].category is ManufacturingGapCategory.BOM


def test_changed_head_is_reported_stale_without_mutating_persisted_record(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    record = FabricationReadinessService().evaluate(source=source, pcb_path=pcb, bom_path=bom, now=NOW, persist=False)
    pcb.write_text(BOARD + "\n", encoding="utf-8")
    status, stale = FabricationReadinessService.current_status(
        record, pcb_path=pcb, bom_path=bom, project_revision=source.project_revision, requirement_revision=source.requirement_revision,
    )
    assert status.value == "STALE"
    assert "PCB" in stale


def test_upstream_gate_names_and_l4_staleness_are_required(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    gates = tuple(
        UpstreamGate.create(
            gate_id=f"gate-{name.lower()}", name=name, status="PASS" if name != "L4" else "STALE",
            evidence_id=f"evidence-{name.lower()}", evidence_fingerprint=FP,
            checked_at=NOW,
        )
        for name in ("ERC", "DRC", "BUILD", "HW_FW", "VIRTUAL_ASSEMBLY", "L4")
    )
    record = FabricationReadinessService().evaluate(
        source=source, pcb_path=pcb, bom_path=bom, schematic_path=tmp_path / "main.kicad_sch", elf_path=tmp_path / "main.elf",
        explicit_mode=FabricationMode.PCB_ONLY, upstream_gates=gates, now=NOW, persist=False,
    )
    assert record.readiness.overall_status.value == "STALE"
    assert "gate-l4" in record.readiness.stale_inputs


class _PipelineRunner:
    def run(self, argv, *, cwd, timeout_seconds, environment, cancellation):
        output = Path(cwd) / argv[argv.index("--output") + 1]
        if argv[2:4] == ("export", "gerbers"):
            output.mkdir(parents=True, exist_ok=True)
            geometry = (
                b"%FSLAX46Y46*%\n%MOMM*%\nG01*\nD10*\n"
                b"X00000000Y00000000D02*\nX20000000Y00000000D01*\n"
                b"X20000000Y10000000D01*\nX00000000Y10000000D01*\n"
                b"X00000000Y00000000D01*\nM02*\n"
            )
            files = {
                "F_Cu.gbr": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Copper,L1,Top*%\n" + geometry,
                "B_Cu.gbr": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Copper,L2,Bot*%\n" + geometry,
                "F_Silkscreen.gbr": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Legend,Top*%\n" + geometry,
                "Edge_Cuts.gbr": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Profile,NP*%\n" + geometry,
            }
            for name, content in files.items():
                (output / name).write_bytes(content)
        elif argv[2:4] == ("export", "drill"):
            output.mkdir(parents=True, exist_ok=True)
            (output / "main-PTH.drl").write_bytes(b"M48\n; #@! TF.FileFunction,PTH,Drill*\nM30\n")
        return 0, b"", b""


class _PipelineRegistry:
    def __init__(self, executable: Path):
        self.executable = executable

    def resolve(self, tool_id, *, target_kind, operation):
        kind = {
            OperationType.KICAD_GERBER_EXPORT: ("KICAD_GERBER_EXPORT", "GERBER"),
            OperationType.KICAD_DRILL_EXPORT: ("KICAD_DRILL_EXPORT", "DRILL"),
            OperationType.KICAD_POSITION_EXPORT: ("KICAD_POSITION_EXPORT", "CPL"),
            OperationType.MANUFACTURING_PACKAGE_BUILD: ("MANUFACTURING_PACKAGE_BUILD", "MANIFEST"),
        }[operation]
        definition = ToolDefinition(kind[0], "1" if operation is OperationType.MANUFACTURING_PACKAGE_BUILD else "10.0.5", ToolFamily.KICAD_MANUFACTURING, operation, ("STM32F103C8T6",), () if operation is OperationType.MANUFACTURING_PACKAGE_BUILD else ("kicad-cli.exe",), 180, (kind[1],))
        return ToolResolution(definition, None if operation is OperationType.MANUFACTURING_PACKAGE_BUILD else self.executable, True, "AVAILABLE", "TOOL_RESOLVED")


class _Runner:
    def run(self, argv, *, cwd, timeout_seconds, environment, cancellation):
        output = Path(cwd) / argv[argv.index("--output") + 1]
        if argv[2] == "export" and argv[3] == "gerbers":
            output.mkdir(parents=True, exist_ok=True)
            (output / "main-F_Cu.gbr").write_bytes(b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Copper,L1,Top*%\nG04 data*%\n")
        return 0, b"", b""


def test_kicad_adapter_uses_typed_fixed_argv_and_returns_artifacts(tmp_path: Path) -> None:
    source, pcb, _ = _source(tmp_path)
    target = ExecutionTarget(
        target_id="target-1", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F103C8T6",
        board_family="board-1", mcu_family="STM32F103C8T6", stable_identity_hash="stable-1",
        candidate_id="candidate-1", binding_status=TargetBindingStatus.BOUND, bound_by="operator-1", bound_at=NOW,
    )
    definition = ToolDefinition("KICAD_GERBER_EXPORT", "10.0.5", ToolFamily.KICAD_MANUFACTURING, OperationType.KICAD_GERBER_EXPORT, ("STM32F103C8T6",), ("kicad-cli.exe",), 180, ("GERBER",))
    operation = KiCadManufacturingOperation(
        operation_id="manufacturing-op-1", tenant_id="tenant-1", project_id="project-1", requester_id="engineer-1",
        target_id="target-1", target_fingerprint=target.fingerprint, workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP, proposal_id="proposal-1", proposal_fingerprint=FP, operation_type=OperationType.KICAD_GERBER_EXPORT,
        created_at=NOW, expires_at=NOW + timedelta(minutes=15), nonce="nonce-1", conversation_id="conversation-1",
        tool_id="KICAD_GERBER_EXPORT", tool_version="10.0.5", export_kind="GERBER", project_revision=source.project_revision,
        pcb_artifact_id=source.pcb_artifact_id, pcb_sha256=source.pcb_sha256, manufacturing_specification_id="spec-1",
        manufacturing_specification_fingerprint=FP, source_pcb_relative_path=pcb.name, output_workspace_relative="out/gerbers",
        layer_list=("F.Cu",),
    )
    context = ExecutionContext(
        workspace_root=tmp_path, state_root=tmp_path / ".state", target=target,
        tool=ToolResolution(definition, tmp_path / "kicad-cli.exe", True, "AVAILABLE", "TOOL_RESOLVED"),
        cancellation=object(),
    )
    (tmp_path / "kicad-cli.exe").write_bytes(b"trusted-test-launcher")
    result = KiCadManufacturingAdapter(runner=_Runner()).execute(operation, context)
    assert result.status == "SUCCEEDED"
    assert len(result.additional_artifacts) == 1
    assert result.additional_artifacts[0][0].artifact_type == "GERBER"


def test_manufacturing_pipeline_runs_through_worker_and_reuses_success(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    target = ExecutionTarget(
        target_id="target-1", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F103C8T6",
        board_family="board-1", mcu_family="STM32F103C8T6", stable_identity_hash="stable-1",
        candidate_id="candidate-1", binding_status=TargetBindingStatus.BOUND, bound_by="operator-1", bound_at=NOW,
    )
    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted-test-launcher")
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW, artifact_store=artifact_store)
    worker = ExecutionWorker(
        worker_id="worker-1", store=store, registry=_PipelineRegistry(executable), workspace_resolver=_Workspace(tmp_path),
        targets=_Targets(target), artifact_store=artifact_store, manufacturing=KiCadManufacturingAdapter(runner=_PipelineRunner()),
        allowed_operation_types=(OperationType.KICAD_GERBER_EXPORT, OperationType.KICAD_DRILL_EXPORT, OperationType.MANUFACTURING_PACKAGE_BUILD),
        clock=lambda: NOW,
    )
    specification = ManufacturingSpecification.create(
        specification_id="spec-1", project_id="project-1", project_revision="revision-1", fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2, copper_layers=("F.Cu", "B.Cu"), board_thickness_mm=1.6, copper_weight="1 oz",
        solder_mask="green", silkscreen="white", surface_finish="ENIG", source_refs=("requirement-1",),
        board_size_mm=(20.0, 10.0), requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint, pcb_sha256=source.pcb_sha256, bom_sha256=source.bom_sha256,
    )
    upstream = ManufacturingCheck.create(
        check_id="upstream-1", category=ManufacturingGapCategory.UPSTREAM, status=ManufacturingCheckStatus.PASS, required=True,
        detail="all upstream fabrication gates are current", evidence_refs=("upstream-evidence-1",), source_refs=("requirement-1",),
    )
    pipeline = ManufacturingPipeline(
        operation_service=operations, worker=worker, readiness=FabricationReadinessService(), artifact_store=artifact_store,
        entry_gate=_AllowEntryGate(),
    )
    kwargs = {
        "source": source, "specification": specification, "project_root": tmp_path, "pcb_path": pcb, "bom_path": bom,
        "schematic_path": tmp_path / "main.kicad_sch", "elf_path": tmp_path / "main.elf", "tenant_id": "tenant-1", "requester_id": "engineer-1",
        "target_id": target.target_id, "target_fingerprint": target.fingerprint, "workspace_snapshot_id": "snapshot-1",
        "workspace_fingerprint": FP, "proposal_id": "proposal-1", "proposal_fingerprint": FP, "conversation_id": "conversation-1",
        "output_root_relative": "manufacturing", "upstream_checks": (upstream,), "now": NOW,
    }
    first = pipeline.run(**kwargs)
    second = pipeline.run(**kwargs)
    assert first.record.readiness.overall_status.value == "PASS"
    assert first.record.readiness.fabrication_ready
    assert first.operation_ids == second.operation_ids
    assert second.record == first.record


def test_manufacturing_pipeline_blocks_before_worker_when_parameters_are_missing(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    specification = ManufacturingSpecification.create(
        specification_id="spec-incomplete-pipeline", project_id=source.project_id,
        project_revision=source.project_revision, fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2, copper_layers=("F.Cu", "B.Cu"), source_refs=("requirement-1",),
        requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint,
        pcb_sha256=source.pcb_sha256, bom_sha256=source.bom_sha256,
    )
    execution_store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    pipeline = ManufacturingPipeline(
        operation_service=ExecutionOperationService(store=execution_store, clock=lambda: NOW),
        worker=object(), readiness=FabricationReadinessService(),
        entry_gate=_AllowEntryGate(),
    )

    with pytest.raises(RuntimeError, match="BLOCKED_MANUFACTURING_PARAMETER_REQUIRED"):
        pipeline.run(
            source=source, specification=specification, project_root=tmp_path, pcb_path=pcb, bom_path=bom,
            schematic_path=tmp_path / "main.kicad_sch", elf_path=tmp_path / "main.elf",
            tenant_id="tenant-1", requester_id="engineer-1", target_id="target-1",
            target_fingerprint=FP, workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
            proposal_id="proposal-1", proposal_fingerprint=FP, conversation_id="conversation-1",
            output_root_relative="manufacturing_package", now=NOW,
        )

    assert not (tmp_path / "manufacturing_package").exists()


def test_manufacturing_pipeline_rejects_g8_before_creating_operations(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW)
    pipeline = ManufacturingPipeline(
        operation_service=operations,
        worker=object(),
        readiness=FabricationReadinessService(),
        entry_gate=_RejectEntryGate(),
    )
    specification = ManufacturingSpecification.create(
        specification_id="spec-g8-gate",
        project_id=source.project_id,
        project_revision=source.project_revision,
        fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2,
        copper_layers=("F.Cu", "B.Cu"),
        board_thickness_mm=1.6,
        copper_weight="1 oz",
        solder_mask="green",
        silkscreen="white",
        surface_finish="ENIG",
        source_refs=("requirement-1",),
        board_size_mm=(20.0, 10.0),
        requirement_revision=source.requirement_revision,
        design_truth_fingerprint=source.design_truth_fingerprint,
        pcb_sha256=source.pcb_sha256,
        bom_sha256=source.bom_sha256,
    )

    with pytest.raises(RuntimeError, match="G8_ENTRY_GATE_REJECTED"):
        pipeline.run(
            source=source,
            specification=specification,
            project_root=tmp_path,
            pcb_path=pcb,
            bom_path=bom,
            schematic_path=tmp_path / "main.kicad_sch",
            elf_path=tmp_path / "main.elf",
            tenant_id="tenant-1",
            requester_id="engineer-1",
            target_id="target-1",
            target_fingerprint=FP,
            workspace_snapshot_id="snapshot-1",
            workspace_fingerprint=FP,
            proposal_id="proposal-1",
            proposal_fingerprint=FP,
            conversation_id="conversation-1",
            output_root_relative="manufacturing-package",
            now=NOW,
        )

    assert store.list_operations("tenant-1", source.project_id, limit=20) == ()
    assert not (tmp_path / "manufacturing-package").exists()


def test_explicit_pcb_only_builds_a_baseline_from_read_back_outputs(tmp_path: Path) -> None:
    source, pcb, bom = _source(tmp_path)
    output_root = tmp_path / "out"
    output_root.mkdir()
    headers = {
        "F.Cu": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Copper,L1,Top*%\nG04 data*%\n",
        "B.Cu": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Copper,L2,Bot*%\nG04 data*%\n",
        "F.SilkS": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Legend,Top*%\nG04 data*%\n",
        "Edge.Cuts": b"%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n%TF.FileFunction,Profile,NP*%\nG04 data*%\n",
    }
    files = []
    for layer, content in headers.items():
        path = output_root / f"{layer.replace('.', '_')}.gbr"
        path.write_bytes(content)
        from embedded_copilot.execution_worker import ArtifactIdentity

        files.append(OutputFile(ArtifactIdentity(
            artifact_id=f"gerber-{layer.replace('.', '-')}", tenant_id="tenant-1", project_id="project-1", target_id="target-1",
            workspace_fingerprint=FP, build_operation_id="op-1", toolchain_id="KICAD_GERBER_EXPORT", toolchain_version="10.0.5",
            build_configuration="revision-1", artifact_type="GERBER", artifact_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
            size_bytes=len(content), created_at=NOW,
        ), path.name, content))
    drill_content = b"M48\n; #@! TF.GenerationSoftware,KiCad,Pcbnew,10.0.5\n; #@! TF.FileFunction,PTH,Drill*\nM30\n"
    (output_root / "main-PTH.drl").write_bytes(drill_content)
    files.append(OutputFile(ArtifactIdentity(
        artifact_id="drill-pth", tenant_id="tenant-1", project_id="project-1", target_id="target-1", workspace_fingerprint=FP,
        build_operation_id="op-2", toolchain_id="KICAD_DRILL_EXPORT", toolchain_version="10.0.5", build_configuration="revision-1",
        artifact_type="DRILL", artifact_sha256="sha256:" + hashlib.sha256(drill_content).hexdigest(), size_bytes=len(drill_content), created_at=NOW,
    ), "main-PTH.drl", drill_content))
    outputs = output_records(files, output_root=output_root, source=source)
    upstream = ManufacturingCheck.create(
        check_id="upstream-1", category=ManufacturingGapCategory.UPSTREAM, status=ManufacturingCheckStatus.PASS, required=True,
        detail="ERC DRC build HWFW virtual assembly simulation and L4 are current", evidence_refs=("erc-1", "drc-1", "build-1", "hwfw-1", "virtual-1", "simulation-1", "functional-1"),
        source_refs=("requirement-1",),
    )
    spec = ManufacturingSpecification.create(
        specification_id="spec-1", project_id="project-1", project_revision="revision-1", fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2, copper_layers=("F.Cu", "B.Cu"), board_thickness_mm=1.6, copper_weight="1 oz", solder_mask="green",
        silkscreen="white", surface_finish="ENIG", source_refs=("requirement-1",), board_size_mm=(20.0, 10.0),
        requirement_revision=source.requirement_revision, design_truth_fingerprint=source.design_truth_fingerprint,
        pcb_sha256=source.pcb_sha256, bom_sha256=source.bom_sha256,
    )
    record = FabricationReadinessService().evaluate(
        source=source, pcb_path=pcb, bom_path=bom, schematic_path=tmp_path / "main.kicad_sch", elf_path=tmp_path / "main.elf",
        explicit_mode=FabricationMode.PCB_ONLY, specification=spec,
        upstream_checks=(upstream,), outputs=outputs, kicad_version="10.0.5", now=NOW, persist=False,
    )
    assert record.readiness.overall_status.value == "PASS"
    assert record.readiness.fabrication_ready
    assert record.baseline is not None
    assert record.evidence.lineage_complete


class _Targets:
    def __init__(self, target):
        self.target = target

    def get_bound(self, *, tenant_id, project_id, target_id):
        return self.target

    def revalidate(self, target):
        return target


class _Workspace:
    def __init__(self, root):
        self.root = root

    def resolve(self, *, tenant_id, project_id, snapshot_id, snapshot_fingerprint):
        return self.root


class _Registry:
    def __init__(self, resolution):
        self.resolution = resolution

    def resolve(self, tool_id, *, target_kind, operation):
        assert tool_id == "KICAD_GERBER_EXPORT"
        assert operation is OperationType.KICAD_GERBER_EXPORT
        return self.resolution


def test_manufacturing_bridge_persists_approval_and_runs_worker(tmp_path: Path) -> None:
    source, pcb, _ = _source(tmp_path)
    target = ExecutionTarget(
        target_id="target-1", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F103C8T6",
        board_family="board-1", mcu_family="STM32F103C8T6", stable_identity_hash="stable-1",
        candidate_id="candidate-1", binding_status=TargetBindingStatus.BOUND, bound_by="operator-1", bound_at=NOW,
    )
    definition = ToolDefinition("KICAD_GERBER_EXPORT", "10.0.5", ToolFamily.KICAD_MANUFACTURING, OperationType.KICAD_GERBER_EXPORT, ("STM32F103C8T6",), ("kicad-cli.exe",), 180, ("GERBER",))
    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted-test-launcher")
    resolution = ToolResolution(definition, executable, True, "AVAILABLE", "TOOL_RESOLVED")
    store = __import__("embedded_copilot.execution_worker", fromlist=["SQLiteExecutionStore"]).SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    operations = ExecutionOperationService(store=store, clock=lambda: NOW, artifact_store=artifact_store)
    worker = ExecutionWorker(
        worker_id="worker-1", store=store, registry=_Registry(resolution), workspace_resolver=_Workspace(tmp_path),
        targets=_Targets(target), artifact_store=artifact_store, manufacturing=KiCadManufacturingAdapter(runner=_Runner()),
        allowed_operation_types=(OperationType.KICAD_GERBER_EXPORT,), clock=lambda: NOW,
    )
    spec = ManufacturingSpecification.create(
        specification_id="spec-1", project_id="project-1", project_revision="revision-1", fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2, copper_layers=("F.Cu", "B.Cu"), source_refs=("requirement-1",),
    )
    request = ManufacturingExecutionBridge.build_operation(
        source=source, specification=spec, tenant_id="tenant-1", requester_id="engineer-1", target_id=target.target_id,
        target_fingerprint=target.fingerprint, workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        proposal_id="proposal-1", proposal_fingerprint=FP, conversation_id="conversation-1", export_kind="GERBER",
        source_pcb_relative_path=pcb.name, output_workspace_relative="out/gerbers", layer_list=("F.Cu",),
        operation_id="bridge-operation-1", nonce="bridge-nonce-1", created_at=NOW,
    )
    status = ManufacturingExecutionBridge(operations=operations, worker=worker).submit_approved(
        request, reviewer_id="reviewer-1", approval_nonce="approval-nonce-1", expires_at=NOW + timedelta(minutes=15),
    )
    assert status.status.value == "SUCCEEDED"
    assert operations.get_receipt(tenant_id="tenant-1", project_id="project-1", operation_id="bridge-operation-1") is not None


def test_independent_manufacturing_verification_checks_gerber_geometry_and_drill_semantics(tmp_path: Path) -> None:
    source, pcb, _ = _source(tmp_path)
    board = read_board_facts(pcb)
    coordinate_block = (
        "%FSLAX46Y46*%\n"
        "%MOMM*%\n"
        "G01*\n"
        "D10*\n"
        "X00000000Y00000000D02*\n"
        "X20000000Y00000000D01*\n"
        "X20000000Y10000000D01*\n"
        "X00000000Y10000000D01*\n"
        "X00000000Y00000000D01*\n"
        "M02*\n"
    )
    empty_coordinate_block = "%FSLAX46Y46*%\n%MOMM*%\n"
    functions = {
        "F_Cu.gbr": "Copper,L1,Top",
        "B_Cu.gbr": "Copper,L2,Bot",
        "F_Silkscreen.gbr": "Legend,Top",
        "Edge_Cuts.gbr": "Profile,NP",
    }

    def output(name: str, content: bytes, kind: str) -> OutputFile:
        return OutputFile(
            artifact=ArtifactIdentity(
                artifact_id=f"artifact-{name.replace('.', '-')}",
                tenant_id="tenant-1",
                project_id=source.project_id,
                target_id="target-1",
                workspace_fingerprint=FP,
                build_operation_id=f"operation-{kind.lower()}",
                toolchain_id=f"KICAD_{kind}_EXPORT",
                toolchain_version="10.0.5",
                build_configuration=source.project_revision,
                artifact_type=kind,
                artifact_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                created_at=NOW,
            ),
            relative_path=name,
            content=content,
        )

    gerbers = tuple(
        output(
            name,
            (
                "%TF.GenerationSoftware,KiCad,Pcbnew,10.0.5*%\n"
                f"%TF.FileFunction,{function}*%\n"
                + (empty_coordinate_block if name == "B_Cu.gbr" else coordinate_block)
            ).encode("ascii"),
            "GERBER",
        )
        for name, function in functions.items()
    )
    drill_content = b"M48\n; #@! TF.FileFunction,Plated,1,2,PTH*\nMETRIC\n%\nM30\n"
    drill = (
        output("main-PTH.drl", drill_content, "DRILL"),
        output("main-NPTH.drl", drill_content.replace(b"PTH", b"NPTH"), "DRILL"),
    )

    gerber_verification, drill_verification = verify_manufacturing_exports(
        board=board,
        source=source,
        gerber_files=gerbers,
        drill_files=drill,
    )

    assert gerber_verification.status is ManufacturingCheckStatus.PASS
    assert gerber_verification.pcb_hash_match
    assert drill_verification.status is ManufacturingCheckStatus.PASS
    assert drill_verification.pcb_hash_match
