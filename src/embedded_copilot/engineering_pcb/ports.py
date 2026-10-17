from __future__ import annotations

from typing import Protocol

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_optimization_loop import OptimizationProposal
from embedded_copilot.engineering_validation import ValidationFinding

from .models import (
    EngineeringPCBGraph,
    ParsedPCBArtifact,
    PCBAnalysis,
    PCBAnalysisRequest,
    PCBArtifactReference,
    PCBComponent,
    PCBContext,
    PCBDatasheetEvidence,
    PCBRepairHandoff,
    PCBRepairHandoffReceipt,
)


class PCBArtifactSourcePort(Protocol):
    def get_artifact(
        self, project_id: str, artifact_id: str
    ) -> PCBArtifactReference | None: ...


class PCBParserPort(Protocol):
    def parse(self, artifact: PCBArtifactReference) -> ParsedPCBArtifact: ...


class PCBGraphPort(Protocol):
    def build(self, context: PCBContext) -> EngineeringPCBGraph: ...


class PCBAnalysisPort(Protocol):
    def analyze(
        self,
        request: PCBAnalysisRequest,
        context: PCBContext,
        graph: EngineeringPCBGraph,
    ) -> PCBAnalysis: ...


class PCBValidationPort(Protocol):
    def submit(self, analysis: PCBAnalysis) -> tuple[ValidationFinding, ...]: ...


class PCBOptimizationPort(Protocol):
    def submit(
        self, analysis: PCBAnalysis, findings: tuple[ValidationFinding, ...]
    ) -> tuple[OptimizationProposal, ...]: ...


class PCBEngineeringContextSourcePort(Protocol):
    def get_context(
        self, request: EngineeringContextRequest
    ) -> EngineeringContextSnapshot | None: ...


class PCBDatasheetSourcePort(Protocol):
    def get_evidence(
        self, project_id: str, component: PCBComponent
    ) -> tuple[PCBDatasheetEvidence, ...]: ...


class PCBRepairPort(Protocol):
    def record(self, handoff: PCBRepairHandoff) -> PCBRepairHandoffReceipt: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
