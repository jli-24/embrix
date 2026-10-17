from __future__ import annotations

from embedded_copilot.engineering_pcb import (
    PCBArtifactType,
    PCBDatasheetConstraint,
    PCBDatasheetEvidence,
    PCBEvidenceClaim,
    PCBEvidenceReference,
    PCBEvidenceSource,
    PCBRepairHandoff,
    deterministic_id,
)
from embedded_copilot.engineering_pcb.adapters import (
    FakePCBArtifactSourceAdapter,
    FakePCBDatasheetSourceAdapter,
    FakePCBRepairAdapter,
    KiCadBoardParserAdapter,
)
from embedded_copilot.input.models import AttachmentType, UserAttachment
from embedded_copilot.pcb.models import (
    PCBComponent as LegacyPCBComponent,
)
from embedded_copilot.pcb.models import (
    PCBNet as LegacyPCBNet,
)
from embedded_copilot.pcb.models import (
    PCBNetNode,
    PCBPosition,
    UnifiedPCBModel,
)
from embedded_copilot.pcb.models import (
    PCBNetType as LegacyPCBNetType,
)
from embedded_copilot.pcb.models import (
    PCBPin as LegacyPCBPin,
)

from .conftest import BOARD_CONTENT, NOW, PROJECT, make_artifact


class LegacyParserStub:
    def __init__(self, model: UnifiedPCBModel) -> None:
        self.model = model
        self.calls = 0

    def parse(self, attachment: UserAttachment) -> UnifiedPCBModel:
        self.calls += 1
        return self.model.model_copy(deep=True)


def test_kicad_board_adapter_converts_existing_parser_output():
    artifact = make_artifact(PCBArtifactType.BOARD, BOARD_CONTENT)
    model = UnifiedPCBModel(
        board_name="main",
        source_format="kicad_pcb",
        components=(
            LegacyPCBComponent(
                reference="U1",
                value="MCU",
                footprint="QFN",
                position=PCBPosition(x_mm=1.0, y_mm=2.0),
                layer="F.Cu",
                pins=(
                    LegacyPCBPin(number="1", pad_type="smd", net_name="VCC"),
                    LegacyPCBPin(number="2", pad_type="smd", net_name="GND"),
                ),
            ),
        ),
        nets=(
            LegacyPCBNet(
                name="VCC",
                net_type=LegacyPCBNetType.POWER,
                nodes=(PCBNetNode(reference="U1", pin="1"),),
            ),
            LegacyPCBNet(
                name="GND",
                net_type=LegacyPCBNetType.GROUND,
                nodes=(PCBNetNode(reference="U1", pin="2"),),
            ),
        ),
        metadata={"category": "eda", "format": "kicad_pcb"},
    )
    parser = LegacyParserStub(model)
    attachment = UserAttachment(
        id="board",
        filename="main.kicad_pcb",
        media_type=AttachmentType.EDA,
        content_type="application/x-kicad-pcb",
        size_bytes=len(BOARD_CONTENT),
        metadata={"category": "eda", "format": "kicad_pcb"},
    )
    result = KiCadBoardParserAdapter(parser, {"board": attachment}).parse(artifact)
    assert parser.calls == 1
    assert result.artifact == artifact
    assert {(item.pin_number, item.net_id) for item in result.connections} == {
        ("1", "VCC"),
        ("2", "GND"),
    }


def test_fake_artifact_source_returns_revalidated_copy(artifacts):
    source = FakePCBArtifactSourceAdapter(artifacts)
    first = source.get_artifact(PROJECT, "board")
    second = source.get_artifact(PROJECT, "board")
    assert first == second
    assert first is not second


def test_datasheet_fake_returns_only_explicit_cited_evidence(
    artifacts, parsed_artifacts
):
    artifact = artifacts[0]
    evidence = PCBEvidenceReference.create(
        evidence_id="datasheet-evidence",
        project_id=PROJECT,
        source_type=PCBEvidenceSource.DATASHEET,
        source_id="mcu-datasheet",
        source_fingerprint=artifact.content_fingerprint,
        claim=PCBEvidenceClaim.CITED,
        locator="knowledge://datasheet/mcu-1",
    )
    constraint = PCBDatasheetConstraint.create(
        constraint_id="voltage-limit",
        name="Supply voltage maximum",
        value="3.6",
        unit="V",
        evidence_refs=(evidence,),
    )
    datasheet = PCBDatasheetEvidence.create(
        project_id=PROJECT,
        component_reference="U1",
        component_part_number="MCU-1",
        constraints=(constraint,),
        evidence_refs=(evidence,),
    )
    source = FakePCBDatasheetSourceAdapter({(PROJECT, "U1"): (datasheet,)})
    values = source.get_evidence(PROJECT, parsed_artifacts["board"].components[0])
    assert values == (datasheet,)
    assert values[0] is not datasheet


def test_fake_repair_adapter_is_idempotent():
    handoff = PCBRepairHandoff.create(
        handoff_id="pcb-handoff",
        project_id=PROJECT,
        pcb_proposal_fingerprint="sha256:" + "1" * 64,
        optimization_proposal_id="proposal-" + "2" * 48,
        optimization_proposal_fingerprint="sha256:" + "3" * 64,
        review_id="review",
        review_record_fingerprint="sha256:" + "4" * 64,
        artifact_fingerprints=("sha256:" + "5" * 64,),
        recorded_at=NOW,
    )
    adapter = FakePCBRepairAdapter()
    assert adapter.record(handoff) == adapter.record(handoff)


def test_deterministic_id_has_stable_identity():
    assert deterministic_id("pcb", "a", "b") == deterministic_id("pcb", "a", "b")
