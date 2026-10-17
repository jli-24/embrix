from __future__ import annotations

from embedded_copilot.input.models import AttachmentType, UserAttachment
from embedded_copilot.pcb.models import UnifiedPCBModel
from embedded_copilot.pcb_intelligence import PCBInputBundle, PCBReviewService


def _model() -> UnifiedPCBModel:
    return UnifiedPCBModel(
        board_name="demo",
        source_format="kicad_pcb",
        components=(
            {
                "reference": "U1",
                "footprint": "QFN",
                "position": {"x_mm": 1.0, "y_mm": 1.0},
                "layer": "F.Cu",
            },
        ),
        nets=(
            {"name": "SPI_SCK", "net_type": "signal", "nodes": ()},
            {"name": "I2C_SDA", "net_type": "signal", "nodes": ()},
            {"name": "UART_TX", "net_type": "signal", "nodes": ()},
            {"name": "POWER_3V3", "net_type": "power", "nodes": ()},
        ),
        layers=({"name": "F.Cu", "index": 0, "type": "signal"},),
    )


def test_connectivity_detection_and_schematic_projection_only() -> None:
    bundle = PCBInputBundle.create(
        project_id="project-1", pcb_model=_model(), schematic=(), bom=(), netlist=()
    )
    graph, issues, proposal = PCBReviewService().review(bundle)
    assert graph.interfaces == ("SPI", "I2C", "UART", "POWER")
    assert not issues
    assert proposal.project_id == "project-1"
    assert "path" not in type(proposal).model_fields
    assert bundle.pcb_model is not _model()


def test_empty_netlist_generates_issue_without_mutating_model() -> None:
    model = UnifiedPCBModel(board_name="empty", source_format="kicad_pcb")
    bundle = PCBInputBundle.create(project_id="project-1", pcb_model=model)
    _, issues, _ = PCBReviewService().analyze(bundle)
    assert issues[0].category == "CONNECTIVITY"
    assert model.nets == ()


def test_injected_parser_is_called_once_and_result_is_revalidated() -> None:
    class Parser:
        calls = 0

        def parse(self, attachment):
            self.calls += 1
            return _model()

    parser = Parser()
    attachment = UserAttachment(
        id="pcb-1",
        filename="demo.kicad_pcb",
        media_type=AttachmentType.EDA,
        content_type="application/x-kicad-pcb",
        size_bytes=10,
        metadata={"category": "eda", "format": "kicad_pcb"},
    )
    graph, _, _ = PCBReviewService(parser).review_attachment(
        project_id="project-1", attachment=attachment
    )
    assert parser.calls == 1
    assert graph.components == ("U1",)
