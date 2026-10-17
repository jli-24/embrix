from __future__ import annotations

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_copilot import EngineeringRequirement
from embedded_copilot.engineering_copilot.common import validate_copy
from embedded_copilot.model_gateway import ModelPolicy, ModelRequest

from .contracts import DatasheetEvidence, GraphProjection, HardwareProposal


class HardwareAnalysisService:
    def __init__(self, model_gateway=None):
        self._model_gateway = model_gateway

    def analyze(
        self,
        requirement: EngineeringRequirement,
        context: EngineeringContextSnapshot,
        datasheet: tuple[DatasheetEvidence, ...],
        graph: GraphProjection,
    ) -> HardwareProposal:
        req = validate_copy(requirement, EngineeringRequirement)
        ctx = validate_copy(context, EngineeringContextSnapshot)
        graph_copy = validate_copy(graph, GraphProjection)
        if req.project_id != ctx.project_id or graph_copy.project_id != ctx.project_id:
            raise ValueError("project binding rejected")
        if type(datasheet) is not tuple:
            raise TypeError("datasheet evidence must be a tuple")
        ds = tuple(validate_copy(item, DatasheetEvidence) for item in datasheet)
        if any(item.project_id != ctx.project_id for item in ds):
            raise ValueError("evidence project rejected")
        context_refs = {
            item.evidence_id
            for item in (
                *ctx.memory_evidence,
                *ctx.graph_evidence,
                *ctx.knowledge_evidence,
                *ctx.multimodal_evidence,
            )
        }
        supplied_refs = {
            *(item.evidence_id for item in ds),
            *graph_copy.evidence_refs,
            *context_refs,
        }
        if any(reference not in supplied_refs for reference in req.evidence_refs):
            raise ValueError("evidence reference rejected")
        refs = (
            tuple(item.evidence_id for item in ds)
            + tuple(graph_copy.evidence_refs)
            + req.evidence_refs
        )
        if not refs:
            raise ValueError("insufficient evidence")
        rationale = None
        if self._model_gateway is not None:
            try:
                request = ModelRequest(
                    request_id=f"note-{req.requirement_id}",
                    task=req.description,
                    engineering_context=ctx,
                    system_instruction="仅生成非权威硬件说明；结构化事实必须来自证据。",
                    constraints=req.constraints,
                    model_policy=ModelPolicy(
                        primary_provider="local", model_name="offline", temperature=0.0
                    ),
                )
                result = self._model_gateway.generate(request)
                rationale = getattr(result, "content", None)
            except Exception:  # noqa: BLE001 - rationale is optional
                rationale = None
        mcu = ds[0].subject if ds else "待证据确认 MCU"
        return HardwareProposal.create(
            proposal_id=f"hardware-{ctx.project_id}-{ctx.fingerprint[-12:]}",
            project_id=ctx.project_id,
            mcu_recommendation=mcu,
            power_architecture=("按 Datasheet 电源约束设计",),
            interface_design=tuple(sorted(graph_copy.relations)) or ("待接口证据确认",),
            bom_proposal=("仅供人工审核的 BOM 建议",),
            evidence_refs=tuple(sorted(set(refs))),
            confidence=float(ctx.confidence),
            rationale=rationale,
        )


__all__ = ("HardwareAnalysisService",)
