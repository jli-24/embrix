from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator

from embedded_copilot.model_gateway.governance import EngineeringStage

from .common import (
    Fingerprinted,
    canonical,
    confidence,
    finalize,
    fp,
    ident,
    text,
    tuple_values,
)


def _plan_sequence(value: object, field: str) -> tuple:
    if type(value) is list:
        value = tuple(
            tuple(item) if type(item) is list else item
            for item in value
        )
    return tuple_values(value, field)


class EngineeringRequirement(Fingerprinted):
    requirement_id: str
    project_id: str
    description: str
    constraints: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("requirement_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v):
        return text(v, "description")

    @field_validator("constraints", "evidence_refs", mode="before")
    @classmethod
    def _tuples(cls, v, info):
        return tuple(
            text(x, info.field_name.removesuffix("s"), 512)
            for x in tuple_values(v, info.field_name)
        )

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringTask(Fingerprinted):
    task_id: str
    sequence: int = 1
    title: str
    description: str
    depends_on: tuple[str, ...] = ()
    risk: str
    evidence_refs: tuple[str, ...] = ()

    @field_validator("task_id", mode="before")
    @classmethod
    def _id(cls, v):
        return ident(v, "task_id")

    @field_validator("sequence", mode="before")
    @classmethod
    def _sequence(cls, value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("sequence is invalid")
        return value

    @field_validator("title", "description", "risk", mode="before")
    @classmethod
    def _text(cls, v, info):
        return text(v, info.field_name)

    @field_validator("depends_on", "evidence_refs", mode="before")
    @classmethod
    def _tuples(cls, v, info):
        return tuple(
            text(x, info.field_name, 256) for x in _plan_sequence(v, info.field_name)
        )

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringPlanNote(Fingerprinted):
    note: str
    source: str = "MODEL"

    @field_validator("note", "source", mode="before")
    @classmethod
    def _text(cls, v, info):
        return text(v, info.field_name, 4096)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    PROPOSED = "PROPOSED"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class PlanPhaseStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class PlanTaskStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class PlanTaskType(StrEnum):
    ANALYSIS = "ANALYSIS"
    DESIGN = "DESIGN"
    IMPLEMENTATION = "IMPLEMENTATION"
    SIMULATION = "SIMULATION"
    VERIFICATION = "VERIFICATION"
    REVIEW = "REVIEW"


class PlanRiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PlanReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PlanProvenance(Fingerprinted):
    project_id: str
    source_type: str
    source_id: str
    source_fingerprint: str

    @field_validator("project_id", "source_type", "source_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value):
        from .common import fp

        return fp(value, "source_fingerprint")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PlanRisk(Fingerprinted):
    id: str = Field(validation_alias=AliasChoices("id", "risk_id"))
    plan_id: str
    project_id: str
    category: str
    description: str
    impact: PlanRiskLevel
    probability: PlanRiskLevel
    mitigation: str
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("id", "plan_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("category", "description", "mitigation", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @property
    def risk_id(self) -> str:
        """Compatibility alias retained for internal plan callers."""

        return self.id

    @classmethod
    def create(cls, **values):
        if "id" not in values and "risk_id" in values:
            values["id"] = values.pop("risk_id")
        return finalize(cls, values)


class PlanOutput(Fingerprinted):
    output_id: str
    phase_id: str
    plan_id: str
    project_id: str
    name: str
    output_type: str
    status: PlanTaskStatus = PlanTaskStatus.PENDING
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("output_id", "phase_id", "plan_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", "output_type", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 512)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PlanVerificationStrategy(Fingerprinted):
    strategy_id: str
    plan_id: str
    project_id: str
    criterion_ids: tuple[str, ...] = ()
    method: str
    evidence_required: tuple[str, ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("strategy_id", "plan_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("criterion_ids", "evidence_required", mode="before")
    @classmethod
    def _refs(cls, value, info):
        refs = tuple(
            ident(item, info.field_name.removesuffix("s"))
            for item in _plan_sequence(value, info.field_name)
        )
        if len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} must be unique")
        return refs

    @field_validator("method", mode="before")
    @classmethod
    def _method(cls, value):
        return text(value, "method", 1024)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PlanEvidenceBinding(Fingerprinted):
    binding_id: str
    plan_id: str
    phase_id: str
    project_id: str
    source_type: str
    source_id: str
    source_fingerprint: str
    relationship: str
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator(
        "binding_id", "plan_id", "phase_id", "project_id", "source_type", "source_id", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value):
        from .common import fp

        return fp(value, "source_fingerprint")

    @field_validator("relationship", mode="before")
    @classmethod
    def _relationship(cls, value):
        return text(value, "relationship", 512)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringPlanTask(Fingerprinted):
    task_id: str
    phase_id: str
    plan_id: str
    project_id: str
    title: str
    type: PlanTaskType
    status: PlanTaskStatus = PlanTaskStatus.PENDING
    dependencies: tuple[str, ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("task_id", "phase_id", "plan_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return text(value, "title", 512)

    @field_validator("dependencies", mode="before")
    @classmethod
    def _dependencies(cls, value):
        dependencies = tuple(
            ident(item, "dependency")
            for item in _plan_sequence(value, "dependencies")
        )
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("plan task dependencies must be unique")
        return dependencies

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @model_validator(mode="after")
    def _no_self_dependency(self):
        if self.task_id in self.dependencies:
            raise ValueError("plan task cannot depend on itself")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringPlanPhase(Fingerprinted):
    phase_id: str
    plan_id: str
    project_id: str
    name: str
    description: str
    status: PlanPhaseStatus = PlanPhaseStatus.PENDING
    dependencies: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    tasks: tuple[EngineeringPlanTask, ...] = ()
    outputs: tuple[PlanOutput, ...] = ()
    verification: tuple[PlanVerificationStrategy, ...] = ()
    evidence_bindings: tuple[PlanEvidenceBinding, ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("phase_id", "plan_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("dependencies", "inputs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        refs = tuple(
            ident(item, info.field_name.removesuffix("s"))
            for item in _plan_sequence(value, info.field_name)
        )
        if len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} must be unique")
        return refs

    @field_validator("tasks", "outputs", "verification", "evidence_bindings", "provenance", mode="before")
    @classmethod
    def _tuples(cls, value, info):
        return _plan_sequence(value, info.field_name)

    @model_validator(mode="after")
    def _bound(self):
        task_ids = tuple(task.task_id for task in self.tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("phase task ids must be unique")
        if self.phase_id in self.dependencies:
            raise ValueError("phase cannot depend on itself")
        for task in self.tasks:
            if task.phase_id != self.phase_id or task.plan_id != self.plan_id or task.project_id != self.project_id:
                raise ValueError("plan task identity mismatch")
            if any(dependency not in set(task_ids) for dependency in task.dependencies):
                raise ValueError("phase task dependency is invalid")
        for output in self.outputs:
            if output.phase_id != self.phase_id or output.plan_id != self.plan_id or output.project_id != self.project_id:
                raise ValueError("plan output identity mismatch")
            if any(item.project_id != self.project_id for item in output.provenance):
                raise ValueError("plan output provenance identity mismatch")
        for verification in self.verification:
            if verification.plan_id != self.plan_id or verification.project_id != self.project_id:
                raise ValueError("plan verification identity mismatch")
            if any(item.project_id != self.project_id for item in verification.provenance):
                raise ValueError("plan verification provenance identity mismatch")
        for binding in self.evidence_bindings:
            if binding.phase_id != self.phase_id or binding.plan_id != self.plan_id or binding.project_id != self.project_id:
                raise ValueError("plan evidence identity mismatch")
            if any(item.project_id != self.project_id for item in binding.provenance):
                raise ValueError("plan evidence provenance identity mismatch")
        for provenance in self.provenance:
            if provenance.project_id != self.project_id:
                raise ValueError("plan provenance identity mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PlanReviewDecision(Fingerprinted):
    plan_id: str
    project_id: str
    proposal_fingerprint: str
    status: PlanReviewStatus
    reviewer: str
    decided_at: datetime

    @field_validator("plan_id", "project_id", "reviewer", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fingerprint(cls, value):
        from .common import fp

        return fp(value, "proposal_fingerprint")

    @field_validator("decided_at", mode="before")
    @classmethod
    def _decided_at(cls, value):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("decided_at is invalid") from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("decided_at is invalid")
        return value.astimezone(UTC)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PlanModelOperationBinding(Fingerprinted):
    phase_id: str
    plan_task_id: str
    operation_id: Literal["HARDWARE_SELECTION_PROPOSAL", "SCHEMATIC_DESIGN_PROPOSAL"]
    stage_id: EngineeringStage

    @field_validator("phase_id", "plan_task_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @model_validator(mode="after")
    def _allowed_stage(self):
        allowed = {
            "HARDWARE_SELECTION_PROPOSAL": EngineeringStage.HARDWARE_SELECTION,
            "SCHEMATIC_DESIGN_PROPOSAL": EngineeringStage.SCHEMATIC_DESIGN,
        }
        if self.stage_id != allowed[self.operation_id]:
            raise ValueError("plan model operation stage mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringPlan(Fingerprinted):
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset({"notes"})
    plan_id: str
    requirement_id: str
    project_id: str
    context_fingerprint: str
    tasks: tuple[EngineeringTask, ...]
    title: str = "工程计划"
    objective: str = "依据已确认需求生成只读工程计划"
    status: PlanStatus = PlanStatus.PROPOSED
    phases: tuple[EngineeringPlanPhase, ...] = ()
    plan_risks: tuple[PlanRisk, ...] = ()
    verification_strategy: tuple[PlanVerificationStrategy, ...] = ()
    evidence_bindings: tuple[PlanEvidenceBinding, ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()
    risks: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    notes: tuple[EngineeringPlanNote, ...] = ()
    confidence: float
    model_operation_schema_version: Literal[1, 2] = 1
    model_operation_bindings: tuple[PlanModelOperationBinding, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _model_operation_version_shape(cls, value):
        if isinstance(value, dict) and value.get("model_operation_schema_version", 1) == 2:
            if "model_operation_bindings" not in value:
                raise ValueError("v2 plan model operation bindings are required")
        return value

    @field_validator("plan_id", "requirement_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("context_fingerprint", mode="before")
    @classmethod
    def _context_fingerprint(cls, value: object) -> str:
        from .common import fp

        return fp(value, "context_fingerprint")

    @field_validator(
        "tasks", "phases", "plan_risks", "verification_strategy", "evidence_bindings", "provenance",
        "risks", "evidence_refs", "notes", "model_operation_bindings", mode="before"
    )
    @classmethod
    def _tuples(cls, v, info):
        return _plan_sequence(v, info.field_name)

    @field_validator("title", "objective", mode="before")
    @classmethod
    def _plan_text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("risks", "evidence_refs", mode="before")
    @classmethod
    def _texts(cls, v, info):
        return tuple(
            text(x, info.field_name, 512) for x in _plan_sequence(v, info.field_name)
        )

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, v):
        return confidence(v)

    @model_validator(mode="after")
    def _bound(self):
        if any(
            task.fingerprint != canonical(task, exclude={"fingerprint"})
            for task in self.tasks
        ):
            raise ValueError("task fingerprint mismatch")
        sequences = tuple(task.sequence for task in self.tasks)
        if sequences != tuple(range(1, len(self.tasks) + 1)):
            raise ValueError("task sequence is invalid")
        known = {task.task_id for task in self.tasks}
        if any(
            dependency not in known
            for task in self.tasks
            for dependency in task.depends_on
        ):
            raise ValueError("task dependency is invalid")
        phase_ids = tuple(phase.phase_id for phase in self.phases)
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("plan phase ids must be unique")
        known_phases = set(phase_ids)
        for phase in self.phases:
            if phase.plan_id != self.plan_id or phase.project_id != self.project_id:
                raise ValueError("plan phase identity mismatch")
            if any(dependency not in known_phases for dependency in phase.dependencies):
                raise ValueError("plan phase dependency is invalid")
        risk_ids = tuple(risk.risk_id for risk in self.plan_risks)
        if len(risk_ids) != len(set(risk_ids)):
            raise ValueError("plan risk ids must be unique")
        phase_task_ids = tuple(
            task.task_id for phase in self.phases for task in phase.tasks
        )
        if len(phase_task_ids) != len(set(phase_task_ids)):
            raise ValueError("plan task ids must be unique")
        if self.model_operation_schema_version == 1 and self.model_operation_bindings:
            raise ValueError("v1 plan cannot bind model operations")
        known_phase_tasks = {
            (phase.phase_id, task.task_id)
            for phase in self.phases for task in phase.tasks
        }
        operation_keys = tuple(
            (binding.phase_id, binding.plan_task_id, binding.operation_id)
            for binding in self.model_operation_bindings
        )
        if len(operation_keys) != len(set(operation_keys)):
            raise ValueError("plan model operation binding must be unique")
        if any(
            (binding.phase_id, binding.plan_task_id) not in known_phase_tasks
            for binding in self.model_operation_bindings
        ):
            raise ValueError("plan model operation binding task is invalid")
        output_ids = tuple(
            output.output_id for phase in self.phases for output in phase.outputs
        )
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("plan output ids must be unique")
        strategy_ids = tuple(item.strategy_id for item in self.verification_strategy)
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("plan verification strategy ids must be unique")
        binding_ids = tuple(item.binding_id for item in self.evidence_bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("plan evidence binding ids must be unique")
        for risk in self.plan_risks:
            if risk.plan_id != self.plan_id or risk.project_id != self.project_id:
                raise ValueError("plan risk identity mismatch")
            if any(item.project_id != self.project_id for item in risk.provenance):
                raise ValueError("plan risk provenance identity mismatch")
        for binding in self.evidence_bindings:
            if binding.plan_id != self.plan_id or binding.project_id != self.project_id:
                raise ValueError("plan evidence identity mismatch")
            if any(item.project_id != self.project_id for item in binding.provenance):
                raise ValueError("plan evidence provenance identity mismatch")
        for strategy in self.verification_strategy:
            if strategy.plan_id != self.plan_id or strategy.project_id != self.project_id:
                raise ValueError("plan verification strategy identity mismatch")
            if any(item.project_id != self.project_id for item in strategy.provenance):
                raise ValueError("plan verification provenance identity mismatch")
        for provenance in self.provenance:
            if provenance.project_id != self.project_id:
                raise ValueError("plan provenance identity mismatch")
        return self

    @property
    def content_fingerprint(self) -> str:
        """Stable proposal fingerprint independent of lifecycle state."""

        exclude = {"fingerprint", "status", "notes"}
        if self.model_operation_schema_version == 1:
            exclude.update({"model_operation_schema_version", "model_operation_bindings"})
        return canonical(self, exclude=exclude)

    @model_validator(mode="after")
    def _verify(self):
        exclude = {"fingerprint", "notes"}
        if self.model_operation_schema_version == 1:
            exclude.update({"model_operation_schema_version", "model_operation_bindings"})
        expected = canonical(self, exclude=exclude)
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected and self.fingerprint not in self._legacy_fingerprint_candidates():
            raise ValueError("fingerprint mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if self.model_operation_schema_version != 1:
            return ()
        if self.phases or self.plan_risks or self.verification_strategy or self.evidence_bindings or self.provenance:
            return ()
        return (
            canonical(
                self,
                exclude={
                    "fingerprint", "notes", "title", "objective", "status", "phases", "plan_risks",
                    "verification_strategy", "evidence_bindings", "provenance",
                    "model_operation_schema_version", "model_operation_bindings",
                },
            ),
        )

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringWorkflowStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class EngineeringWorkflowItemStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class EngineeringWorkflowPhaseType(StrEnum):
    REQUIREMENT_CONFIRMATION = "REQUIREMENT_CONFIRMATION"
    SYSTEM_DESIGN = "SYSTEM_DESIGN"
    HARDWARE_DESIGN = "HARDWARE_DESIGN"
    PCB_DESIGN = "PCB_DESIGN"
    FIRMWARE_DEVELOPMENT = "FIRMWARE_DEVELOPMENT"
    SIMULATION = "SIMULATION"
    VERIFICATION = "VERIFICATION"


class EngineeringWorkflowTaskType(StrEnum):
    ANALYSIS = "ANALYSIS"
    DESIGN = "DESIGN"
    IMPLEMENTATION = "IMPLEMENTATION"
    REVIEW = "REVIEW"
    TEST = "TEST"
    DOCUMENTATION = "DOCUMENTATION"


class EngineeringWorkflowArtifactType(StrEnum):
    DOCUMENT = "DOCUMENT"
    SCHEMATIC = "SCHEMATIC"
    PCB = "PCB"
    FIRMWARE = "FIRMWARE"
    CONFIGURATION = "CONFIGURATION"
    SIMULATION_RESULT = "SIMULATION_RESULT"
    TEST_REPORT = "TEST_REPORT"


def _workflow_refs(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _workflow_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


class EngineeringWorkflowVerification(Fingerprinted):
    verification_id: str
    task_id: str
    method: str
    acceptance_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    status: EngineeringWorkflowItemStatus = EngineeringWorkflowItemStatus.PENDING

    @field_validator("verification_id", "task_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("method", mode="before")
    @classmethod
    def _method(cls, value):
        return text(value, "method", 2048)

    @field_validator("acceptance_refs", mode="before")
    @classmethod
    def _acceptance_refs(cls, value):
        return _workflow_refs(value, "acceptance_refs")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value):
        if type(value) is list:
            value = tuple(value)
        refs = tuple(text(item, "evidence_ref", 1024) for item in tuple_values(value, "evidence_refs"))
        if len(refs) != len(set(refs)):
            raise ValueError("evidence_refs must be unique")
        return refs

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, EngineeringWorkflowItemStatus) else EngineeringWorkflowItemStatus(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringWorkflowArtifact(Fingerprinted):
    artifact_id: str
    task_id: str
    artifact_type: EngineeringWorkflowArtifactType
    name: str
    status: EngineeringWorkflowItemStatus = EngineeringWorkflowItemStatus.PENDING
    source_refs: tuple[str, ...] = ()

    @field_validator("artifact_id", "task_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("artifact_type", mode="before")
    @classmethod
    def _artifact_type(cls, value):
        return value if isinstance(value, EngineeringWorkflowArtifactType) else EngineeringWorkflowArtifactType(value)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        return text(value, "name", 1024)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, EngineeringWorkflowItemStatus) else EngineeringWorkflowItemStatus(value)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value):
        return _workflow_refs(value, "source_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringWorkflowTask(Fingerprinted):
    task_id: str
    phase_id: str
    title: str
    description: str
    task_type: EngineeringWorkflowTaskType
    status: EngineeringWorkflowItemStatus = EngineeringWorkflowItemStatus.PENDING
    dependencies: tuple[str, ...] = ()
    input_refs: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    verification_requirements: tuple[str, ...] = ()

    @field_validator("task_id", "phase_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("title", "description", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 4096 if info.field_name == "description" else 1024)

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type(cls, value):
        return value if isinstance(value, EngineeringWorkflowTaskType) else EngineeringWorkflowTaskType(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, EngineeringWorkflowItemStatus) else EngineeringWorkflowItemStatus(value)

    @field_validator(
        "dependencies", "input_refs", "output_refs", "verification_refs",
        "expected_outputs", "verification_requirements", mode="before"
    )
    @classmethod
    def _refs(cls, value, info):
        return _workflow_refs(value, info.field_name)

    @model_validator(mode="after")
    def _no_self_dependency(self):
        if self.task_id in self.dependencies:
            raise ValueError("workflow task cannot depend on itself")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringWorkflowPhase(Fingerprinted):
    phase_id: str
    workflow_id: str
    phase_type: EngineeringWorkflowPhaseType
    name: str
    description: str
    order: int
    status: EngineeringWorkflowItemStatus = EngineeringWorkflowItemStatus.PENDING
    tasks: tuple[EngineeringWorkflowTask, ...] = ()
    outputs: tuple[EngineeringWorkflowArtifact, ...] = ()
    verification_refs: tuple[EngineeringWorkflowVerification, ...] = ()
    dependencies: tuple[str, ...] = ()

    @field_validator("phase_id", "workflow_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("phase_type", mode="before")
    @classmethod
    def _phase_type(cls, value):
        return value if isinstance(value, EngineeringWorkflowPhaseType) else EngineeringWorkflowPhaseType(value)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 4096 if info.field_name == "description" else 1024)

    @field_validator("order", mode="before")
    @classmethod
    def _order(cls, value):
        if type(value) is not int or value < 1:
            raise ValueError("workflow phase order is invalid")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, EngineeringWorkflowItemStatus) else EngineeringWorkflowItemStatus(value)

    @field_validator("tasks", mode="before")
    @classmethod
    def _tasks(cls, value):
        return _workflow_objects(value, "tasks", EngineeringWorkflowTask)

    @field_validator("outputs", mode="before")
    @classmethod
    def _outputs(cls, value):
        return _workflow_objects(value, "outputs", EngineeringWorkflowArtifact)

    @field_validator("verification_refs", mode="before")
    @classmethod
    def _verifications(cls, value):
        return _workflow_objects(value, "verification_refs", EngineeringWorkflowVerification)

    @field_validator("dependencies", mode="before")
    @classmethod
    def _dependencies(cls, value):
        return _workflow_refs(value, "dependencies")

    @model_validator(mode="after")
    def _bound(self):
        task_ids = tuple(item.task_id for item in self.tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("workflow phase task ids must be unique")
        if self.phase_id in self.dependencies:
            raise ValueError("workflow phase cannot depend on itself")
        for task in self.tasks:
            if task.phase_id != self.phase_id:
                raise ValueError("workflow task phase identity mismatch")
        output_ids = tuple(item.artifact_id for item in self.outputs)
        if len(output_ids) != len(set(output_ids)):
            raise ValueError("workflow artifact ids must be unique")
        task_set = set(task_ids)
        if any(item.task_id not in task_set for item in self.outputs):
            raise ValueError("workflow artifact task reference is invalid")
        verification_ids = tuple(item.verification_id for item in self.verification_refs)
        if len(verification_ids) != len(set(verification_ids)):
            raise ValueError("workflow verification ids must be unique")
        verification_by_id = set(verification_ids)
        for verification in self.verification_refs:
            if verification.task_id not in task_set:
                raise ValueError("workflow verification task reference is invalid")
        for task in self.tasks:
            if not set(task.dependencies) <= task_set:
                raise ValueError("workflow task dependency is invalid")
            if not set(task.output_refs) <= set(output_ids):
                raise ValueError("workflow task output reference is invalid")
            if not set(task.expected_outputs) <= set(output_ids):
                raise ValueError("workflow task expected output reference is invalid")
            if not set(task.verification_refs) <= verification_by_id:
                raise ValueError("workflow task verification reference is invalid")
            if not set(task.verification_requirements) <= verification_by_id:
                raise ValueError("workflow task verification requirement reference is invalid")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringWorkflow(Fingerprinted):
    workflow_id: str
    project_id: str
    conversation_id: str
    plan_id: str
    architecture_id: str
    title: str
    status: EngineeringWorkflowStatus
    phases: tuple[EngineeringWorkflowPhase, ...] = ()
    current_phase_id: str | None = None
    workflow_version: Literal["1.0"] = "1.0"
    provenance: tuple[PlanProvenance, ...]

    @field_validator("workflow_id", "project_id", "conversation_id", "plan_id", "architecture_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return text(value, "title", 4096)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, EngineeringWorkflowStatus) else EngineeringWorkflowStatus(value)

    @field_validator("phases", mode="before")
    @classmethod
    def _phases(cls, value):
        return _workflow_objects(value, "phases", EngineeringWorkflowPhase)

    @field_validator("current_phase_id", mode="before")
    @classmethod
    def _current_phase(cls, value):
        return None if value is None else ident(value, "current_phase_id")

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _workflow_objects(value, "provenance", PlanProvenance)

    @model_validator(mode="after")
    def _bound(self):
        phase_ids = tuple(item.phase_id for item in self.phases)
        if len(phase_ids) != len(set(phase_ids)):
            raise ValueError("workflow phase ids must be unique")
        if tuple(item.order for item in self.phases) != tuple(range(1, len(self.phases) + 1)):
            raise ValueError("workflow phase order is invalid")
        phase_set = set(phase_ids)
        if self.current_phase_id is not None and self.current_phase_id not in phase_set:
            raise ValueError("workflow current phase reference is invalid")
        for phase in self.phases:
            if phase.workflow_id != self.workflow_id:
                raise ValueError("workflow phase identity mismatch")
            if not set(phase.dependencies) <= phase_set:
                raise ValueError("workflow phase dependency is invalid")
        task_ids = tuple(task.task_id for phase in self.phases for task in phase.tasks)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("workflow task ids must be unique")
        artifact_ids = tuple(output.artifact_id for phase in self.phases for output in phase.outputs)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("workflow artifact ids must be unique")
        verification_ids = tuple(
            verification.verification_id
            for phase in self.phases
            for verification in phase.verification_refs
        )
        if len(verification_ids) != len(set(verification_ids)):
            raise ValueError("workflow verification ids must be unique")
        plan_sources = tuple(
            item for item in self.provenance
            if item.source_type == "ENGINEERING_PLAN" and item.source_id == self.plan_id
        )
        architecture_sources = tuple(
            item for item in self.provenance
            if item.source_type == "ARCHITECTURE_DESIGN" and item.source_id == self.architecture_id
        )
        if len(plan_sources) != 1 or len(architecture_sources) != 1:
            raise ValueError("workflow source provenance is incomplete")
        if not self.provenance or any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("workflow provenance identity mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureDesignStatus(StrEnum):
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    PROPOSED = "PROPOSED"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    IMPLEMENTING = "IMPLEMENTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class ArchitectureLevel(StrEnum):
    SYSTEM = "SYSTEM"
    HARDWARE = "HARDWARE"
    SOFTWARE = "SOFTWARE"
    INTERFACE = "INTERFACE"


class ArchitectureInterfaceType(StrEnum):
    HARDWARE = "HARDWARE"
    SOFTWARE = "SOFTWARE"


class ArchitectureFlowType(StrEnum):
    DATA = "DATA"
    CONTROL = "CONTROL"


class ArchitectureConfidence(StrEnum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"


class ArchitectureDecisionType(StrEnum):
    HARDWARE_SELECTION = "HARDWARE_SELECTION"
    SOFTWARE_SELECTION = "SOFTWARE_SELECTION"
    ARCHITECTURE_PATTERN = "ARCHITECTURE_PATTERN"


class ArchitectureReviewStatus(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ArchitectureReviewFindingStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"


class ArchitectureComponent(Fingerprinted):
    component_id: str
    architecture_id: str
    project_id: str
    name: str
    category: str
    responsibility: str
    level: ArchitectureLevel = Field(
        validation_alias=AliasChoices("level", "layer")
    )
    confidence: ArchitectureConfidence
    evidence_binding_ids: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("component_id", "architecture_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", "category", "responsibility", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("evidence_binding_ids", mode="before")
    @classmethod
    def _bindings(cls, value):
        refs = tuple(ident(item, "evidence_binding_id") for item in _plan_sequence(value, "evidence_binding_ids"))
        if not refs:
            raise ValueError("architecture component requires evidence bindings")
        if len(refs) != len(set(refs)):
            raise ValueError("architecture component evidence bindings must be unique")
        return refs

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        if "level" not in values and "layer" in values:
            values["level"] = values.pop("layer")
        return finalize(cls, values)

    @property
    def layer(self) -> ArchitectureLevel:
        """Compatibility name for early architecture projections."""

        return self.level


class ArchitectureInterface(Fingerprinted):
    interface_id: str
    architecture_id: str
    project_id: str
    source_component_id: str
    target_component_id: str
    interface_type: ArchitectureInterfaceType
    protocol: str
    verification: str
    confidence: ArchitectureConfidence
    evidence_binding_ids: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator(
        "interface_id", "architecture_id", "project_id", "source_component_id",
        "target_component_id", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("protocol", "verification", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("evidence_binding_ids", mode="before")
    @classmethod
    def _bindings(cls, value):
        refs = tuple(ident(item, "evidence_binding_id") for item in _plan_sequence(value, "evidence_binding_ids"))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("architecture interface evidence bindings are invalid")
        return refs

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureBoundary(Fingerprinted):
    architecture_id: str
    project_id: str
    hardware_component_ids: tuple[str, ...]
    software_component_ids: tuple[str, ...]
    evidence_binding_ids: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("architecture_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("hardware_component_ids", "software_component_ids", "evidence_binding_ids", mode="before")
    @classmethod
    def _refs(cls, value, info):
        refs = tuple(ident(item, info.field_name.removesuffix("s")) for item in _plan_sequence(value, info.field_name))
        if len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} must be unique")
        return refs

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @model_validator(mode="after")
    def _disjoint(self):
        if set(self.hardware_component_ids) & set(self.software_component_ids):
            raise ValueError("architecture boundary component sets must be disjoint")
        if not self.evidence_binding_ids:
            raise ValueError("architecture boundary requires evidence bindings")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureFlow(Fingerprinted):
    flow_id: str
    architecture_id: str
    project_id: str
    flow_type: ArchitectureFlowType
    source_component_id: str
    target_component_id: str
    data_or_command: str
    confidence: ArchitectureConfidence
    evidence_binding_ids: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("flow_id", "architecture_id", "project_id", "source_component_id", "target_component_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("data_or_command", mode="before")
    @classmethod
    def _data(cls, value):
        return text(value, "data_or_command", 1024)

    @field_validator("evidence_binding_ids", mode="before")
    @classmethod
    def _bindings(cls, value):
        refs = tuple(ident(item, "evidence_binding_id") for item in _plan_sequence(value, "evidence_binding_ids"))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("architecture flow evidence bindings are invalid")
        return refs

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureDecision(Fingerprinted):
    decision_id: str
    architecture_id: str
    project_id: str
    subject: str
    selected_option: str
    alternatives: tuple[str, ...]
    decision_type: ArchitectureDecisionType
    rationale: str
    confidence: ArchitectureConfidence
    evidence_binding_ids: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("decision_id", "architecture_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("subject", "selected_option", "rationale", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("alternatives", "evidence_binding_ids", mode="before")
    @classmethod
    def _refs(cls, value, info):
        field = "alternative" if info.field_name == "alternatives" else "evidence_binding_id"
        refs = tuple(text(item, field, 1024) if info.field_name == "alternatives" else ident(item, field) for item in _plan_sequence(value, info.field_name))
        if len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} must be unique")
        if info.field_name == "evidence_binding_ids" and not refs:
            raise ValueError("architecture decision requires evidence bindings")
        return refs

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureVerification(Fingerprinted):
    verification_id: str
    architecture_id: str
    project_id: str
    method: str
    evidence_required: tuple[str, ...]
    acceptance_criterion_ids: tuple[str, ...]
    evidence_binding_ids: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("verification_id", "architecture_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("method", mode="before")
    @classmethod
    def _method(cls, value):
        return text(value, "method", 2048)

    @field_validator("evidence_required", "acceptance_criterion_ids", "evidence_binding_ids", mode="before")
    @classmethod
    def _refs(cls, value, info):
        field = "acceptance_criterion_id" if info.field_name == "acceptance_criterion_ids" else "evidence_binding_id" if info.field_name == "evidence_binding_ids" else "evidence_required"
        refs = tuple(ident(item, field) if info.field_name != "evidence_required" else text(item, field, 1024) for item in _plan_sequence(value, info.field_name))
        if len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} must be unique")
        if info.field_name != "evidence_required" and not refs:
            raise ValueError(f"{info.field_name} cannot be empty")
        return refs

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureEvidenceBinding(Fingerprinted):
    binding_id: str
    architecture_id: str
    plan_id: str
    project_id: str
    source_type: str
    source_id: str
    source_fingerprint: str
    requirement_ids: tuple[str, ...]
    acceptance_criterion_ids: tuple[str, ...]
    relationship: str
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("binding_id", "architecture_id", "plan_id", "project_id", "source_type", "source_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value):
        return fp(value, "source_fingerprint")

    @field_validator("requirement_ids", "acceptance_criterion_ids", mode="before")
    @classmethod
    def _refs(cls, value, info):
        field = "requirement_id" if info.field_name == "requirement_ids" else "acceptance_criterion_id"
        refs = tuple(ident(item, field) for item in _plan_sequence(value, info.field_name))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} are invalid")
        return refs

    @field_validator("relationship", mode="before")
    @classmethod
    def _relationship(cls, value):
        return text(value, "relationship", 1024)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureReviewFinding(Fingerprinted):
    finding_id: str
    architecture_id: str
    project_id: str
    category: str
    description: str
    status: ArchitectureReviewFindingStatus = ArchitectureReviewFindingStatus.OPEN
    resolution: str | None = None
    evidence_binding_ids: tuple[str, ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("finding_id", "architecture_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("category", "description", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("resolution", mode="before")
    @classmethod
    def _resolution(cls, value):
        return None if value is None else text(value, "resolution", 2048)

    @field_validator("evidence_binding_ids", mode="before")
    @classmethod
    def _bindings(cls, value):
        refs = tuple(ident(item, "evidence_binding_id") for item in _plan_sequence(value, "evidence_binding_ids"))
        if len(refs) != len(set(refs)):
            raise ValueError("architecture finding evidence bindings must be unique")
        return refs

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @model_validator(mode="after")
    def _resolution_binding(self):
        if self.status is ArchitectureReviewFindingStatus.OPEN and self.resolution is not None:
            raise ValueError("open architecture finding cannot have a resolution")
        if self.status is ArchitectureReviewFindingStatus.RESOLVED and self.resolution is None:
            raise ValueError("resolved architecture finding requires a resolution")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureReviewDecision(Fingerprinted):
    architecture_id: str
    plan_id: str
    project_id: str
    proposal_fingerprint: str
    status: ArchitectureReviewStatus
    reviewer: str
    decided_at: datetime
    findings: tuple[ArchitectureReviewFinding, ...] = ()

    @field_validator("architecture_id", "plan_id", "project_id", "reviewer", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fingerprint(cls, value):
        return fp(value, "proposal_fingerprint")

    @field_validator("decided_at", mode="before")
    @classmethod
    def _decided_at(cls, value):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("decided_at is invalid") from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("decided_at is invalid")
        return value.astimezone(UTC)

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value):
        return _plan_sequence(value, "findings")

    @model_validator(mode="after")
    def _verify_findings(self):
        finding_ids = tuple(item.finding_id for item in self.findings)
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("architecture review finding ids must be unique")
        for finding in self.findings:
            if finding.architecture_id != self.architecture_id or finding.project_id != self.project_id:
                raise ValueError("architecture review finding identity mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArchitectureDesign(Fingerprinted):
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset()
    architecture_id: str
    project_id: str
    plan_id: str
    context_fingerprint: str
    plan_fingerprint: str
    title: str
    objective: str
    status: ArchitectureDesignStatus = ArchitectureDesignStatus.PROPOSED
    system_components: tuple[ArchitectureComponent, ...]
    interfaces: tuple[ArchitectureInterface, ...] = ()
    boundary: ArchitectureBoundary
    data_flow: tuple[ArchitectureFlow, ...] = ()
    control_flow: tuple[ArchitectureFlow, ...] = ()
    decisions: tuple[ArchitectureDecision, ...] = ()
    verification: tuple[ArchitectureVerification, ...]
    constraints: tuple[str, ...] = ()
    evidence_bindings: tuple[ArchitectureEvidenceBinding, ...]
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("architecture_id", "project_id", "plan_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("context_fingerprint", "plan_fingerprint", mode="before")
    @classmethod
    def _fingerprints(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("title", "objective", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator(
        "system_components", "interfaces", "data_flow", "control_flow", "decisions",
        "verification", "evidence_bindings", "provenance", mode="before"
    )
    @classmethod
    def _objects(cls, value, info):
        return _plan_sequence(value, info.field_name)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value):
        return tuple(text(item, "constraint", 1024) for item in _plan_sequence(value, "constraints"))

    @model_validator(mode="before")
    @classmethod
    def _early_reference_check(cls, values):
        """Report identity/reference failures before nested tamper failures."""

        if not isinstance(values, dict):
            return values
        components = values.get("system_components", ())
        component_ids = {
            item.get("component_id") for item in components if isinstance(item, dict)
        }
        for field in ("interfaces", "data_flow", "control_flow"):
            for item in values.get(field, ()):
                if not isinstance(item, dict):
                    continue
                if (
                    item.get("source_component_id") not in component_ids
                    or item.get("target_component_id") not in component_ids
                ):
                    raise ValueError(f"architecture {field} component reference is invalid")
        return values

    @model_validator(mode="after")
    def _bound(self):
        components = self.system_components
        component_ids = tuple(item.component_id for item in components)
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("architecture component ids must be unique")
        known_components = set(component_ids)
        binding_ids = tuple(item.binding_id for item in self.evidence_bindings)
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("architecture evidence binding ids must be unique")
        known_bindings = set(binding_ids)
        element_ids = (
            *component_ids,
            *(item.interface_id for item in self.interfaces),
            *(item.flow_id for item in (*self.data_flow, *self.control_flow)),
            *(item.decision_id for item in self.decisions),
            *(item.verification_id for item in self.verification),
        )
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("architecture element ids must be unique")
        if self.boundary.architecture_id != self.architecture_id or self.boundary.project_id != self.project_id:
            raise ValueError("architecture boundary identity mismatch")
        if not (set(self.boundary.hardware_component_ids) | set(self.boundary.software_component_ids)) <= known_components:
            raise ValueError("architecture boundary component reference is invalid")
        component_by_id = {item.component_id: item for item in components}
        if any(component_by_id[item].level is not ArchitectureLevel.HARDWARE for item in self.boundary.hardware_component_ids):
            raise ValueError("architecture hardware boundary reference is invalid")
        if any(component_by_id[item].level is not ArchitectureLevel.SOFTWARE for item in self.boundary.software_component_ids):
            raise ValueError("architecture software boundary reference is invalid")
        for item in components:
            self._check_child(item, self.architecture_id, self.project_id, known_bindings)
        for item in self.interfaces:
            self._check_child(item, self.architecture_id, self.project_id, known_bindings)
            if item.source_component_id not in known_components or item.target_component_id not in known_components:
                raise ValueError("architecture interface component reference is invalid")
        for item in (*self.data_flow, *self.control_flow):
            self._check_child(item, self.architecture_id, self.project_id, known_bindings)
            if item.source_component_id not in known_components or item.target_component_id not in known_components:
                raise ValueError("architecture flow component reference is invalid")
        self._check_child(self.boundary, self.architecture_id, self.project_id, known_bindings)
        for item in self.decisions:
            self._check_child(item, self.architecture_id, self.project_id, known_bindings)
        for item in self.verification:
            self._check_child(item, self.architecture_id, self.project_id, known_bindings)
        for binding in self.evidence_bindings:
            if binding.architecture_id != self.architecture_id or binding.plan_id != self.plan_id or binding.project_id != self.project_id:
                raise ValueError("architecture evidence binding identity mismatch")
            if any(item.project_id != self.project_id for item in binding.provenance):
                raise ValueError("architecture evidence provenance identity mismatch")
        known_criterion_ids = {
            criterion_id
            for binding in self.evidence_bindings
            for criterion_id in binding.acceptance_criterion_ids
        }
        for item in self.verification:
            if not set(item.acceptance_criterion_ids) <= known_criterion_ids:
                raise ValueError("architecture verification acceptance criterion reference is invalid")
        for provenance in self.provenance:
            if provenance.project_id != self.project_id:
                raise ValueError("architecture provenance identity mismatch")
        if not self.system_components or not self.verification or not self.evidence_bindings:
            raise ValueError("architecture design is incomplete")
        return self

    @staticmethod
    def _check_child(item, architecture_id: str, project_id: str, binding_ids: set[str]) -> None:
        if item.architecture_id != architecture_id or item.project_id != project_id:
            raise ValueError("architecture element identity mismatch")
        if not set(item.evidence_binding_ids) <= binding_ids:
            raise ValueError("architecture evidence binding reference is invalid")
        if any(item.project_id != project_id for item in item.provenance):
            raise ValueError("architecture element provenance identity mismatch")

    @property
    def content_fingerprint(self) -> str:
        """Stable architecture proposal fingerprint independent of lifecycle state."""

        return canonical(self, exclude={"fingerprint", "status"})

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class HardwareDomainType(StrEnum):
    SYSTEM = "SYSTEM"
    CONTROLLER = "CONTROLLER"
    POWER = "POWER"
    SENSOR = "SENSOR"
    ACTUATOR = "ACTUATOR"
    COMMUNICATION = "COMMUNICATION"
    MECHANICAL = "MECHANICAL"
    OTHER = "OTHER"


class HardwareCandidateType(StrEnum):
    CAPABILITY = "CAPABILITY"
    DEVICE = "DEVICE"
    MODULE = "MODULE"
    UNKNOWN = "UNKNOWN"


class HardwareCandidateStatus(StrEnum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class HardwareDecisionStatus(StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class HardwareInterfaceType(StrEnum):
    POWER = "POWER"
    GPIO = "GPIO"
    BUS = "BUS"
    CONTROL = "CONTROL"
    CLOCK = "CLOCK"
    DEBUG = "DEBUG"
    COMMUNICATION = "COMMUNICATION"
    ANALOG = "ANALOG"
    DIGITAL = "DIGITAL"
    UNKNOWN = "UNKNOWN"


class SchematicCircuitType(StrEnum):
    POWER_CIRCUIT = "POWER_CIRCUIT"
    CONTROLLER_CIRCUIT = "CONTROLLER_CIRCUIT"
    INTERFACE_CIRCUIT = "INTERFACE_CIRCUIT"
    SENSOR_CIRCUIT = "SENSOR_CIRCUIT"
    ACTUATOR_CIRCUIT = "ACTUATOR_CIRCUIT"
    COMMUNICATION_CIRCUIT = "COMMUNICATION_CIRCUIT"


class HardwareDesignPlanStatus(StrEnum):
    PLANNED = "PLANNED"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


def _hardware_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


def _hardware_texts(value: object, field: str, maximum: int = 2048) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    return tuple(text(item, field.removesuffix("s"), maximum) for item in tuple_values(value, field))


def _hardware_refs(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _hardware_revision(
    architecture_id: str,
    architecture_fingerprint: str,
    workflow_id: str,
    workflow_fingerprint: str,
) -> str:
    digest = hashlib.sha256(
        f"hardware-design-v1|{architecture_id}|{architecture_fingerprint}|{workflow_id}|{workflow_fingerprint}".encode()
    ).hexdigest()[:24]
    return f"revision-{digest}"


class HardwareDomain(Fingerprinted):
    domain_id: str
    hardware_spec_id: str
    name: str
    type: HardwareDomainType
    requirements: tuple[str, ...]
    interfaces: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()

    @field_validator("domain_id", "hardware_spec_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        return text(value, "name", 1024)

    @field_validator("type", mode="before")
    @classmethod
    def _type(cls, value):
        return value if isinstance(value, HardwareDomainType) else HardwareDomainType(value)

    @field_validator("requirements", mode="before")
    @classmethod
    def _requirements(cls, value):
        return _hardware_texts(value, "requirements")

    @field_validator("interfaces", "verification_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _hardware_refs(value, info.field_name)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class HardwareComponentCandidate(Fingerprinted):
    candidate_id: str
    domain_id: str
    candidate_type: HardwareCandidateType = HardwareCandidateType.UNKNOWN
    category: str
    name: str
    description: str
    selection_status: HardwareCandidateStatus
    constraints: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("candidate_id", "domain_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("candidate_type", mode="before")
    @classmethod
    def _candidate_type(cls, value):
        return value if isinstance(value, HardwareCandidateType) else HardwareCandidateType(value)

    @field_validator("category", "name", "description", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("selection_status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, HardwareCandidateStatus) else HardwareCandidateStatus(value)

    @field_validator("constraints", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _hardware_texts(value, info.field_name, 2048) if info.field_name == "constraints" else _hardware_refs(value, info.field_name)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class HardwareDecision(Fingerprinted):
    decision_id: str
    subject: str
    options: tuple[str, ...]
    selected_option: str | None = None
    rationale: str
    status: HardwareDecisionStatus = HardwareDecisionStatus.OPEN
    evidence_refs: tuple[str, ...] = ()

    @field_validator("decision_id", mode="before")
    @classmethod
    def _id(cls, value):
        return ident(value, "decision_id")

    @field_validator("subject", "rationale", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("options", mode="before")
    @classmethod
    def _options(cls, value):
        return _hardware_texts(value, "options", 2048)

    @field_validator("selected_option", mode="before")
    @classmethod
    def _selected(cls, value):
        return None if value is None else text(value, "selected_option", 2048)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, HardwareDecisionStatus) else HardwareDecisionStatus(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence_refs(cls, value):
        return _hardware_refs(value, "evidence_refs")

    @model_validator(mode="after")
    def _selection_bound(self):
        if self.status is not HardwareDecisionStatus.CONFIRMED and self.selected_option is not None:
            raise ValueError("selected_option requires confirmed hardware decision")
        if self.status is HardwareDecisionStatus.CONFIRMED:
            if self.selected_option is None:
                raise ValueError("confirmed hardware decision requires selected_option")
            if self.options and self.selected_option not in self.options:
                raise ValueError("selected_option must be one of options")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class HardwareInterface(Fingerprinted):
    interface_id: str
    source: str
    target: str
    interface_type: HardwareInterfaceType = HardwareInterfaceType.UNKNOWN
    signal_refs: tuple[str, ...] = ()
    direction: str = "UNKNOWN"
    constraints: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("interface_id", "source", "target", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("interface_type", mode="before")
    @classmethod
    def _type(cls, value):
        return value if isinstance(value, HardwareInterfaceType) else HardwareInterfaceType(value)

    @field_validator("signal_refs", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _hardware_refs(value, info.field_name)

    @field_validator("direction", mode="before")
    @classmethod
    def _direction(cls, value):
        return text(value, "direction", 256)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value):
        return _hardware_texts(value, "constraints", 2048)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicDesignPlan(Fingerprinted):
    schematic_plan_id: str
    hardware_spec_id: str
    circuits: tuple[SchematicCircuitType, ...]
    status: HardwareDesignPlanStatus = HardwareDesignPlanStatus.PLANNED
    constraints: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()

    @field_validator("schematic_plan_id", "hardware_spec_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("circuits", mode="before")
    @classmethod
    def _circuits(cls, value):
        if type(value) is list:
            value = tuple(value)
        return tuple(
            item if isinstance(item, SchematicCircuitType) else SchematicCircuitType(item)
            for item in tuple_values(value, "circuits")
        )

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, HardwareDesignPlanStatus) else HardwareDesignPlanStatus(value)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value):
        return _hardware_texts(value, "constraints", 2048)

    @field_validator("verification_refs", mode="before")
    @classmethod
    def _verification_refs(cls, value):
        return _hardware_refs(value, "verification_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PCBDesignPlan(Fingerprinted):
    pcb_plan_id: str
    hardware_spec_id: str
    status: HardwareDesignPlanStatus = HardwareDesignPlanStatus.PLANNED
    constraints: tuple[str, ...] = ()
    layout_requirements: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()

    @field_validator("pcb_plan_id", "hardware_spec_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, HardwareDesignPlanStatus) else HardwareDesignPlanStatus(value)

    @field_validator("constraints", "layout_requirements", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return _hardware_texts(value, info.field_name, 2048)

    @field_validator("verification_refs", mode="before")
    @classmethod
    def _verification_refs(cls, value):
        return _hardware_refs(value, "verification_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class HardwareDesignSpecification(Fingerprinted):
    hardware_spec_id: str
    project_id: str
    architecture_id: str
    workflow_id: str
    architecture_fingerprint: str
    workflow_fingerprint: str
    design_revision: str
    title: str
    system_requirements: tuple[str, ...]
    hardware_domains: tuple[HardwareDomain, ...]
    component_candidates: tuple[HardwareComponentCandidate, ...] = ()
    interfaces: tuple[HardwareInterface, ...] = ()
    decisions: tuple[HardwareDecision, ...] = ()
    constraints: tuple[str, ...] = ()
    verification_requirements: tuple[str, ...] = ()
    source_refs: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...]
    schematic_plan: SchematicDesignPlan | None = None
    pcb_plan: PCBDesignPlan | None = None

    @field_validator(
        "hardware_spec_id", "project_id", "architecture_id", "workflow_id", "design_revision", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("architecture_fingerprint", "workflow_fingerprint", mode="before")
    @classmethod
    def _fingerprints(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return text(value, "title", 4096)

    @field_validator(
        "system_requirements", "constraints", "verification_requirements", mode="before"
    )
    @classmethod
    def _texts(cls, value, info):
        return _hardware_texts(value, info.field_name, 4096)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _source_refs(cls, value):
        return _hardware_refs(value, "source_refs")

    @field_validator("hardware_domains", mode="before")
    @classmethod
    def _domains(cls, value):
        return _hardware_objects(value, "hardware_domains", HardwareDomain)

    @field_validator("component_candidates", mode="before")
    @classmethod
    def _candidates(cls, value):
        return _hardware_objects(value, "component_candidates", HardwareComponentCandidate)

    @field_validator("interfaces", mode="before")
    @classmethod
    def _interfaces(cls, value):
        return _hardware_objects(value, "interfaces", HardwareInterface)

    @field_validator("decisions", mode="before")
    @classmethod
    def _decisions(cls, value):
        return _hardware_objects(value, "decisions", HardwareDecision)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _hardware_objects(value, "provenance", PlanProvenance)

    @field_validator("schematic_plan", mode="before")
    @classmethod
    def _schematic_plan(cls, value):
        return None if value is None else SchematicDesignPlan.model_validate(copy.deepcopy(value), strict=False)

    @field_validator("pcb_plan", mode="before")
    @classmethod
    def _pcb_plan(cls, value):
        return None if value is None else PCBDesignPlan.model_validate(copy.deepcopy(value), strict=False)

    @model_validator(mode="after")
    def _bound(self):
        if self.design_revision != _hardware_revision(
            self.architecture_id,
            self.architecture_fingerprint,
            self.workflow_id,
            self.workflow_fingerprint,
        ):
            raise ValueError("hardware design revision binding mismatch")
        domain_ids = tuple(item.domain_id for item in self.hardware_domains)
        interface_ids = tuple(item.interface_id for item in self.interfaces)
        candidate_ids = tuple(item.candidate_id for item in self.component_candidates)
        decision_ids = tuple(item.decision_id for item in self.decisions)
        all_ids = (*domain_ids, *interface_ids, *candidate_ids, *decision_ids)
        if len(domain_ids) != len(set(domain_ids)):
            raise ValueError("hardware domain ids must be unique")
        if len(interface_ids) != len(set(interface_ids)):
            raise ValueError("hardware interface ids must be unique")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("hardware candidate ids must be unique")
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("hardware decision ids must be unique")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("hardware design ids must be globally unique")
        domains = set(domain_ids)
        interfaces = set(interface_ids)
        for domain in self.hardware_domains:
            if domain.hardware_spec_id != self.hardware_spec_id:
                raise ValueError("hardware domain identity mismatch")
            if not set(domain.interfaces) <= interfaces:
                raise ValueError("hardware domain interface reference is invalid")
        for candidate in self.component_candidates:
            if candidate.domain_id not in domains:
                raise ValueError("hardware candidate domain reference is invalid")
        for interface in self.interfaces:
            if interface.source not in domains or interface.target not in domains:
                raise ValueError("hardware interface domain reference is invalid")
        for plan in (self.schematic_plan, self.pcb_plan):
            if plan is not None and plan.hardware_spec_id != self.hardware_spec_id:
                raise ValueError("hardware design plan identity mismatch")
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("hardware design provenance identity mismatch")
        architecture_sources = tuple(
            item for item in self.provenance
            if item.source_type == "ARCHITECTURE_DESIGN" and item.source_id == self.architecture_id
        )
        workflow_sources = tuple(
            item for item in self.provenance
            if item.source_type == "ENGINEERING_WORKFLOW" and item.source_id == self.workflow_id
        )
        if (
            len(architecture_sources) != 1
            or architecture_sources[0].source_fingerprint != self.architecture_fingerprint
            or len(workflow_sources) != 1
            or workflow_sources[0].source_fingerprint != self.workflow_fingerprint
        ):
            raise ValueError("hardware design source provenance is incomplete")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareModuleType(StrEnum):
    APPLICATION = "APPLICATION"
    CONTROL = "CONTROL"
    DRIVER = "DRIVER"
    HAL = "HAL"
    COMMUNICATION = "COMMUNICATION"
    MIDDLEWARE = "MIDDLEWARE"
    BOOT = "BOOT"
    UNKNOWN = "UNKNOWN"


class FirmwareInterfaceRole(StrEnum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"
    CONTROL = "CONTROL"
    COMMUNICATION = "COMMUNICATION"
    POWER_STATE = "POWER_STATE"
    UNKNOWN = "UNKNOWN"


class FirmwareTaskType(StrEnum):
    DESIGN = "DESIGN"
    DRIVER = "DRIVER"
    IMPLEMENTATION = "IMPLEMENTATION"
    INTEGRATION = "INTEGRATION"
    TEST = "TEST"
    DOCUMENTATION = "DOCUMENTATION"


class FirmwareTaskStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


def _firmware_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


def _firmware_texts(value: object, field: str, maximum: int = 4096) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    return tuple(text(item, field.removesuffix("s"), maximum) for item in tuple_values(value, field))


def _firmware_refs(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _firmware_revision(
    hardware_design_id: str,
    hardware_design_fingerprint: str,
    workflow_id: str,
    workflow_fingerprint: str,
) -> str:
    digest = hashlib.sha256(
        f"firmware-design-v1|{hardware_design_id}|{hardware_design_fingerprint}|{workflow_id}|{workflow_fingerprint}".encode()
    ).hexdigest()[:24]
    return f"revision-{digest}"


class FirmwareModule(Fingerprinted):
    module_id: str
    firmware_spec_id: str
    name: str
    module_type: FirmwareModuleType
    responsibility: str
    dependencies: tuple[str, ...] = ()
    hardware_bindings: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    @field_validator("module_id", "firmware_spec_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", "responsibility", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("module_type", mode="before")
    @classmethod
    def _type(cls, value):
        return value if isinstance(value, FirmwareModuleType) else FirmwareModuleType(value)

    @field_validator("dependencies", "hardware_bindings", "verification_refs", "source_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _firmware_refs(value, info.field_name)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class HardwareFirmwareBinding(Fingerprinted):
    binding_id: str
    hardware_interface_id: str
    firmware_module_id: str
    interface_role: FirmwareInterfaceRole = FirmwareInterfaceRole.UNKNOWN
    direction: str = "UNKNOWN"
    signal_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("binding_id", "hardware_interface_id", "firmware_module_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("interface_role", mode="before")
    @classmethod
    def _role(cls, value):
        return value if isinstance(value, FirmwareInterfaceRole) else FirmwareInterfaceRole(value)

    @field_validator("direction", mode="before")
    @classmethod
    def _direction(cls, value):
        return text(value, "direction", 256)

    @field_validator("signal_refs", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _firmware_refs(value, info.field_name)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareTask(Fingerprinted):
    task_id: str
    firmware_spec_id: str
    module_id: str
    title: str
    task_type: FirmwareTaskType
    dependencies: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    status: FirmwareTaskStatus = FirmwareTaskStatus.PLANNED

    @field_validator("task_id", "firmware_spec_id", "module_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return text(value, "title", 4096)

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type(cls, value):
        return value if isinstance(value, FirmwareTaskType) else FirmwareTaskType(value)

    @field_validator("dependencies", "verification_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _firmware_refs(value, info.field_name)

    @field_validator("expected_outputs", mode="before")
    @classmethod
    def _outputs(cls, value):
        return _firmware_texts(value, "expected_outputs", 1024)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, FirmwareTaskStatus) else FirmwareTaskStatus(value)

    @model_validator(mode="after")
    def _planned_only(self):
        if self.status is not FirmwareTaskStatus.PLANNED:
            raise ValueError("firmware task status is reserved for v1 planning")
        if self.task_id in self.dependencies:
            raise ValueError("firmware task cannot depend on itself")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareVerificationRequirement(Fingerprinted):
    verification_id: str
    target_module: str
    method: str
    acceptance_refs: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    simulation_refs: tuple[str, ...] = ()

    @field_validator("verification_id", "target_module", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("method", mode="before")
    @classmethod
    def _method(cls, value):
        return text(value, "method", 4096)

    @field_validator("acceptance_refs", "evidence_requirements", "simulation_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _firmware_refs(value, info.field_name)

    @model_validator(mode="after")
    def _simulation_reserved(self):
        if self.simulation_refs:
            raise ValueError("simulation_refs are reserved for a future simulation layer")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareSpecification(Fingerprinted):
    firmware_spec_id: str
    project_id: str
    conversation_id: str
    hardware_design_id: str
    architecture_id: str
    workflow_id: str
    hardware_design_fingerprint: str
    workflow_fingerprint: str
    title: str
    requirements: tuple[str, ...] = ()
    runtime_constraints: tuple[str, ...] = ()
    software_interfaces: tuple[str, ...] = ()
    modules: tuple[FirmwareModule, ...] = ()
    bindings: tuple[HardwareFirmwareBinding, ...] = ()
    tasks: tuple[FirmwareTask, ...] = ()
    verification_requirements: tuple[FirmwareVerificationRequirement, ...] = ()
    source_refs: tuple[str, ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()
    design_revision: str
    # Canonical target facts are optional for backward-compatible legacy
    # planning records, but a recovered Golden must populate all of them.
    target_platform: str | None = None
    mcu_family: str | None = None
    mcu_exact_part: str | None = None
    architecture: str | None = None
    firmware_framework: str | None = None
    build_system: str | None = None
    toolchain_family: str | None = None
    design_truth_id: str | None = None
    design_truth_fingerprint: str | None = None
    source_layout: tuple[str, ...] = ()
    entry_point: str | None = None
    peripheral_expectations: tuple[str, ...] = ()

    @field_validator(
        "firmware_spec_id", "project_id", "conversation_id", "hardware_design_id",
        "architecture_id", "workflow_id", "design_revision", mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("hardware_design_fingerprint", "workflow_fingerprint", mode="before")
    @classmethod
    def _fingerprints(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return text(value, "title", 4096)

    @field_validator("requirements", "runtime_constraints", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return _firmware_texts(value, info.field_name)

    @field_validator("software_interfaces", "source_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _firmware_refs(value, info.field_name)

    @field_validator("modules", mode="before")
    @classmethod
    def _modules(cls, value):
        return _firmware_objects(value, "modules", FirmwareModule)

    @field_validator("bindings", mode="before")
    @classmethod
    def _bindings(cls, value):
        return _firmware_objects(value, "bindings", HardwareFirmwareBinding)

    @field_validator("tasks", mode="before")
    @classmethod
    def _tasks(cls, value):
        return _firmware_objects(value, "tasks", FirmwareTask)

    @field_validator("verification_requirements", mode="before")
    @classmethod
    def _verification_requirements(cls, value):
        return _firmware_objects(value, "verification_requirements", FirmwareVerificationRequirement)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _firmware_objects(value, "provenance", PlanProvenance)

    @field_validator(
        "target_platform", "mcu_family", "mcu_exact_part", "architecture",
        "firmware_framework", "build_system", "toolchain_family", "entry_point",
        mode="before",
    )
    @classmethod
    def _target_text(cls, value, info):
        return None if value is None else text(value, info.field_name, 256)

    @field_validator("design_truth_id", mode="before")
    @classmethod
    def _design_truth_id(cls, value):
        return None if value is None else ident(value, "design_truth_id")

    @field_validator("design_truth_fingerprint", mode="before")
    @classmethod
    def _design_truth_fingerprint(cls, value):
        return None if value is None else fp(value, "design_truth_fingerprint")

    @field_validator("source_layout", "peripheral_expectations", mode="before")
    @classmethod
    def _target_refs(cls, value, info):
        return _firmware_refs(value, info.field_name)

    @model_validator(mode="after")
    def _bound(self):
        if self.design_revision != _firmware_revision(
            self.hardware_design_id,
            self.hardware_design_fingerprint,
            self.workflow_id,
            self.workflow_fingerprint,
        ):
            raise ValueError("firmware design revision binding mismatch")
        target_values = (
            self.target_platform, self.mcu_family, self.mcu_exact_part,
            self.architecture, self.firmware_framework, self.build_system,
            self.toolchain_family,
        )
        if any(value is not None for value in target_values) and any(value is None for value in target_values):
            raise ValueError("firmware canonical target facts are incomplete")
        if (self.design_truth_id is None) != (self.design_truth_fingerprint is None):
            raise ValueError("firmware DesignTruth binding is incomplete")
        module_ids = tuple(item.module_id for item in self.modules)
        binding_ids = tuple(item.binding_id for item in self.bindings)
        task_ids = tuple(item.task_id for item in self.tasks)
        verification_ids = tuple(item.verification_id for item in self.verification_requirements)
        all_ids = (*module_ids, *binding_ids, *task_ids, *verification_ids)
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("firmware module ids must be unique")
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("firmware binding ids must be unique")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("firmware task ids must be unique")
        if len(verification_ids) != len(set(verification_ids)):
            raise ValueError("firmware verification ids must be unique")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("firmware design ids must be globally unique")
        modules = set(module_ids)
        bindings = set(binding_ids)
        tasks = set(task_ids)
        verifications = set(verification_ids)
        interfaces = set(self.software_interfaces)
        for module in self.modules:
            if module.firmware_spec_id != self.firmware_spec_id:
                raise ValueError("firmware module identity mismatch")
            if not set(module.dependencies) <= modules:
                raise ValueError("firmware module dependency reference is invalid")
            if not set(module.hardware_bindings) <= bindings:
                raise ValueError("firmware module binding reference is invalid")
            if not set(module.verification_refs) <= verifications:
                raise ValueError("firmware module verification reference is invalid")
        for binding in self.bindings:
            if binding.firmware_module_id not in modules:
                raise ValueError("firmware binding module reference is invalid")
            if binding.hardware_interface_id not in interfaces:
                raise ValueError("firmware binding interface reference is invalid")
        for task in self.tasks:
            if task.firmware_spec_id != self.firmware_spec_id:
                raise ValueError("firmware task identity mismatch")
            if task.module_id not in modules:
                raise ValueError("firmware task module reference is invalid")
            if not set(task.dependencies) <= tasks:
                raise ValueError("firmware task dependency reference is invalid")
            if not set(task.verification_refs) <= verifications:
                raise ValueError("firmware task verification reference is invalid")
        for verification in self.verification_requirements:
            if verification.target_module not in modules:
                raise ValueError("firmware verification module reference is invalid")
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("firmware design provenance identity mismatch")
        hardware_sources = tuple(
            item for item in self.provenance
            if item.source_type == "HARDWARE_DESIGN_SPECIFICATION"
            and item.source_id == self.hardware_design_id
        )
        workflow_sources = tuple(
            item for item in self.provenance
            if item.source_type == "ENGINEERING_WORKFLOW"
            and item.source_id == self.workflow_id
        )
        if (
            len(hardware_sources) != 1
            or hardware_sources[0].source_fingerprint != self.hardware_design_fingerprint
            or len(workflow_sources) != 1
            or workflow_sources[0].source_fingerprint != self.workflow_fingerprint
        ):
            raise ValueError("firmware design source provenance is incomplete")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SimulationTargetType(StrEnum):
    CONTROLLER = "CONTROLLER"
    SENSOR = "SENSOR"
    ACTUATOR = "ACTUATOR"
    COMMUNICATION = "COMMUNICATION"
    POWER = "POWER"
    FIRMWARE_MODULE = "FIRMWARE_MODULE"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class SimulationScenarioType(StrEnum):
    FUNCTIONAL_TEST = "FUNCTIONAL_TEST"
    INTERFACE_TEST = "INTERFACE_TEST"
    CONTROL_TEST = "CONTROL_TEST"
    COMMUNICATION_TEST = "COMMUNICATION_TEST"
    PERFORMANCE_TEST = "PERFORMANCE_TEST"
    UNKNOWN = "UNKNOWN"


class SimulationEvidenceType(StrEnum):
    LOG = "LOG"
    TRACE = "TRACE"
    WAVEFORM = "WAVEFORM"
    STATE_RESULT = "STATE_RESULT"
    MEASUREMENT = "MEASUREMENT"
    REPORT = "REPORT"
    UNKNOWN = "UNKNOWN"


class SimulationStatus(StrEnum):
    PLANNED = "PLANNED"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


class SimulationDesignIntent(StrEnum):
    CONTROL_VALIDATION = "CONTROL_VALIDATION"
    INTERFACE_VALIDATION = "INTERFACE_VALIDATION"
    FUNCTIONAL_VALIDATION = "FUNCTIONAL_VALIDATION"
    PERFORMANCE_VALIDATION = "PERFORMANCE_VALIDATION"
    UNKNOWN = "UNKNOWN"


def _simulation_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


def _simulation_texts(value: object, field: str, maximum: int = 4096) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    return tuple(text(item, field.removesuffix("s"), maximum) for item in tuple_values(value, field))


def _simulation_refs(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _simulation_source_reference(value: object) -> str:
    reference = ident(value, "source_reference")
    if "://" in reference:
        raise ValueError("source_reference is unsafe")
    return reference


def _simulation_revision(
    hardware_design_id: str,
    hardware_design_fingerprint: str,
    firmware_spec_id: str,
    firmware_spec_fingerprint: str,
    workflow_id: str,
    workflow_fingerprint: str,
) -> str:
    digest = hashlib.sha256(
        f"simulation-design-v1|{hardware_design_id}|{hardware_design_fingerprint}|"
        f"{firmware_spec_id}|{firmware_spec_fingerprint}|{workflow_id}|{workflow_fingerprint}".encode()
    ).hexdigest()[:24]
    return f"revision-{digest}"


class SimulationModel(Fingerprinted):
    model_id: str
    simulation_spec_id: str
    name: str
    model_type: SimulationTargetType
    source_reference: str
    parameters: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("model_id", "simulation_spec_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def _source_reference(cls, value):
        return _simulation_source_reference(value)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        return text(value, "name", 4096)

    @field_validator("model_type", mode="before")
    @classmethod
    def _model_type(cls, value):
        return value if isinstance(value, SimulationTargetType) else SimulationTargetType(value)

    @field_validator("parameters", mode="before")
    @classmethod
    def _parameters(cls, value):
        return _simulation_texts(value, "parameters", 2048)

    @field_validator("interfaces", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _simulation_refs(value, info.field_name)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SimulationScenario(Fingerprinted):
    scenario_id: str
    simulation_spec_id: str
    title: str
    scenario_type: SimulationScenarioType
    inputs: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    related_models: tuple[str, ...] = ()
    acceptance_refs: tuple[str, ...] = ()

    @field_validator("scenario_id", "simulation_spec_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return text(value, "title", 4096)

    @field_validator("scenario_type", mode="before")
    @classmethod
    def _scenario_type(cls, value):
        return value if isinstance(value, SimulationScenarioType) else SimulationScenarioType(value)

    @field_validator("inputs", "expected_outputs", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return _simulation_texts(value, info.field_name, 4096)

    @field_validator("related_models", "acceptance_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _simulation_refs(value, info.field_name)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SimulationEvidenceRequirement(Fingerprinted):
    evidence_id: str
    simulation_spec_id: str
    scenario_id: str
    evidence_type: SimulationEvidenceType
    description: str
    acceptance_refs: tuple[str, ...] = ()

    @field_validator("evidence_id", "simulation_spec_id", "scenario_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("evidence_type", mode="before")
    @classmethod
    def _evidence_type(cls, value):
        return value if isinstance(value, SimulationEvidenceType) else SimulationEvidenceType(value)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value):
        return text(value, "description", 4096)

    @field_validator("acceptance_refs", mode="before")
    @classmethod
    def _refs(cls, value):
        return _simulation_refs(value, "acceptance_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SimulationSpecification(Fingerprinted):
    simulation_spec_id: str
    project_id: str
    conversation_id: str
    hardware_design_id: str
    firmware_spec_id: str
    workflow_id: str
    hardware_design_fingerprint: str
    firmware_spec_fingerprint: str
    workflow_fingerprint: str
    title: str
    design_intent: SimulationDesignIntent = SimulationDesignIntent.UNKNOWN
    status: SimulationStatus = SimulationStatus.PLANNED
    targets: tuple[str, ...] = ()
    models: tuple[SimulationModel, ...] = ()
    scenarios: tuple[SimulationScenario, ...] = ()
    evidence_requirements: tuple[SimulationEvidenceRequirement, ...] = ()
    verification_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()
    design_revision: str

    @field_validator(
        "simulation_spec_id", "project_id", "conversation_id", "hardware_design_id",
        "firmware_spec_id", "workflow_id", "design_revision", mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator(
        "hardware_design_fingerprint", "firmware_spec_fingerprint", "workflow_fingerprint", mode="before",
    )
    @classmethod
    def _fingerprints(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value):
        return text(value, "title", 4096)

    @field_validator("design_intent", mode="before")
    @classmethod
    def _design_intent(cls, value):
        return value if isinstance(value, SimulationDesignIntent) else SimulationDesignIntent(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, SimulationStatus) else SimulationStatus(value)

    @field_validator("targets", "verification_refs", "source_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _simulation_refs(value, info.field_name)

    @field_validator("models", mode="before")
    @classmethod
    def _models(cls, value):
        return _simulation_objects(value, "models", SimulationModel)

    @field_validator("scenarios", mode="before")
    @classmethod
    def _scenarios(cls, value):
        return _simulation_objects(value, "scenarios", SimulationScenario)

    @field_validator("evidence_requirements", mode="before")
    @classmethod
    def _evidence_requirements(cls, value):
        return _simulation_objects(value, "evidence_requirements", SimulationEvidenceRequirement)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _simulation_objects(value, "provenance", PlanProvenance)

    @model_validator(mode="after")
    def _bound(self):
        if self.design_revision != _simulation_revision(
            self.hardware_design_id,
            self.hardware_design_fingerprint,
            self.firmware_spec_id,
            self.firmware_spec_fingerprint,
            self.workflow_id,
            self.workflow_fingerprint,
        ):
            raise ValueError("simulation design revision binding mismatch")
        model_ids = tuple(item.model_id for item in self.models)
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        evidence_ids = tuple(item.evidence_id for item in self.evidence_requirements)
        all_ids = (*model_ids, *scenario_ids, *evidence_ids)
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("simulation model ids must be unique")
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("simulation scenario ids must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("simulation evidence ids must be unique")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("simulation design ids must be globally unique")
        models = set(model_ids)
        if not set(self.targets) <= models:
            raise ValueError("simulation target reference is invalid")
        for model in self.models:
            if model.simulation_spec_id != self.simulation_spec_id:
                raise ValueError("simulation model identity mismatch")
        for scenario in self.scenarios:
            if scenario.simulation_spec_id != self.simulation_spec_id:
                raise ValueError("simulation scenario identity mismatch")
            if not set(scenario.related_models) <= models:
                raise ValueError("simulation scenario model reference is invalid")
        scenarios = set(scenario_ids)
        for evidence in self.evidence_requirements:
            if evidence.simulation_spec_id != self.simulation_spec_id:
                raise ValueError("simulation evidence identity mismatch")
            if evidence.scenario_id not in scenarios:
                raise ValueError("simulation evidence scenario reference is invalid")
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("simulation design provenance identity mismatch")
        hardware_sources = tuple(
            item for item in self.provenance
            if item.source_type == "HARDWARE_DESIGN_SPECIFICATION"
            and item.source_id == self.hardware_design_id
        )
        firmware_sources = tuple(
            item for item in self.provenance
            if item.source_type == "FIRMWARE_SPECIFICATION"
            and item.source_id == self.firmware_spec_id
        )
        workflow_sources = tuple(
            item for item in self.provenance
            if item.source_type == "ENGINEERING_WORKFLOW"
            and item.source_id == self.workflow_id
        )
        if (
            len(hardware_sources) != 1
            or hardware_sources[0].source_fingerprint != self.hardware_design_fingerprint
            or len(firmware_sources) != 1
            or firmware_sources[0].source_fingerprint != self.firmware_spec_fingerprint
            or len(workflow_sources) != 1
            or workflow_sources[0].source_fingerprint != self.workflow_fingerprint
        ):
            raise ValueError("simulation design source provenance is incomplete")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class VerificationStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class VerificationCaseType(StrEnum):
    FUNCTIONAL = "FUNCTIONAL"
    INTERFACE = "INTERFACE"
    CONTROL = "CONTROL"
    COMMUNICATION = "COMMUNICATION"
    PERFORMANCE = "PERFORMANCE"
    DESIGN_REVIEW = "DESIGN_REVIEW"


class VerificationEvidenceType(StrEnum):
    LOG = "LOG"
    TRACE = "TRACE"
    WAVEFORM = "WAVEFORM"
    MEASUREMENT = "MEASUREMENT"
    SIMULATION_REPORT = "SIMULATION_REPORT"
    TEST_REPORT = "TEST_REPORT"
    DOCUMENT = "DOCUMENT"
    UNKNOWN = "UNKNOWN"


class VerificationIntent(StrEnum):
    FUNCTIONAL_VALIDATION = "FUNCTIONAL_VALIDATION"
    INTERFACE_VALIDATION = "INTERFACE_VALIDATION"
    CONTROL_VALIDATION = "CONTROL_VALIDATION"
    PERFORMANCE_VALIDATION = "PERFORMANCE_VALIDATION"
    COMPLIANCE_VALIDATION = "COMPLIANCE_VALIDATION"


def _verification_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


def _verification_texts(value: object, field: str, maximum: int = 4096) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    return tuple(text(item, field.removesuffix("s"), maximum) for item in tuple_values(value, field))


def _verification_refs(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _verification_revision(
    hardware_design_id: str,
    hardware_design_fingerprint: str,
    firmware_spec_id: str,
    firmware_spec_fingerprint: str,
    simulation_spec_id: str,
    simulation_spec_fingerprint: str,
    workflow_id: str,
    workflow_fingerprint: str,
) -> str:
    digest = hashlib.sha256(
        f"verification-plan-v1|{hardware_design_id}|{hardware_design_fingerprint}|"
        f"{firmware_spec_id}|{firmware_spec_fingerprint}|"
        f"{simulation_spec_id}|{simulation_spec_fingerprint}|"
        f"{workflow_id}|{workflow_fingerprint}".encode()
    ).hexdigest()[:24]
    return f"revision-{digest}"


class VerificationCase(Fingerprinted):
    case_id: str
    verification_id: str
    title: str
    case_type: VerificationCaseType
    verification_intent: VerificationIntent | None = None
    target: str
    related_requirement_refs: tuple[str, ...] = ()
    related_design_refs: tuple[str, ...] = ()
    method: str
    expected_behavior: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    status: VerificationStatus = VerificationStatus.PLANNED

    @field_validator("case_id", "verification_id", "target", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("title", "method", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("case_type", mode="before")
    @classmethod
    def _case_type(cls, value):
        return value if isinstance(value, VerificationCaseType) else VerificationCaseType(value)

    @field_validator("verification_intent", mode="before")
    @classmethod
    def _intent(cls, value):
        if value is None or isinstance(value, VerificationIntent):
            return value
        return VerificationIntent(value)

    @field_validator("related_requirement_refs", "related_design_refs", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _verification_refs(value, info.field_name)

    @field_validator("expected_behavior", mode="before")
    @classmethod
    def _expected_behavior(cls, value):
        return _verification_texts(value, "expected_behavior", 4096)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, VerificationStatus) else VerificationStatus(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class VerificationEvidenceRequirement(Fingerprinted):
    evidence_id: str
    verification_id: str
    case_id: str
    evidence_type: VerificationEvidenceType = VerificationEvidenceType.UNKNOWN
    description: str
    source_reference: str
    acceptance_refs: tuple[str, ...] = ()

    @field_validator("evidence_id", "verification_id", "case_id", "source_reference", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("evidence_type", mode="before")
    @classmethod
    def _evidence_type(cls, value):
        return value if isinstance(value, VerificationEvidenceType) else VerificationEvidenceType(value)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value):
        return text(value, "description", 4096)

    @field_validator("acceptance_refs", mode="before")
    @classmethod
    def _acceptance_refs(cls, value):
        return _verification_refs(value, "acceptance_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class VerificationAcceptanceMapping(Fingerprinted):
    mapping_id: str
    verification_id: str
    case_id: str
    requirement_ref: str
    acceptance_refs: tuple[str, ...] = ()

    @field_validator("mapping_id", "verification_id", "case_id", "requirement_ref", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("acceptance_refs", mode="before")
    @classmethod
    def _acceptance_refs(cls, value):
        return _verification_refs(value, "acceptance_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class VerificationSpecification(Fingerprinted):
    schema_version: Literal["1.0"] = "1.0"
    verification_id: str
    project_id: str
    conversation_id: str
    hardware_design_id: str
    firmware_spec_id: str
    simulation_spec_id: str
    workflow_id: str
    hardware_design_fingerprint: str
    firmware_spec_fingerprint: str
    simulation_spec_fingerprint: str
    workflow_fingerprint: str
    design_revision: str
    verification_intent: VerificationIntent | None = None
    cases: tuple[VerificationCase, ...] = ()
    evidence_requirements: tuple[VerificationEvidenceRequirement, ...] = ()
    acceptance_mapping: tuple[VerificationAcceptanceMapping, ...] = ()
    status: VerificationStatus = VerificationStatus.PLANNED
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator(
        "verification_id", "project_id", "conversation_id", "hardware_design_id",
        "firmware_spec_id", "simulation_spec_id", "workflow_id", "design_revision", mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator(
        "hardware_design_fingerprint", "firmware_spec_fingerprint",
        "simulation_spec_fingerprint", "workflow_fingerprint", mode="before",
    )
    @classmethod
    def _fingerprints(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("verification_intent", mode="before")
    @classmethod
    def _intent(cls, value):
        if value is None or isinstance(value, VerificationIntent):
            return value
        return VerificationIntent(value)

    @field_validator("cases", mode="before")
    @classmethod
    def _cases(cls, value):
        return _verification_objects(value, "cases", VerificationCase)

    @field_validator("evidence_requirements", mode="before")
    @classmethod
    def _evidence_requirements(cls, value):
        return _verification_objects(value, "evidence_requirements", VerificationEvidenceRequirement)

    @field_validator("acceptance_mapping", mode="before")
    @classmethod
    def _acceptance_mapping(cls, value):
        return _verification_objects(value, "acceptance_mapping", VerificationAcceptanceMapping)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, VerificationStatus) else VerificationStatus(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _verification_objects(value, "provenance", PlanProvenance)

    @model_validator(mode="after")
    def _bound(self):
        if self.design_revision != _verification_revision(
            self.hardware_design_id,
            self.hardware_design_fingerprint,
            self.firmware_spec_id,
            self.firmware_spec_fingerprint,
            self.simulation_spec_id,
            self.simulation_spec_fingerprint,
            self.workflow_id,
            self.workflow_fingerprint,
        ):
            raise ValueError("verification design revision binding mismatch")
        case_ids = tuple(item.case_id for item in self.cases)
        evidence_ids = tuple(item.evidence_id for item in self.evidence_requirements)
        mapping_ids = tuple(item.mapping_id for item in self.acceptance_mapping)
        all_ids = (*case_ids, *evidence_ids, *mapping_ids)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("verification case ids must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("verification evidence ids must be unique")
        if len(mapping_ids) != len(set(mapping_ids)):
            raise ValueError("verification mapping ids must be unique")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("verification ids must be globally unique")
        cases = set(case_ids)
        for case in self.cases:
            if case.verification_id != self.verification_id:
                raise ValueError("verification case identity mismatch")
        for evidence in self.evidence_requirements:
            if evidence.verification_id != self.verification_id:
                raise ValueError("verification evidence identity mismatch")
            if evidence.case_id not in cases:
                raise ValueError("verification evidence case reference is invalid")
        for mapping in self.acceptance_mapping:
            if mapping.verification_id != self.verification_id:
                raise ValueError("verification mapping identity mismatch")
            if mapping.case_id not in cases:
                raise ValueError("verification mapping case reference is invalid")
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("verification provenance identity mismatch")
        expected_sources = (
            ("HARDWARE_DESIGN_SPECIFICATION", self.hardware_design_id, self.hardware_design_fingerprint),
            ("FIRMWARE_SPECIFICATION", self.firmware_spec_id, self.firmware_spec_fingerprint),
            ("SIMULATION_SPECIFICATION", self.simulation_spec_id, self.simulation_spec_fingerprint),
            ("ENGINEERING_WORKFLOW", self.workflow_id, self.workflow_fingerprint),
        )
        for source_type, source_id, source_fingerprint in expected_sources:
            sources = tuple(
                item for item in self.provenance
                if item.source_type == source_type and item.source_id == source_id
            )
            if len(sources) != 1 or sources[0].source_fingerprint != source_fingerprint:
                raise ValueError("verification source provenance is incomplete")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ImplementationStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class ImplementationTaskType(StrEnum):
    HARDWARE_CONFIGURATION = "HARDWARE_CONFIGURATION"
    FIRMWARE_MODULE = "FIRMWARE_MODULE"
    DRIVER_IMPLEMENTATION = "DRIVER_IMPLEMENTATION"
    INTERFACE_IMPLEMENTATION = "INTERFACE_IMPLEMENTATION"
    CONTROL_ALGORITHM = "CONTROL_ALGORITHM"
    COMMUNICATION = "COMMUNICATION"
    CONFIGURATION = "CONFIGURATION"
    TEST_PREPARATION = "TEST_PREPARATION"
    DOCUMENTATION = "DOCUMENTATION"


class ImplementationIntent(StrEnum):
    MODULE_IMPLEMENTATION = "MODULE_IMPLEMENTATION"
    INTERFACE_ADAPTATION = "INTERFACE_ADAPTATION"
    DRIVER_DEVELOPMENT = "DRIVER_DEVELOPMENT"
    CONTROL_IMPLEMENTATION = "CONTROL_IMPLEMENTATION"
    CONFIGURATION_SETUP = "CONFIGURATION_SETUP"
    TEST_PREPARATION = "TEST_PREPARATION"
    DOCUMENTATION = "DOCUMENTATION"


class ImplementationArtifactType(StrEnum):
    SOURCE_CODE = "SOURCE_CODE"
    CONFIGURATION = "CONFIGURATION"
    SCHEMATIC = "SCHEMATIC"
    PCB = "PCB"
    DOCUMENT = "DOCUMENT"
    TEST_ASSET = "TEST_ASSET"
    SIMULATION_ASSET = "SIMULATION_ASSET"
    UNKNOWN = "UNKNOWN"


def _implementation_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


def _implementation_texts(value: object, field: str, maximum: int = 4096) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    return tuple(text(item, field.removesuffix("s"), maximum) for item in tuple_values(value, field))


def _implementation_refs(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _implementation_revision(
    hardware_design_id: str,
    hardware_design_fingerprint: str,
    firmware_spec_id: str,
    firmware_spec_fingerprint: str,
    simulation_spec_id: str,
    simulation_spec_fingerprint: str,
    verification_id: str,
    verification_fingerprint: str,
    workflow_id: str,
    workflow_fingerprint: str,
) -> str:
    digest = hashlib.sha256(
        f"implementation-planning-v1|{hardware_design_id}|{hardware_design_fingerprint}|"
        f"{firmware_spec_id}|{firmware_spec_fingerprint}|"
        f"{simulation_spec_id}|{simulation_spec_fingerprint}|"
        f"{verification_id}|{verification_fingerprint}|"
        f"{workflow_id}|{workflow_fingerprint}".encode()
    ).hexdigest()[:24]
    return f"revision-{digest}"


class ImplementationTask(Fingerprinted):
    task_id: str
    implementation_id: str
    title: str
    task_type: ImplementationTaskType
    implementation_intent: ImplementationIntent
    description: str
    source_refs: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    acceptance_refs: tuple[str, ...] = ()
    status: ImplementationStatus = ImplementationStatus.PLANNED

    @field_validator("task_id", "implementation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("title", "description", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("task_type", mode="before")
    @classmethod
    def _task_type(cls, value):
        return value if isinstance(value, ImplementationTaskType) else ImplementationTaskType(value)

    @field_validator("implementation_intent", mode="before")
    @classmethod
    def _intent(cls, value):
        return value if isinstance(value, ImplementationIntent) else ImplementationIntent(value)

    @field_validator(
        "source_refs", "dependencies", "expected_artifacts", "verification_refs", "acceptance_refs",
        mode="before",
    )
    @classmethod
    def _refs(cls, value, info):
        return _implementation_refs(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, ImplementationStatus) else ImplementationStatus(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ExpectedArtifact(Fingerprinted):
    artifact_id: str
    implementation_id: str
    artifact_type: ImplementationArtifactType
    name: str
    description: str
    source_refs: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()

    @field_validator("artifact_id", "implementation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("artifact_type", mode="before")
    @classmethod
    def _artifact_type(cls, value):
        return value if isinstance(value, ImplementationArtifactType) else ImplementationArtifactType(value)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("source_refs", "verification_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _implementation_refs(value, info.field_name)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ImplementationSpecification(Fingerprinted):
    implementation_id: str
    project_id: str
    conversation_id: str
    hardware_design_id: str
    firmware_spec_id: str
    simulation_spec_id: str
    verification_id: str
    workflow_id: str
    hardware_design_fingerprint: str
    firmware_spec_fingerprint: str
    simulation_spec_fingerprint: str
    verification_fingerprint: str
    workflow_fingerprint: str
    design_revision: str
    tasks: tuple[ImplementationTask, ...] = ()
    expected_artifacts: tuple[ExpectedArtifact, ...] = ()
    dependencies: tuple[str, ...] = ()
    verification_refs: tuple[str, ...] = ()
    status: ImplementationStatus = ImplementationStatus.PLANNED
    provenance: tuple[PlanProvenance, ...] = ()
    design_truth_id: str | None = None
    design_truth_fingerprint: str | None = None
    build_configuration_id: str | None = None
    build_configuration_fingerprint: str | None = None

    @field_validator(
        "implementation_id", "project_id", "conversation_id", "hardware_design_id",
        "firmware_spec_id", "simulation_spec_id", "verification_id", "workflow_id", "design_revision",
        mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator(
        "hardware_design_fingerprint", "firmware_spec_fingerprint", "simulation_spec_fingerprint",
        "verification_fingerprint", "workflow_fingerprint", mode="before",
    )
    @classmethod
    def _fingerprints(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("tasks", mode="before")
    @classmethod
    def _tasks(cls, value):
        return _implementation_objects(value, "tasks", ImplementationTask)

    @field_validator("expected_artifacts", mode="before")
    @classmethod
    def _expected_artifacts(cls, value):
        return _implementation_objects(value, "expected_artifacts", ExpectedArtifact)

    @field_validator("dependencies", "verification_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _implementation_refs(value, info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, ImplementationStatus) else ImplementationStatus(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _implementation_objects(value, "provenance", PlanProvenance)

    @field_validator("design_truth_id", "build_configuration_id", mode="before")
    @classmethod
    def _optional_ids(cls, value, info):
        return None if value is None else ident(value, info.field_name)

    @field_validator("design_truth_fingerprint", "build_configuration_fingerprint", mode="before")
    @classmethod
    def _optional_fingerprints(cls, value, info):
        return None if value is None else fp(value, info.field_name)

    @model_validator(mode="after")
    def _bound(self):
        if self.design_revision != _implementation_revision(
            self.hardware_design_id,
            self.hardware_design_fingerprint,
            self.firmware_spec_id,
            self.firmware_spec_fingerprint,
            self.simulation_spec_id,
            self.simulation_spec_fingerprint,
            self.verification_id,
            self.verification_fingerprint,
            self.workflow_id,
            self.workflow_fingerprint,
        ):
            raise ValueError("implementation design revision binding mismatch")
        if (self.design_truth_id is None) != (self.design_truth_fingerprint is None):
            raise ValueError("implementation DesignTruth binding is incomplete")
        if (self.build_configuration_id is None) != (self.build_configuration_fingerprint is None):
            raise ValueError("implementation BuildConfiguration binding is incomplete")
        task_ids = tuple(item.task_id for item in self.tasks)
        artifact_ids = tuple(item.artifact_id for item in self.expected_artifacts)
        all_ids = (*task_ids, *artifact_ids)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("implementation task ids must be unique")
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("implementation artifact ids must be unique")
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("implementation ids must be globally unique")
        tasks = set(task_ids)
        artifacts = set(artifact_ids)
        verifications = set(self.verification_refs)
        if not set(self.dependencies) <= tasks:
            raise ValueError("implementation dependency reference is invalid")
        for task in self.tasks:
            if task.implementation_id != self.implementation_id:
                raise ValueError("implementation task identity mismatch")
            if task.task_id in task.dependencies:
                raise ValueError("implementation task cannot depend on itself")
            if not set(task.dependencies) <= tasks:
                raise ValueError("implementation task dependency reference is invalid")
            if not set(task.expected_artifacts) <= artifacts:
                raise ValueError("implementation task artifact reference is invalid")
            if not set(task.verification_refs) <= verifications:
                raise ValueError("implementation task verification reference is invalid")
        for artifact in self.expected_artifacts:
            if artifact.implementation_id != self.implementation_id:
                raise ValueError("implementation artifact identity mismatch")
            if not set(artifact.verification_refs) <= verifications:
                raise ValueError("implementation artifact verification reference is invalid")
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("implementation provenance identity mismatch")
        expected_sources = (
            ("HARDWARE_DESIGN_SPECIFICATION", self.hardware_design_id, self.hardware_design_fingerprint),
            ("FIRMWARE_SPECIFICATION", self.firmware_spec_id, self.firmware_spec_fingerprint),
            ("SIMULATION_SPECIFICATION", self.simulation_spec_id, self.simulation_spec_fingerprint),
            ("VERIFICATION_SPECIFICATION", self.verification_id, self.verification_fingerprint),
            ("ENGINEERING_WORKFLOW", self.workflow_id, self.workflow_fingerprint),
        )
        for source_type, source_id, source_fingerprint in expected_sources:
            sources = tuple(
                item for item in self.provenance
                if item.source_type == source_type and item.source_id == source_id
            )
            if len(sources) != 1 or sources[0].source_fingerprint != source_fingerprint:
                raise ValueError("implementation source provenance is incomplete")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class CapabilityCategory(StrEnum):
    IMPLEMENTATION = "IMPLEMENTATION"
    BUILD = "BUILD"
    SIMULATION = "SIMULATION"
    VERIFICATION = "VERIFICATION"
    HARDWARE = "HARDWARE"
    FIRMWARE = "FIRMWARE"
    ARTIFACT = "ARTIFACT"
    SYSTEM = "SYSTEM"


class CapabilityRiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PLANNED = "PLANNED"
    RESTRICTED = "RESTRICTED"
    DISABLED = "DISABLED"


class ExecutionIntentType(StrEnum):
    CREATE_ARTIFACT = "CREATE_ARTIFACT"
    GENERATE_SOURCE = "GENERATE_SOURCE"
    BUILD_PROJECT = "BUILD_PROJECT"
    RUN_SIMULATION = "RUN_SIMULATION"
    RUN_VERIFICATION = "RUN_VERIFICATION"
    FLASH_DEVICE = "FLASH_DEVICE"
    MODIFY_HARDWARE = "MODIFY_HARDWARE"


class ExecutionIntentStatus(StrEnum):
    PLANNED = "PLANNED"


class CapabilityApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ExecutionRequestStatus(StrEnum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class AuthorizationDecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExecutionAuthorizationStatus(StrEnum):
    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    BLOCKED = "BLOCKED"


class FirmwareSourceProposalStatus(StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FirmwareSourceProposalDecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FirmwareEvidenceParseStatus(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"


class FirmwareSourceGenerationStatus(StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    QUEUED = "QUEUED"
    REJECTED = "REJECTED"


_CAPABILITY_IDS = frozenset(
    {
        "ARTIFACT_CREATION",
        "FIRMWARE_CODE_GENERATION",
        "PROJECT_BUILD",
        "SIMULATION_EXECUTION",
        "VERIFICATION_EXECUTION",
        "FLASH_DEVICE",
        "HARDWARE_MODIFICATION",
    }
)


def _capability_id(value: object, field: str) -> str:
    value = ident(value, field)
    if value not in _CAPABILITY_IDS:
        raise ValueError(f"{field} references unknown capability")
    return value


def _capability_refs(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if not refs:
        raise ValueError(f"{field} must not be empty")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _known_capability_refs(value: object, field: str) -> tuple[str, ...]:
    refs = _capability_refs(value, field)
    if not set(refs) <= _CAPABILITY_IDS:
        raise ValueError(f"{field} references unknown capability")
    return refs


def _references(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _capability_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


class EngineeringCapability(Fingerprinted):
    capability_id: str
    name: str
    category: CapabilityCategory
    description: str
    risk_level: CapabilityRiskLevel
    requires_approval: bool
    status: CapabilityStatus
    source_refs: tuple[str, ...]

    @field_validator("capability_id", mode="before")
    @classmethod
    def _id(cls, value):
        return _capability_id(value, "capability_id")

    @field_validator("name", "description", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value):
        return value if isinstance(value, CapabilityCategory) else CapabilityCategory(value)

    @field_validator("risk_level", mode="before")
    @classmethod
    def _risk(cls, value):
        return value if isinstance(value, CapabilityRiskLevel) else CapabilityRiskLevel(value)

    @field_validator("requires_approval", mode="before")
    @classmethod
    def _approval_flag(cls, value):
        if type(value) is not bool:
            raise TypeError("requires_approval is invalid")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, CapabilityStatus) else CapabilityStatus(value)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _sources(cls, value):
        return _capability_refs(value, "source_refs")

    @model_validator(mode="after")
    def _approval_boundary(self):
        if self.risk_level in {CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL} and not self.requires_approval:
            raise ValueError("high risk capability requires approval")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ImplementationCapabilityBinding(Fingerprinted):
    binding_id: str
    implementation_task_id: str
    capability_id: str
    required: bool
    reason: str

    @field_validator("binding_id", "implementation_task_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("capability_id", mode="before")
    @classmethod
    def _capability(cls, value):
        return _capability_id(value, "capability_id")

    @field_validator("required", mode="before")
    @classmethod
    def _required(cls, value):
        if type(value) is not bool:
            raise TypeError("required is invalid")
        return value

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value):
        return text(value, "reason", 4096)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ExecutionIntent(Fingerprinted):
    intent_id: str
    intent_type: ExecutionIntentType
    source_task_refs: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    status: ExecutionIntentStatus = ExecutionIntentStatus.PLANNED

    @field_validator("intent_id", mode="before")
    @classmethod
    def _id(cls, value):
        return ident(value, "intent_id")

    @field_validator("intent_type", mode="before")
    @classmethod
    def _intent_type(cls, value):
        return value if isinstance(value, ExecutionIntentType) else ExecutionIntentType(value)

    @field_validator("source_task_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _capability_refs(value, info.field_name)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value):
        return _known_capability_refs(value, "required_capabilities")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, ExecutionIntentStatus) else ExecutionIntentStatus(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class CapabilityApprovalRequirement(Fingerprinted):
    approval_id: str
    intent_id: str
    required_capabilities: tuple[str, ...]
    risk_level: CapabilityRiskLevel
    approval_status: CapabilityApprovalStatus

    @field_validator("approval_id", "intent_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value):
        return _known_capability_refs(value, "required_capabilities")

    @field_validator("risk_level", mode="before")
    @classmethod
    def _risk(cls, value):
        return value if isinstance(value, CapabilityRiskLevel) else CapabilityRiskLevel(value)

    @field_validator("approval_status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, CapabilityApprovalStatus) else CapabilityApprovalStatus(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class CapabilityEvaluation(Fingerprinted):
    evaluation_id: str
    project_id: str
    implementation_id: str
    implementation_fingerprint: str
    capabilities: tuple[EngineeringCapability, ...]
    bindings: tuple[ImplementationCapabilityBinding, ...]
    approval_requirements: tuple[CapabilityApprovalRequirement, ...]

    @field_validator("evaluation_id", "project_id", "implementation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("implementation_fingerprint", mode="before")
    @classmethod
    def _implementation_fingerprint(cls, value):
        return fp(value, "implementation_fingerprint")

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value):
        return _capability_objects(value, "capabilities", EngineeringCapability)

    @field_validator("bindings", mode="before")
    @classmethod
    def _bindings(cls, value):
        return _capability_objects(value, "bindings", ImplementationCapabilityBinding)

    @field_validator("approval_requirements", mode="before")
    @classmethod
    def _requirements(cls, value):
        return _capability_objects(value, "approval_requirements", CapabilityApprovalRequirement)

    @model_validator(mode="after")
    def _bound(self):
        capability_ids = tuple(item.capability_id for item in self.capabilities)
        binding_ids = tuple(item.binding_id for item in self.bindings)
        approval_ids = tuple(item.approval_id for item in self.approval_requirements)
        intent_ids = tuple(item.intent_id for item in self.approval_requirements)
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability ids must be unique")
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("capability binding ids must be unique")
        if len(approval_ids) != len(set(approval_ids)) or len(intent_ids) != len(set(intent_ids)):
            raise ValueError("capability approval bindings must be unique")
        capabilities = {item.capability_id: item for item in self.capabilities}
        for binding in self.bindings:
            if binding.capability_id not in capabilities:
                raise ValueError("capability binding references unknown capability")
            if not binding.required:
                raise ValueError("generated capability binding must be required")
        for capability in self.capabilities:
            task_refs = tuple(
                binding.implementation_task_id
                for binding in self.bindings
                if binding.capability_id == capability.capability_id
            )
            if not task_refs or capability.source_refs != tuple(dict.fromkeys(task_refs)):
                raise ValueError("capability source binding mismatch")
        risk_order = {
            CapabilityRiskLevel.LOW: 0,
            CapabilityRiskLevel.MEDIUM: 1,
            CapabilityRiskLevel.HIGH: 2,
            CapabilityRiskLevel.CRITICAL: 3,
        }
        for requirement in self.approval_requirements:
            if not set(requirement.required_capabilities) <= set(capabilities):
                raise ValueError("approval references unknown capability")
            selected = tuple(capabilities[item] for item in requirement.required_capabilities)
            if any(not item.requires_approval for item in selected):
                raise ValueError("approval references capability that does not require approval")
            expected_risk = max((item.risk_level for item in selected), key=risk_order.__getitem__)
            if requirement.risk_level is not expected_risk:
                raise ValueError("capability approval risk mismatch")
        approved_capabilities = {
            capability_id
            for requirement in self.approval_requirements
            for capability_id in requirement.required_capabilities
        }
        if any(
            capability.requires_approval
            and capability.capability_id not in approved_capabilities
            for capability in self.capabilities
        ):
            raise ValueError("capability requiring approval has no approval requirement")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ExecutionRequest(Fingerprinted):
    request_id: str
    intent_id: str
    capability_id: str
    project_id: str
    conversation_id: str
    requested_by: str
    request_reason: str
    risk_level: CapabilityRiskLevel
    status: ExecutionRequestStatus
    source_refs: tuple[str, ...]

    @field_validator(
        "request_id", "intent_id", "project_id", "conversation_id", "requested_by",
        mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("capability_id", mode="before")
    @classmethod
    def _capability(cls, value):
        return _capability_id(value, "capability_id")

    @field_validator("request_reason", mode="before")
    @classmethod
    def _reason(cls, value):
        return text(value, "request_reason", 4096)

    @field_validator("risk_level", mode="before")
    @classmethod
    def _risk(cls, value):
        return value if isinstance(value, CapabilityRiskLevel) else CapabilityRiskLevel(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, ExecutionRequestStatus) else ExecutionRequestStatus(value)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _sources(cls, value):
        return _capability_refs(value, "source_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ExecutionPolicy(Fingerprinted):
    policy_id: str
    capability_id: str
    risk_level: CapabilityRiskLevel
    requires_user_confirmation: bool
    requires_review: bool
    allowed: bool

    @field_validator("policy_id", mode="before")
    @classmethod
    def _id(cls, value):
        return ident(value, "policy_id")

    @field_validator("capability_id", mode="before")
    @classmethod
    def _capability(cls, value):
        return _capability_id(value, "capability_id")

    @field_validator("risk_level", mode="before")
    @classmethod
    def _risk(cls, value):
        return value if isinstance(value, CapabilityRiskLevel) else CapabilityRiskLevel(value)

    @field_validator(
        "requires_user_confirmation", "requires_review", "allowed", mode="before"
    )
    @classmethod
    def _flags(cls, value, info):
        if type(value) is not bool:
            raise TypeError(f"{info.field_name} is invalid")
        return value

    @model_validator(mode="after")
    def _review_boundary(self):
        if self.requires_review and not self.requires_user_confirmation:
            raise ValueError("review policy requires user confirmation")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class AuthorizationDecision(Fingerprinted):
    decision_id: str
    request_id: str
    decision: AuthorizationDecisionStatus
    reason: str
    approved_by: str | None = None
    timestamp_reference: datetime | None = None

    @field_validator("decision_id", "request_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("decision", mode="before")
    @classmethod
    def _decision(cls, value):
        return value if isinstance(value, AuthorizationDecisionStatus) else AuthorizationDecisionStatus(value)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value):
        return text(value, "reason", 4096)

    @field_validator("approved_by", mode="before")
    @classmethod
    def _reviewer(cls, value):
        return None if value is None else ident(value, "approved_by")

    @field_validator("timestamp_reference", mode="before")
    @classmethod
    def _timestamp(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("timestamp_reference is invalid") from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("timestamp_reference is invalid")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _pending_boundary(self):
        if self.decision is AuthorizationDecisionStatus.PENDING:
            if self.approved_by is not None or self.timestamp_reference is not None:
                raise ValueError("pending decision cannot have reviewer or timestamp")
        elif self.approved_by is None or self.timestamp_reference is None:
            raise ValueError("terminal decision requires reviewer and timestamp")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ExecutionAuthorization(Fingerprinted):
    authorization_id: str
    project_id: str
    conversation_id: str
    capability_evaluation_id: str
    requests: tuple[ExecutionRequest, ...]
    policies: tuple[ExecutionPolicy, ...]
    decisions: tuple[AuthorizationDecision, ...]
    status: ExecutionAuthorizationStatus

    @field_validator(
        "authorization_id", "project_id", "conversation_id", "capability_evaluation_id",
        mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("requests", mode="before")
    @classmethod
    def _requests(cls, value):
        return _capability_objects(value, "requests", ExecutionRequest)

    @field_validator("policies", mode="before")
    @classmethod
    def _policies(cls, value):
        return _capability_objects(value, "policies", ExecutionPolicy)

    @field_validator("decisions", mode="before")
    @classmethod
    def _decisions(cls, value):
        return _capability_objects(value, "decisions", AuthorizationDecision)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, ExecutionAuthorizationStatus) else ExecutionAuthorizationStatus(value)

    @model_validator(mode="after")
    def _authorization_boundary(self):
        if not self.requests:
            raise ValueError("execution authorization requires requests")
        request_ids = tuple(item.request_id for item in self.requests)
        policy_ids = tuple(item.policy_id for item in self.policies)
        policy_capabilities = tuple(item.capability_id for item in self.policies)
        decision_ids = tuple(item.decision_id for item in self.decisions)
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("execution request ids must be unique")
        if len(policy_ids) != len(set(policy_ids)) or len(policy_capabilities) != len(set(policy_capabilities)):
            raise ValueError("execution policies must be unique")
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("authorization decision ids must be unique")
        expected_capabilities = tuple(dict.fromkeys(item.capability_id for item in self.requests))
        if policy_capabilities != expected_capabilities:
            raise ValueError("execution policy capability binding mismatch")
        if tuple(item.request_id for item in self.decisions) != request_ids:
            raise ValueError("authorization decision request binding mismatch")
        policy_by_capability = {item.capability_id: item for item in self.policies}
        for request in self.requests:
            if request.project_id != self.project_id or request.conversation_id != self.conversation_id:
                raise ValueError("execution request scope binding mismatch")
            policy = policy_by_capability[request.capability_id]
            if request.risk_level is not policy.risk_level:
                raise ValueError("execution request policy risk mismatch")
        for request, decision in zip(self.requests, self.decisions, strict=True):
            expected_request_status = {
                AuthorizationDecisionStatus.PENDING: ExecutionRequestStatus.REQUESTED,
                AuthorizationDecisionStatus.APPROVED: ExecutionRequestStatus.AUTHORIZED,
                AuthorizationDecisionStatus.REJECTED: ExecutionRequestStatus.DENIED,
            }[decision.decision]
            if request.status is not expected_request_status:
                raise ValueError("pending authorization request decision status mismatch")
        if self.status is ExecutionAuthorizationStatus.PENDING:
            if not any(item.decision is AuthorizationDecisionStatus.PENDING for item in self.decisions):
                raise ValueError("pending authorization requires a pending decision")
        elif self.status is ExecutionAuthorizationStatus.AUTHORIZED:
            if any(not item.allowed for item in self.policies):
                raise ValueError("authorized state requires allowed policies")
            if any(item.status is not ExecutionRequestStatus.AUTHORIZED for item in self.requests):
                raise ValueError("authorized state requires authorized requests")
            if any(item.decision is not AuthorizationDecisionStatus.APPROVED for item in self.decisions):
                raise ValueError("authorized state requires approved decisions")
        elif (
            any(item.decision is AuthorizationDecisionStatus.PENDING for item in self.decisions)
            or not any(
                item.status in {ExecutionRequestStatus.DENIED, ExecutionRequestStatus.EXPIRED}
                for item in self.requests
            )
            or not any(
                item.decision is AuthorizationDecisionStatus.REJECTED
                for item in self.decisions
            )
        ):
            raise ValueError("blocked authorization requires terminal rejected state")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareSourceGenerationRequest(Fingerprinted):
    request_id: str
    source_request_id: str | None = None
    trace_id: str | None = None
    tenant_id: str
    project_id: str
    conversation_id: str
    requester: str
    firmware_spec_id: str
    firmware_spec_fingerprint: str
    implementation_id: str
    implementation_revision: str
    implementation_fingerprint: str
    implementation_task_id: str
    implementation_task_fingerprint: str
    firmware_module_id: str
    firmware_module_fingerprint: str
    execution_intent_id: str
    execution_intent_fingerprint: str
    authorization_id: str
    authorization_fingerprint: str
    authorization_request_id: str
    authorization_request_fingerprint: str
    authorization_decision_id: str
    authorization_decision_fingerprint: str
    capability_id: Literal["FIRMWARE_CODE_GENERATION"] = "FIRMWARE_CODE_GENERATION"
    source_refs: tuple[str, ...]
    verification_refs: tuple[str, ...] = ()

    @field_validator(
        "request_id", "source_request_id", "trace_id", "tenant_id", "project_id", "conversation_id", "requester",
        "firmware_spec_id", "implementation_id", "implementation_revision", "implementation_task_id",
        "firmware_module_id", "execution_intent_id", "authorization_id",
        "authorization_request_id", "authorization_decision_id", mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        if value is None and info.field_name in {"source_request_id", "trace_id"}:
            return None
        return ident(value, info.field_name)

    @field_validator(
        "firmware_spec_fingerprint", "implementation_fingerprint",
        "implementation_task_fingerprint", "firmware_module_fingerprint",
        "execution_intent_fingerprint", "authorization_fingerprint",
        "authorization_request_fingerprint", "authorization_decision_fingerprint",
        mode="before",
    )
    @classmethod
    def _fingerprints(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _sources(cls, value):
        return _capability_refs(value, "source_refs")

    @field_validator("verification_refs", mode="before")
    @classmethod
    def _verification_refs(cls, value):
        return _references(value, "verification_refs")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if self.source_request_id is None and self.trace_id is None:
            return (
                canonical(
                    self,
                    exclude={"fingerprint", "source_request_id", "trace_id"},
                ),
            )
        return ()


class FirmwareSourceFileProposal(Fingerprinted):
    relative_path: str
    content: str
    content_fingerprint: str
    size_bytes: int = Field(ge=1, le=64 * 1024)

    @field_validator("relative_path", mode="before")
    @classmethod
    def _path(cls, value):
        if not isinstance(value, str):
            raise TypeError("relative_path is invalid")
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        windows_reserved = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{index}" for index in range(1, 10)),
            *(f"LPT{index}" for index in range(1, 10)),
        }
        if (
            not normalized.startswith("generated/")
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in parts)
            or parts[-1].rsplit(".", 1)[-1].casefold() not in {"c", "h"}
            or any(part.split(".", 1)[0].upper() in windows_reserved for part in parts)
            or len(normalized) > 255
        ):
            raise ValueError("relative_path is unsafe")
        return normalized

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("content is invalid")
        if len(value.encode("utf-8")) > 64 * 1024:
            raise ValueError("content exceeds source file limit")
        return value

    @field_validator("content_fingerprint", mode="before")
    @classmethod
    def _content_fingerprint(cls, value):
        return fp(value, "content_fingerprint")

    @model_validator(mode="after")
    def _content_binding(self):
        encoded = self.content.encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.content_fingerprint != expected or self.size_bytes != len(encoded):
            raise ValueError("source content fingerprint or size mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareSourceProposal(Fingerprinted):
    proposal_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    requester: str
    generation_request_id: str
    generation_request_fingerprint: str
    firmware_module_id: str
    module_name: str
    module_responsibility: str
    files: tuple[FirmwareSourceFileProposal, ...]
    verification_refs: tuple[str, ...]
    validation_fingerprint: str
    source_refs: tuple[str, ...]
    status: FirmwareSourceProposalStatus = FirmwareSourceProposalStatus.AWAITING_APPROVAL
    firmware_evidence_parse: FirmwareEvidenceParseStatus = FirmwareEvidenceParseStatus.UNAVAILABLE

    @field_validator(
        "proposal_id", "tenant_id", "project_id", "conversation_id", "requester",
        "generation_request_id", "firmware_module_id", mode="before",
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("module_name", "module_responsibility", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("generation_request_fingerprint", "validation_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value, info):
        return fp(value, info.field_name)

    @field_validator("files", mode="before")
    @classmethod
    def _files(cls, value):
        return _capability_objects(value, "files", FirmwareSourceFileProposal)

    @field_validator("source_refs", mode="before")
    @classmethod
    def _sources(cls, value):
        return _capability_refs(value, "source_refs")

    @field_validator("verification_refs", mode="before")
    @classmethod
    def _verification_refs(cls, value):
        return _references(value, "verification_refs")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, FirmwareSourceProposalStatus) else FirmwareSourceProposalStatus(value)

    @field_validator("firmware_evidence_parse", mode="before")
    @classmethod
    def _parse_status(cls, value):
        return value if isinstance(value, FirmwareEvidenceParseStatus) else FirmwareEvidenceParseStatus(value)

    @model_validator(mode="after")
    def _file_boundary(self):
        if len(self.files) != 2:
            raise ValueError("firmware source proposal requires exactly two files")
        paths = tuple(item.relative_path.casefold() for item in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("firmware source proposal paths must be unique")
        if sum(item.size_bytes for item in self.files) > 128 * 1024:
            raise ValueError("firmware source proposal exceeds total size limit")
        if self.status is not FirmwareSourceProposalStatus.AWAITING_APPROVAL:
            raise ValueError("proposal content remains immutable and awaiting approval")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareSourceProposalDecision(Fingerprinted):
    decision_id: str
    proposal_id: str
    proposal_fingerprint: str
    decision: FirmwareSourceProposalDecisionStatus
    reviewer: str | None = None
    decided_at: datetime | None = None
    reason: str

    @field_validator("decision_id", "proposal_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fp(cls, value):
        return fp(value, "proposal_fingerprint")

    @field_validator("decision", mode="before")
    @classmethod
    def _decision(cls, value):
        return value if isinstance(value, FirmwareSourceProposalDecisionStatus) else FirmwareSourceProposalDecisionStatus(value)

    @field_validator("reviewer", mode="before")
    @classmethod
    def _reviewer(cls, value):
        return None if value is None else ident(value, "reviewer")

    @field_validator("decided_at", mode="before")
    @classmethod
    def _decided_at(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("decided_at must be a UTC timestamp") from error
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decided_at must be a UTC timestamp")
        return value.astimezone(UTC)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value):
        return text(value, "reason", 1024)

    @model_validator(mode="after")
    def _decision_boundary(self):
        if self.decision is FirmwareSourceProposalDecisionStatus.PENDING:
            if self.reviewer is not None or self.decided_at is not None:
                raise ValueError("pending proposal decision cannot have reviewer or time")
        elif self.reviewer is None or self.decided_at is None:
            raise ValueError("terminal proposal decision requires reviewer and time")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareSourceGenerationState(Fingerprinted):
    generation_id: str
    project_id: str
    conversation_id: str
    request: FirmwareSourceGenerationRequest
    proposal: FirmwareSourceProposal
    decision: FirmwareSourceProposalDecision
    operation_id: str | None = None
    operation_fingerprint: str | None = None
    status: FirmwareSourceGenerationStatus

    @field_validator("generation_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("operation_id", mode="before")
    @classmethod
    def _operation_id(cls, value):
        return None if value is None else ident(value, "operation_id")

    @field_validator("operation_fingerprint", mode="before")
    @classmethod
    def _operation_fingerprint(cls, value):
        return None if value is None else fp(value, "operation_fingerprint")

    @field_validator("request", mode="before")
    @classmethod
    def _request(cls, value):
        return FirmwareSourceGenerationRequest.model_validate(
            copy.deepcopy(value.model_dump(mode="python") if isinstance(value, FirmwareSourceGenerationRequest) else value),
            strict=False,
        )

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value):
        return FirmwareSourceProposal.model_validate(
            copy.deepcopy(value.model_dump(mode="python") if isinstance(value, FirmwareSourceProposal) else value),
            strict=False,
        )

    @field_validator("decision", mode="before")
    @classmethod
    def _proposal_decision(cls, value):
        return FirmwareSourceProposalDecision.model_validate(
            copy.deepcopy(value.model_dump(mode="python") if isinstance(value, FirmwareSourceProposalDecision) else value),
            strict=False,
        )

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, FirmwareSourceGenerationStatus) else FirmwareSourceGenerationStatus(value)

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.request.project_id != self.project_id
            or self.request.conversation_id != self.conversation_id
            or self.proposal.project_id != self.project_id
            or self.proposal.conversation_id != self.conversation_id
            or self.proposal.generation_request_id != self.request.request_id
            or self.proposal.generation_request_fingerprint != self.request.fingerprint
            or self.decision.proposal_id != self.proposal.proposal_id
            or self.decision.proposal_fingerprint != self.proposal.fingerprint
            or (self.operation_id is None) != (self.operation_fingerprint is None)
        ):
            raise ValueError("firmware source generation state binding mismatch")
        if self.status is FirmwareSourceGenerationStatus.AWAITING_APPROVAL:
            if self.decision.decision is not FirmwareSourceProposalDecisionStatus.PENDING or self.operation_id is not None:
                raise ValueError("awaiting source generation requires pending proposal decision")
        elif self.status is FirmwareSourceGenerationStatus.QUEUED:
            if self.decision.decision is not FirmwareSourceProposalDecisionStatus.APPROVED or self.operation_id is None:
                raise ValueError("queued source generation requires approved operation")
        elif self.decision.decision is not FirmwareSourceProposalDecisionStatus.REJECTED or self.operation_id is not None:
            raise ValueError("rejected source generation state is invalid")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicReadinessStatus(StrEnum):
    READY = "READY"
    # Keep the historical wire value while naming the G2 handoff explicitly.
    READY_FOR_GENERATION = "READY"
    BLOCKED_MISSING_COMPONENT = "BLOCKED_MISSING_COMPONENT"
    BLOCKED_MISSING_SYMBOL = "BLOCKED_MISSING_SYMBOL"
    BLOCKED_MISSING_PIN = "BLOCKED_MISSING_PIN"
    BLOCKED_MISSING_NET = "BLOCKED_MISSING_NET"
    BLOCKED_MISSING_POWER_FACT = "BLOCKED_MISSING_POWER_FACT"
    BLOCKED_AMBIGUOUS_DESIGN_TRUTH = "BLOCKED_AMBIGUOUS_DESIGN_TRUTH"
    BLOCKED_UNSUPPORTED_KICAD_MAPPING = "BLOCKED_UNSUPPORTED_KICAD_MAPPING"


class SchematicProductionStatus(StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    QUEUED = "QUEUED"
    REJECTED = "REJECTED"
    GENERATED = "GENERATED"
    FAILED = "FAILED"
    STALE = "STALE"


class SchematicGenerationStatus(StrEnum):
    ARTIFACT_GENERATED = "ARTIFACT_GENERATED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    STALE = "STALE"


class SchematicProposalDecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SchematicErcStatus(StrEnum):
    ERC_PASS = "ERC_PASS"
    ERC_FAIL = "ERC_FAIL"
    ERC_WARNINGS = "ERC_WARNINGS"
    ERC_BLOCKED = "ERC_BLOCKED"
    ERC_UNAVAILABLE = "ERC_UNAVAILABLE"


class SchematicErcEngineeringStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    WARNINGS_REMAIN = "WARNINGS_REMAIN"
    PARTIAL = "PARTIAL"


class SchematicErcIssueSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class SchematicErcIssueStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"
    STALE = "STALE"


class SchematicErcIssueClassification(StrEnum):
    UNCLASSIFIED = "UNCLASSIFIED"
    ARTIFACT_RENDERING_DEFECT = "ARTIFACT_RENDERING_DEFECT"
    PLAN_OR_TRUTH_DEFECT = "PLAN_OR_TRUTH_DEFECT"
    LIBRARY_OR_EDA_MAPPING_DEFECT = "LIBRARY_OR_EDA_MAPPING_DEFECT"
    TOOLCHAIN_OR_ENVIRONMENT = "TOOLCHAIN_OR_ENVIRONMENT"
    NON_BLOCKING_WARNING = "NON_BLOCKING_WARNING"


class SchematicErcCorrelationStatus(StrEnum):
    MATCHED = "MATCHED"
    UNMATCHED = "UNMATCHED"
    STALE = "STALE"


class SchematicErcGateStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"


class SchematicRepairDecisionAction(StrEnum):
    APPROVE_FIX = "APPROVE_FIX"
    REJECT_FIX = "REJECT_FIX"
    EDIT_FIX = "EDIT_FIX"
    DEFER = "DEFER"
    WAIVE_WITH_REASON = "WAIVE_WITH_REASON"


class SchematicRepairApplicationStatus(StrEnum):
    APPLIED = "APPLIED"
    STALE = "STALE"


class SchematicRepairAssessmentKind(StrEnum):
    DETERMINISTIC_RENDERING_DEFECT = "DETERMINISTIC_RENDERING_DEFECT"
    DESIGN_TRUTH_CONFLICT = "DESIGN_TRUTH_CONFLICT"
    MISSING_DESIGN_FACT = "MISSING_DESIGN_FACT"
    LIBRARY_OR_EDA_MAPPING_DEFECT = "LIBRARY_OR_EDA_MAPPING_DEFECT"
    TOOLCHAIN_OR_ENVIRONMENT = "TOOLCHAIN_OR_ENVIRONMENT"
    NON_BLOCKING_WARNING = "NON_BLOCKING_WARNING"
    UNSUPPORTED = "UNSUPPORTED"


def _schematic_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


def _schematic_refs(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if required and not refs:
        raise ValueError(f"{field} are invalid")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _schematic_path(value: object, field: str = "target_relative_path") -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in parts)
        or not normalized.casefold().endswith(".kicad_sch")
        or len(normalized) > 255
        or any(part.split(".", 1)[0].upper() in reserved for part in parts)
    ):
        raise ValueError(f"{field} is unsafe")
    return normalized


def _schematic_reference(value: object, field: str) -> str:
    """Validate a non-secret logical artifact/report reference."""

    checked = ident(value, field)
    normalized = checked.replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.startswith("//")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError(f"{field} is unsafe")
    return normalized


class SchematicReadinessResult(Fingerprinted):
    status: SchematicReadinessStatus
    missing_facts: tuple[str, ...] = ()
    affected_source_refs: tuple[str, ...] = ()
    required_next_action: str
    design_truth_fingerprint: str
    schematic_plan_id: str | None = None
    schematic_plan_fingerprint: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, SchematicReadinessStatus) else SchematicReadinessStatus(value)

    @field_validator("missing_facts", "affected_source_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _schematic_refs(value, info.field_name)

    @field_validator("required_next_action", mode="before")
    @classmethod
    def _action(cls, value):
        return text(value, "required_next_action", 1024)

    @field_validator("design_truth_fingerprint", mode="before")
    @classmethod
    def _truth_fp(cls, value):
        return fp(value, "design_truth_fingerprint")

    @field_validator("schematic_plan_id", mode="before")
    @classmethod
    def _plan_id(cls, value):
        return None if value is None else ident(value, "schematic_plan_id")

    @field_validator("schematic_plan_fingerprint", mode="before")
    @classmethod
    def _plan_fp(cls, value):
        return None if value is None else fp(value, "schematic_plan_fingerprint")

    @model_validator(mode="after")
    def _result(self):
        if (self.schematic_plan_id is None) != (self.schematic_plan_fingerprint is None):
            raise ValueError("schematic readiness plan binding is incomplete")
        if self.status is SchematicReadinessStatus.READY_FOR_GENERATION and self.missing_facts:
            raise ValueError("ready schematic result cannot contain missing facts")
        if self.status is not SchematicReadinessStatus.READY_FOR_GENERATION and not self.missing_facts:
            raise ValueError("blocked schematic result must explain missing facts")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicComponentProposal(Fingerprinted):
    component_id: str
    reference: str
    symbol_binding: str
    value: str
    source_refs: tuple[str, ...]

    _ids = field_validator("component_id", "reference", mode="before")(lambda value, info: ident(value, info.field_name))
    @field_validator("symbol_binding", mode="before")
    @classmethod
    def _symbol(cls, value: object) -> str:
        checked = text(value, "symbol_binding", 512)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+", checked):
            raise ValueError("symbol_binding is invalid")
        return checked
    _value = field_validator("value", mode="before")(lambda value: text(value, "value", 2048))
    _refs = field_validator("source_refs", mode="before")(lambda value: _schematic_refs(value, "source_refs", required=True))

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicPinProposal(Fingerprinted):
    pin_id: str
    component_id: str
    pin_number: str
    pin_name: str
    signal_id: str | None = None
    source_refs: tuple[str, ...]

    _ids = field_validator("pin_id", "component_id", mode="before")(lambda value, info: ident(value, info.field_name))
    _text = field_validator("pin_number", "pin_name", mode="before")(lambda value, info: text(value, info.field_name, 128))
    _signal = field_validator("signal_id", mode="before")(lambda value: None if value is None else ident(value, "signal_id"))
    _refs = field_validator("source_refs", mode="before")(lambda value: _schematic_refs(value, "source_refs", required=True))

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicNetProposal(Fingerprinted):
    net_id: str
    net_name: str
    signal_id: str
    nodes: tuple[str, ...]
    source_refs: tuple[str, ...]

    _ids = field_validator("net_id", "signal_id", mode="before")(lambda value, info: ident(value, info.field_name))
    _text = field_validator("net_name", mode="before")(lambda value: text(value, "net_name", 512))
    _nodes = field_validator("nodes", mode="before")(lambda value: tuple(text(item, "node", 256) for item in (tuple(value) if type(value) is list else tuple_values(value, "nodes"))))
    _refs = field_validator("source_refs", mode="before")(lambda value: _schematic_refs(value, "source_refs", required=True))

    @model_validator(mode="after")
    def _nodes_present(self):
        if len(self.nodes) < 2 or len(self.nodes) != len(set(self.nodes)):
            raise ValueError("schematic net requires unique endpoints")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicPowerProposal(Fingerprinted):
    power_domain_id: str
    name: str
    voltage: str
    ground_net: str
    component_refs: tuple[str, ...]
    source_refs: tuple[str, ...]

    _id = field_validator("power_domain_id", mode="before")(lambda value: ident(value, "power_domain_id"))
    _text = field_validator("name", "voltage", "ground_net", mode="before")(lambda value, info: text(value, info.field_name, 256))
    _components = field_validator("component_refs", mode="before")(lambda value: _schematic_refs(value, "component_refs", required=True))
    _refs = field_validator("source_refs", mode="before")(lambda value: _schematic_refs(value, "source_refs", required=True))

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicProductionRequest(Fingerprinted):
    request_id: str
    source_request_id: str | None = None
    trace_id: str | None = None
    project_id: str
    conversation_id: str
    requester: str
    architecture_id: str
    design_truth_id: str
    hardware_design_id: str
    implementation_id: str
    implementation_task_id: str
    authorization_id: str
    authorization_request_id: str
    capability_id: Literal["ARTIFACT_CREATION"] = "ARTIFACT_CREATION"
    design_truth_fingerprint: str
    hardware_design_fingerprint: str
    implementation_fingerprint: str
    authorization_fingerprint: str
    workspace_identity: str
    target_relative_path: str
    source_refs: tuple[str, ...]
    readiness_fingerprint: str
    schematic_plan_id: str | None = None
    schematic_plan_fingerprint: str | None = None

    _ids = field_validator(
        "request_id", "source_request_id", "trace_id", "project_id", "conversation_id", "requester", "architecture_id", "design_truth_id",
        "hardware_design_id", "implementation_id", "implementation_task_id", "authorization_id", "authorization_request_id",
        "workspace_identity", mode="before",
    )(
        lambda value, info: (
            None
            if value is None and info.field_name in {"source_request_id", "trace_id"}
            else ident(value, info.field_name)
        )
    )
    _fps = field_validator(
        "design_truth_fingerprint", "hardware_design_fingerprint", "implementation_fingerprint",
        "authorization_fingerprint", "readiness_fingerprint", mode="before",
    )(lambda value, info: fp(value, info.field_name))
    _path = field_validator("target_relative_path", mode="before")(_schematic_path)
    _refs = field_validator("source_refs", mode="before")(lambda value: _schematic_refs(value, "source_refs", required=True))

    @field_validator("schematic_plan_id", mode="before")
    @classmethod
    def _plan_id(cls, value):
        return None if value is None else ident(value, "schematic_plan_id")

    @field_validator("schematic_plan_fingerprint", mode="before")
    @classmethod
    def _plan_fp(cls, value):
        return None if value is None else fp(value, "schematic_plan_fingerprint")

    @model_validator(mode="after")
    def _plan_binding(self):
        if (self.schematic_plan_id is None) != (self.schematic_plan_fingerprint is None):
            raise ValueError("schematic production plan binding is incomplete")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if self.schematic_plan_id is not None or self.schematic_plan_fingerprint is not None:
            if self.source_request_id is None and self.trace_id is None:
                return ()
            return (
                canonical(
                    self,
                    exclude={"fingerprint", "schematic_plan_id", "schematic_plan_fingerprint"},
                ),
            )
        return (
            canonical(
                self,
                exclude={
                    "fingerprint",
                    "schematic_plan_id",
                    "schematic_plan_fingerprint",
                    "source_request_id",
                    "trace_id",
                },
            ),
            canonical(
                self,
                exclude={"fingerprint", "schematic_plan_id", "schematic_plan_fingerprint"},
            ),
        )

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicProposal(Fingerprinted):
    proposal_id: str
    request_id: str
    project_id: str
    conversation_id: str
    target_relative_path: str
    components: tuple[SchematicComponentProposal, ...]
    pins: tuple[SchematicPinProposal, ...]
    nets: tuple[SchematicNetProposal, ...]
    power_symbols: tuple[SchematicPowerProposal, ...]
    verification_refs: tuple[str, ...] = ()
    source_refs: tuple[str, ...]
    design_truth_fingerprint: str
    connectivity_fingerprint: str
    status: SchematicProductionStatus = SchematicProductionStatus.AWAITING_APPROVAL
    schematic_plan_id: str | None = None
    schematic_plan_fingerprint: str | None = None

    _ids = field_validator("proposal_id", "request_id", "project_id", "conversation_id", mode="before")(lambda value, info: ident(value, info.field_name))
    _path = field_validator("target_relative_path", mode="before")(_schematic_path)
    @field_validator("components", "pins", "nets", "power_symbols", mode="before")
    @classmethod
    def _objects(cls, value, info):
        models = {
            "components": SchematicComponentProposal,
            "pins": SchematicPinProposal,
            "nets": SchematicNetProposal,
            "power_symbols": SchematicPowerProposal,
        }
        return _schematic_objects(value, info.field_name, models[info.field_name])
    _refs = field_validator("verification_refs", "source_refs", mode="before")(lambda value, info: _schematic_refs(value, info.field_name))
    _fps = field_validator("design_truth_fingerprint", "connectivity_fingerprint", mode="before")(lambda value, info: fp(value, info.field_name))
    _status = field_validator("status", mode="before")(lambda value: value if isinstance(value, SchematicProductionStatus) else SchematicProductionStatus(value))

    @field_validator("schematic_plan_id", mode="before")
    @classmethod
    def _plan_id(cls, value):
        return None if value is None else ident(value, "schematic_plan_id")

    @field_validator("schematic_plan_fingerprint", mode="before")
    @classmethod
    def _plan_fp(cls, value):
        return None if value is None else fp(value, "schematic_plan_fingerprint")

    @model_validator(mode="after")
    def _binding(self):
        if (self.schematic_plan_id is None) != (self.schematic_plan_fingerprint is None):
            raise ValueError("schematic proposal plan binding is incomplete")
        component_ids = tuple(item.component_id for item in self.components)
        pin_ids = tuple(item.pin_id for item in self.pins)
        net_ids = tuple(item.net_id for item in self.nets)
        references = tuple(item.reference for item in self.components)
        if not self.components or len(component_ids) != len(set(component_ids)) or len(references) != len(set(references)):
            raise ValueError("schematic proposal components are invalid")
        if len(pin_ids) != len(set(pin_ids)) or len(net_ids) != len(set(net_ids)):
            raise ValueError("schematic proposal identities are invalid")
        if any(item.component_id not in component_ids for item in self.pins):
            raise ValueError("schematic pin component reference is invalid")
        pins_by_component = {
            component_id: tuple(
                sorted(
                    (item.pin_number, item.pin_name)
                    for item in self.pins
                    if item.component_id == component_id
                )
            )
            for component_id in component_ids
        }
        if any(not pins_by_component[component_id] for component_id in component_ids):
            raise ValueError("schematic proposal components require explicit pins")
        symbol_shapes: dict[str, tuple[tuple[str, str], ...]] = {}
        for component in self.components:
            shape = pins_by_component[component.component_id]
            previous = symbol_shapes.setdefault(component.symbol_binding, shape)
            if previous != shape:
                raise ValueError("schematic symbol binding pin shape is inconsistent")
        endpoints = {
            f"{item.component_id}.{item.pin_number}": item
            for item in self.pins
        }
        if any(item.signal_id is None for item in self.pins):
            raise ValueError("schematic proposal pins require explicit signal bindings")
        for net in self.nets:
            for node in net.nodes:
                pin = endpoints.get(node)
                if pin is None:
                    raise ValueError("schematic net endpoint reference is invalid")
                if pin.signal_id != net.signal_id:
                    raise ValueError("schematic net signal binding is invalid")
        net_names = {item.net_name for item in self.nets}
        if any(power.ground_net not in net_names for power in self.power_symbols):
            raise ValueError("schematic power ground net reference is invalid")
        if any(
            component_ref not in component_ids
            for power in self.power_symbols
            for component_ref in power.component_refs
        ):
            raise ValueError("schematic power component reference is invalid")
        if self.status is not SchematicProductionStatus.AWAITING_APPROVAL:
            raise ValueError("schematic proposal content is immutable")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if self.schematic_plan_id is not None or self.schematic_plan_fingerprint is not None:
            return ()
        return (
            canonical(
                self,
                exclude={"fingerprint", "schematic_plan_id", "schematic_plan_fingerprint"},
            ),
        )

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicProposalDecision(Fingerprinted):
    decision_id: str
    proposal_id: str
    proposal_fingerprint: str
    decision: SchematicProposalDecisionStatus
    reviewer: str | None = None
    decided_at: datetime | None = None
    reason: str

    _ids = field_validator("decision_id", "proposal_id", mode="before")(lambda value, info: ident(value, info.field_name))
    _fp = field_validator("proposal_fingerprint", mode="before")(lambda value: fp(value, "proposal_fingerprint"))
    _decision = field_validator("decision", mode="before")(lambda value: value if isinstance(value, SchematicProposalDecisionStatus) else SchematicProposalDecisionStatus(value))
    _reviewer = field_validator("reviewer", mode="before")(lambda value: None if value is None else ident(value, "reviewer"))
    @field_validator("decided_at", mode="before")
    @classmethod
    def _time(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("decided_at must be timezone-aware")
        return value.astimezone(UTC)

    _reason = field_validator("reason", mode="before")(lambda value: text(value, "reason", 1024))

    @model_validator(mode="after")
    def _boundary(self):
        if self.decision is SchematicProposalDecisionStatus.PENDING and (self.reviewer is not None or self.decided_at is not None):
            raise ValueError("pending schematic decision cannot have reviewer or time")
        if self.decision is not SchematicProposalDecisionStatus.PENDING and (self.reviewer is None or self.decided_at is None):
            raise ValueError("terminal schematic decision requires reviewer and time")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicParseValidationEvidence(Fingerprinted):
    validation_id: str
    project_id: str
    conversation_id: str
    operation_id: str
    artifact_path: str
    artifact_fingerprint: str
    kicad_cli_path: str
    kicad_version: str
    command_identity: str
    exit_code: int
    validation_status: Literal["PARSE_VERIFIED", "FAILED"]
    artifact_unchanged: bool
    stdout_summary: str = ""
    stderr_summary: str = ""
    validated_at: datetime

    _ids = field_validator(
        "validation_id", "project_id", "conversation_id", "operation_id", mode="before"
    )(lambda value, info: ident(value, info.field_name))
    _artifact_fingerprint = field_validator("artifact_fingerprint", mode="before")(
        lambda value: fp(value, "artifact_fingerprint")
    )
    _artifact_path = field_validator("artifact_path", mode="before")(_schematic_path)

    @field_validator("kicad_cli_path", mode="before")
    @classmethod
    def _cli_path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("kicad_cli_path is invalid")
        value = value.strip()
        if not value or len(value) > 1024 or "\x00" in value or re.search(
            r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret)\s*[:=]",
            value,
            re.IGNORECASE,
        ):
            raise ValueError("kicad_cli_path is unsafe")
        return value

    @field_validator("kicad_version", "command_identity", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return text(value, info.field_name, 256)

    @field_validator("stdout_summary", "stderr_summary", mode="before")
    @classmethod
    def _diagnostics(cls, value: object, info) -> str:
        if value in (None, ""):
            return ""
        return text(value, info.field_name, 2048)

    @field_validator("exit_code", mode="before")
    @classmethod
    def _exit_code(cls, value: object) -> int:
        if type(value) is not int or not -255 <= value <= 255:
            raise ValueError("exit_code is invalid")
        return value

    @field_validator("artifact_unchanged", mode="before")
    @classmethod
    def _artifact_unchanged(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("artifact_unchanged is invalid")
        return value

    @field_validator("validated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("validated_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _boundary(self):
        if self.validation_status == "PARSE_VERIFIED" and (
            self.exit_code != 0 or not self.artifact_unchanged
        ):
            raise ValueError("verified parse evidence requires successful immutable validation")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicGenerationReceipt(Fingerprinted):
    receipt_id: str
    project_id: str
    conversation_id: str
    operation_id: str
    operation_fingerprint: str
    operation_receipt_fingerprint: str
    workspace_apply_receipt_fingerprint: str
    artifact_type: Literal["SCHEMATIC"] = "SCHEMATIC"
    artifact_path: str
    schematic_plan_id: str | None = None
    schematic_plan_fingerprint: str | None = None
    design_truth_fingerprint: str
    artifact_fingerprint: str
    generator: str
    generator_version: str
    validation_status: Literal["PARSE_VERIFIED", "UNAVAILABLE", "FAILED"]
    parse_validation: SchematicParseValidationEvidence | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    status: SchematicGenerationStatus
    source_refs: tuple[str, ...]
    created_at: datetime

    _ids = field_validator(
        "receipt_id", "project_id", "conversation_id", "operation_id", mode="before"
    )(lambda value, info: ident(value, info.field_name))
    _fps = field_validator(
        "operation_receipt_fingerprint", "workspace_apply_receipt_fingerprint",
        "operation_fingerprint", "design_truth_fingerprint", "artifact_fingerprint", mode="before",
    )(lambda value, info: fp(value, info.field_name))
    _plan_id = field_validator("schematic_plan_id", mode="before")(
        lambda value: None if value is None else ident(value, "schematic_plan_id")
    )
    _plan_fp = field_validator("schematic_plan_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "schematic_plan_fingerprint")
    )
    _path = field_validator("artifact_path", mode="before")(_schematic_path)
    _generator = field_validator("generator", "generator_version", mode="before")(
        lambda value, info: text(value, info.field_name, 256)
    )
    _refs = field_validator("source_refs", mode="before")(
        lambda value: _schematic_refs(value, "source_refs", required=True)
    )

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return value if isinstance(value, SchematicGenerationStatus) else SchematicGenerationStatus(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validation_boundary(self):
        if self.status is SchematicGenerationStatus.SUCCESS and self.validation_status != "PARSE_VERIFIED":
            raise ValueError("successful schematic generation requires parse validation")
        if self.validation_status == "PARSE_VERIFIED" and self.parse_validation is None:
            raise ValueError("parse-verified schematic generation requires parse evidence")
        if self.parse_validation is not None:
            evidence = self.parse_validation
            if (
                evidence.project_id != self.project_id
                or evidence.conversation_id != self.conversation_id
                or evidence.operation_id != self.operation_id
                or evidence.artifact_path != self.artifact_path
                or evidence.artifact_fingerprint != self.artifact_fingerprint
                or evidence.validation_status != self.validation_status
            ):
                raise ValueError("schematic parse evidence binding mismatch")
        if self.status is SchematicGenerationStatus.ARTIFACT_GENERATED and self.validation_status != "UNAVAILABLE":
            raise ValueError("unvalidated schematic generation must be explicit")
        if self.status is SchematicGenerationStatus.ARTIFACT_GENERATED and self.parse_validation is not None:
            raise ValueError("unvalidated schematic generation cannot contain parse evidence")
        if self.status is SchematicGenerationStatus.FAILED and self.validation_status != "FAILED":
            raise ValueError("failed schematic generation requires failed validation status")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if self.parse_validation is None:
            return (canonical(self, exclude={"fingerprint", "parse_validation"}),)
        return ()

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicProductionState(Fingerprinted):
    production_id: str
    project_id: str
    conversation_id: str
    request: SchematicProductionRequest
    proposal: SchematicProposal
    decision: SchematicProposalDecision
    operation_id: str | None = None
    operation_fingerprint: str | None = None
    status: SchematicProductionStatus
    generation_receipt: SchematicGenerationReceipt | None = None
    erc_receipt: SchematicErcReceipt | None = None
    erc_assessment: SchematicRepairAssessment | None = None
    repair_proposals: tuple[SchematicRepairProposal, ...] = ()
    repair_decisions: tuple[SchematicRepairDecision, ...] = ()
    repair_application_receipts: tuple[SchematicRepairApplicationReceipt, ...] = ()
    erc_gate_status: SchematicErcGateStatus | None = None

    _ids = field_validator("production_id", "project_id", "conversation_id", mode="before")(lambda value, info: ident(value, info.field_name))
    _operation_id = field_validator("operation_id", mode="before")(lambda value: None if value is None else ident(value, "operation_id"))
    _operation_fp = field_validator("operation_fingerprint", mode="before")(lambda value: None if value is None else fp(value, "operation_fingerprint"))
    _status = field_validator("status", mode="before")(lambda value: value if isinstance(value, SchematicProductionStatus) else SchematicProductionStatus(value))

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.request.project_id != self.project_id
            or self.request.conversation_id != self.conversation_id
            or self.proposal.request_id != self.request.request_id
            or self.proposal.project_id != self.project_id
            or self.proposal.conversation_id != self.conversation_id
            or self.proposal.target_relative_path != self.request.target_relative_path
            or self.proposal.design_truth_fingerprint != self.request.design_truth_fingerprint
            or self.decision.proposal_id != self.proposal.proposal_id
            or self.decision.proposal_fingerprint != self.proposal.fingerprint
            or self.decision.decision_id
            != "schematic-decision-"
            + hashlib.sha256(
                f"{self.proposal.proposal_id}|{self.proposal.fingerprint}".encode("utf-8")
            ).hexdigest()[:24]
            or ((self.operation_id is None) != (self.operation_fingerprint is None))
            or self.request.schematic_plan_id != self.proposal.schematic_plan_id
            or self.request.schematic_plan_fingerprint != self.proposal.schematic_plan_fingerprint
        ):
            raise ValueError("schematic production state binding mismatch")
        expected_reason = (
            "Awaiting explicit schematic proposal approval."
            if self.decision.decision is SchematicProposalDecisionStatus.PENDING
            else "Schematic proposal explicitly approved."
            if self.decision.decision is SchematicProposalDecisionStatus.APPROVED
            else "Schematic proposal explicitly rejected."
        )
        if self.decision.reason != expected_reason:
            raise ValueError("schematic proposal decision reason mismatch")
        if self.status is SchematicProductionStatus.AWAITING_APPROVAL and self.decision.decision is not SchematicProposalDecisionStatus.PENDING:
            raise ValueError("awaiting schematic production requires pending decision")
        if self.status is SchematicProductionStatus.QUEUED and (self.decision.decision is not SchematicProposalDecisionStatus.APPROVED or self.operation_id is None):
            raise ValueError("queued schematic production requires approved operation")
        if self.status is SchematicProductionStatus.REJECTED and (self.decision.decision is not SchematicProposalDecisionStatus.REJECTED or self.operation_id is not None):
            raise ValueError("rejected schematic production state is invalid")
        if self.status in {SchematicProductionStatus.GENERATED, SchematicProductionStatus.FAILED, SchematicProductionStatus.STALE}:
            if self.decision.decision is not SchematicProposalDecisionStatus.APPROVED or self.operation_id is None or self.generation_receipt is None:
                raise ValueError("terminal schematic production state requires an approved receipt")
            receipt = self.generation_receipt
            if (
                receipt.project_id != self.project_id
                or receipt.conversation_id != self.conversation_id
                or receipt.operation_id != self.operation_id
                or receipt.operation_fingerprint != self.operation_fingerprint
                or receipt.artifact_path != self.request.target_relative_path
                or receipt.design_truth_fingerprint != self.request.design_truth_fingerprint
                or receipt.schematic_plan_id != self.request.schematic_plan_id
                or receipt.schematic_plan_fingerprint != self.request.schematic_plan_fingerprint
            ):
                raise ValueError("schematic generation receipt binding mismatch")
            expected_receipt_status = {
                SchematicProductionStatus.GENERATED: {SchematicGenerationStatus.ARTIFACT_GENERATED, SchematicGenerationStatus.SUCCESS},
                SchematicProductionStatus.FAILED: {SchematicGenerationStatus.FAILED},
                SchematicProductionStatus.STALE: {SchematicGenerationStatus.STALE},
            }[self.status]
            if receipt.status not in expected_receipt_status:
                raise ValueError("schematic production receipt status mismatch")
        elif self.generation_receipt is not None:
            raise ValueError("non-terminal schematic production cannot contain a receipt")
        if self.erc_receipt is not None:
            if self.generation_receipt is None:
                raise ValueError("ERC receipt requires a schematic generation receipt")
            if (
                self.erc_receipt.project_id != self.project_id
                or self.erc_receipt.conversation_id != self.conversation_id
                or self.erc_receipt.artifact_path != self.generation_receipt.artifact_path
                or self.erc_receipt.artifact_fingerprint != self.generation_receipt.artifact_fingerprint
                or self.erc_receipt.schematic_plan_fingerprint != self.generation_receipt.schematic_plan_fingerprint
            ):
                raise ValueError("ERC receipt is not bound to the current schematic artifact")
            if self.erc_gate_status is None:
                raise ValueError("ERC receipt requires a gate status")
        elif self.erc_assessment is not None or self.repair_proposals or self.repair_decisions or self.erc_gate_status is not None:
            raise ValueError("ERC projection requires an ERC receipt")
        if (
            self.erc_assessment is not None
            and self.erc_receipt is not None
            and self.erc_assessment.erc_result_fingerprint
            != self.erc_receipt.result.fingerprint
        ):
            raise ValueError("ERC assessment binding mismatch")
        if self.repair_proposals and self.erc_receipt is not None:
            result_fingerprint = self.erc_receipt.result.fingerprint
            if any(item.erc_result_fingerprint != result_fingerprint for item in self.repair_proposals):
                raise ValueError("repair proposal ERC binding mismatch")
        if self.repair_decisions and self.repair_proposals:
            proposal_ids = {item.repair_id for item in self.repair_proposals}
            if any(item.repair_id not in proposal_ids for item in self.repair_decisions):
                raise ValueError("repair decision proposal binding mismatch")
        if self.repair_application_receipts:
            if self.erc_receipt is None or self.generation_receipt is None:
                raise ValueError("repair application receipt requires current ERC and generation receipts")
            proposal_ids = {item.repair_id for item in self.repair_proposals}
            decision_ids = {item.decision_id for item in self.repair_decisions}
            for receipt in self.repair_application_receipts:
                if (
                    receipt.project_id != self.project_id
                    or receipt.conversation_id != self.conversation_id
                    or receipt.repair_id not in proposal_ids
                    or receipt.repair_decision_id not in decision_ids
                ):
                    raise ValueError("repair application receipt lineage mismatch")
                if receipt.status is SchematicRepairApplicationStatus.APPLIED and (
                    receipt.post_artifact_fingerprint != self.generation_receipt.artifact_fingerprint
                    or receipt.post_erc_receipt_fingerprint != self.erc_receipt.fingerprint
                    or receipt.post_erc_result_fingerprint != self.erc_receipt.result.fingerprint
                ):
                    raise ValueError("current repair application receipt binding mismatch")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if (
            self.request.schematic_plan_id is not None
            or self.request.schematic_plan_fingerprint is not None
            or self.proposal.schematic_plan_id is not None
            or self.proposal.schematic_plan_fingerprint is not None
            or self.generation_receipt is not None
            or self.erc_receipt is not None
            or self.erc_assessment is not None
            or self.repair_proposals
            or self.repair_decisions
            or self.repair_application_receipts
            or self.erc_gate_status is not None
        ):
            return ()
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        for field in ("request", "proposal"):
            payload[field].pop("schematic_plan_id", None)
            payload[field].pop("schematic_plan_fingerprint", None)
        for field in (
            "generation_receipt",
            "erc_receipt",
            "erc_assessment",
            "repair_proposals",
            "repair_decisions",
            "repair_application_receipts",
            "erc_gate_status",
        ):
            payload.pop(field, None)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return ("sha256:" + hashlib.sha256(encoded).hexdigest(),)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicErcIssue(Fingerprinted):
    """Normalized projection of one real KiCad ERC diagnostic."""

    issue_id: str
    severity: SchematicErcIssueSeverity
    rule_id: str
    message: str
    component_ref: str | None = None
    pin_ref: str | None = None
    net_ref: str | None = None
    location: str | None = None
    raw_evidence_ref: str
    artifact_fingerprint: str
    status: SchematicErcIssueStatus = SchematicErcIssueStatus.OPEN
    classification: SchematicErcIssueClassification = SchematicErcIssueClassification.UNCLASSIFIED

    _ids = field_validator("issue_id", mode="before")(
        lambda value: ident(value, "issue_id")
    )
    _severity = field_validator("severity", mode="before")(
        lambda value: value if isinstance(value, SchematicErcIssueSeverity) else SchematicErcIssueSeverity(value)
    )
    _rule = field_validator("rule_id", mode="before")(
        lambda value: text(value, "rule_id", 256)
    )
    _message = field_validator("message", mode="before")(
        lambda value: text(value, "message", 4096)
    )
    _optional_refs = field_validator("component_ref", "pin_ref", "net_ref", mode="before")(
        lambda value, info: None if value is None else ident(value, info.field_name)
    )
    _location = field_validator("location", mode="before")(
        lambda value: None if value is None else text(value, "location", 256)
    )
    _raw_ref = field_validator("raw_evidence_ref", mode="before")(
        lambda value: _schematic_reference(value, "raw_evidence_ref")
    )
    _artifact = field_validator("artifact_fingerprint", mode="before")(
        lambda value: fp(value, "artifact_fingerprint")
    )
    _status = field_validator("status", mode="before")(
        lambda value: value if isinstance(value, SchematicErcIssueStatus) else SchematicErcIssueStatus(value)
    )
    _classification = field_validator("classification", mode="before")(
        lambda value: value if isinstance(value, SchematicErcIssueClassification) else SchematicErcIssueClassification(value)
    )

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicErcCorrelation(Fingerprinted):
    issue_id: str
    project_id: str
    artifact_fingerprint: str
    component_id: str | None = None
    pin_id: str | None = None
    net_id: str | None = None
    truth_evidence_refs: tuple[str, ...] = ()
    plan_refs: tuple[str, ...] = ()
    confidence: float = 0.0
    status: SchematicErcCorrelationStatus = SchematicErcCorrelationStatus.UNMATCHED

    _ids = field_validator("issue_id", "project_id", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _artifact = field_validator("artifact_fingerprint", mode="before")(
        lambda value: fp(value, "artifact_fingerprint")
    )
    _optional_ids = field_validator("component_id", "pin_id", "net_id", mode="before")(
        lambda value, info: None if value is None else ident(value, info.field_name)
    )
    _refs = field_validator("truth_evidence_refs", "plan_refs", mode="before")(
        lambda value, info: _schematic_refs(value, info.field_name)
    )
    _confidence = field_validator("confidence", mode="before")(
        lambda value: confidence(value)
    )
    _status = field_validator("status", mode="before")(
        lambda value: value if isinstance(value, SchematicErcCorrelationStatus) else SchematicErcCorrelationStatus(value)
    )

    @model_validator(mode="after")
    def _matched_binding(self):
        if self.status is SchematicErcCorrelationStatus.MATCHED and not any(
            (self.component_id, self.pin_id, self.net_id)
        ):
            raise ValueError("matched ERC correlation requires a stable identity")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicErcViolation(Fingerprinted):
    severity: str
    rule: str
    message: str
    object_refs: tuple[str, ...] = ()
    raw_source_ref: str

    _text = field_validator("severity", "rule", "message", mode="before")(lambda value, info: text(value, info.field_name, 2048))
    _source = field_validator("raw_source_ref", mode="before")(
        lambda value: _schematic_reference(value, "raw_source_ref")
    )
    _refs = field_validator("object_refs", mode="before")(lambda value: _schematic_refs(value, "object_refs"))

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicErcResult(Fingerprinted):
    operation_id: str
    schematic_artifact_ref: str
    schematic_content_fingerprint: str | None = None
    kicad_version: str
    exit_status: int
    violations: tuple[SchematicErcViolation, ...] = ()
    issues: tuple[SchematicErcIssue, ...] = ()
    correlations: tuple[SchematicErcCorrelation, ...] = ()
    warnings: tuple[str, ...] = ()
    report_artifact: str | None = None
    raw_evidence: str = ""
    raw_evidence_fingerprint: str | None = None
    command_identity: str = "kicad-cli sch erc"
    engineering_status: SchematicErcEngineeringStatus | None = None
    started_at: datetime
    finished_at: datetime
    receipt_ref: str
    status: SchematicErcStatus

    _ids = field_validator("operation_id", "receipt_ref", mode="before")(lambda value, info: ident(value, info.field_name))
    _artifact_ref = field_validator("schematic_artifact_ref", mode="before")(
        lambda value: _schematic_reference(value, "schematic_artifact_ref")
    )
    _content_fp = field_validator("schematic_content_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "schematic_content_fingerprint")
    )
    _version = field_validator("kicad_version", mode="before")(lambda value: text(value, "kicad_version", 128))
    _violations = field_validator("violations", mode="before")(lambda value: _schematic_objects(value, "violations", SchematicErcViolation))
    _issues = field_validator("issues", mode="before")(lambda value: _schematic_objects(value, "issues", SchematicErcIssue))
    _correlations = field_validator("correlations", mode="before")(
        lambda value: _schematic_objects(value, "correlations", SchematicErcCorrelation)
    )
    _warnings = field_validator("warnings", mode="before")(lambda value: tuple(text(item, "warning", 2048) for item in (tuple(value) if type(value) is list else tuple_values(value, "warnings"))))
    _report = field_validator("report_artifact", mode="before")(
        lambda value: None if value is None else _schematic_reference(value, "report_artifact")
    )
    _raw_evidence = field_validator("raw_evidence", mode="before")(
        lambda value: "" if value in (None, "") else text(value, "raw_evidence", 512 * 1024)
    )
    _raw_evidence_fp = field_validator("raw_evidence_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "raw_evidence_fingerprint")
    )
    _command = field_validator("command_identity", mode="before")(
        lambda value: text(value, "command_identity", 256)
    )
    _engineering_status = field_validator("engineering_status", mode="before")(
        lambda value: value if isinstance(value, SchematicErcEngineeringStatus) else SchematicErcEngineeringStatus(value)
    )
    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def _times(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("ERC timestamps must be timezone-aware")
        return value.astimezone(UTC)

    _status = field_validator("status", mode="before")(lambda value: value if isinstance(value, SchematicErcStatus) else SchematicErcStatus(value))

    @model_validator(mode="before")
    @classmethod
    def _engineering_default(cls, values):
        if isinstance(values, dict) and values.get("engineering_status") is None:
            status = values.get("status")
            status_value = status.value if isinstance(status, SchematicErcStatus) else status
            values["engineering_status"] = {
                "ERC_PASS": SchematicErcEngineeringStatus.PASS,
                "ERC_WARNINGS": SchematicErcEngineeringStatus.WARNINGS_REMAIN,
                "ERC_FAIL": SchematicErcEngineeringStatus.BLOCKED,
                "ERC_BLOCKED": SchematicErcEngineeringStatus.BLOCKED,
                "ERC_UNAVAILABLE": SchematicErcEngineeringStatus.BLOCKED,
            }.get(status_value, SchematicErcEngineeringStatus.BLOCKED)
        return values

    @model_validator(mode="after")
    def _result_consistency(self):
        if self.finished_at < self.started_at:
            raise ValueError("ERC finished_at precedes started_at")
        if self.raw_evidence and self.raw_evidence_fingerprint is None:
            raise ValueError("raw ERC evidence requires a fingerprint")
        if self.raw_evidence_fingerprint is not None and self.raw_evidence:
            expected_raw = "sha256:" + hashlib.sha256(self.raw_evidence.encode("utf-8")).hexdigest()
            if expected_raw != self.raw_evidence_fingerprint:
                raise ValueError("raw ERC evidence fingerprint mismatch")
        if self.status is SchematicErcStatus.ERC_PASS and (self.exit_status != 0 or self.violations or any(item.severity is SchematicErcIssueSeverity.ERROR for item in self.issues)):
            raise ValueError("ERC_PASS requires zero exit status and no violations")
        if self.status is SchematicErcStatus.ERC_FAIL and not self.violations:
            raise ValueError("ERC_FAIL requires structured violations")
        if self.status is SchematicErcStatus.ERC_WARNINGS and any(
            item.severity is SchematicErcIssueSeverity.ERROR for item in self.issues
        ):
            raise ValueError("ERC_WARNINGS cannot contain error issues")
        if self.status is SchematicErcStatus.ERC_UNAVAILABLE and self.exit_status != -1:
            raise ValueError("ERC_UNAVAILABLE requires unavailable exit status")
        if self.status is SchematicErcStatus.ERC_BLOCKED and (
            self.exit_status >= 0 or self.violations
        ):
            raise ValueError("ERC_BLOCKED cannot contain executable diagnostics")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if (
            self.issues
            or self.correlations
            or self.raw_evidence
            or self.raw_evidence_fingerprint is not None
            or self.command_identity != "kicad-cli sch erc"
            or self.engineering_status is not SchematicErcEngineeringStatus.PASS
        ):
            return ()
        payload = self.model_dump(
            mode="json",
            exclude={
                "fingerprint",
                "issues",
                "correlations",
                "raw_evidence",
                "raw_evidence_fingerprint",
                "command_identity",
                "engineering_status",
            },
        )
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return ("sha256:" + hashlib.sha256(encoded).hexdigest(),)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicErcReceipt(Fingerprinted):
    receipt_id: str
    project_id: str
    conversation_id: str
    operation_id: str
    operation_receipt_fingerprint: str
    artifact_path: str
    artifact_fingerprint: str
    schematic_plan_fingerprint: str | None = None
    design_truth_fingerprint: str
    kicad_version: str
    command_identity: str
    tool_execution_status: str
    engineering_status: SchematicErcEngineeringStatus
    result: SchematicErcResult
    created_at: datetime

    _ids = field_validator(
        "receipt_id", "project_id", "conversation_id", "operation_id", mode="before"
    )(lambda value, info: ident(value, info.field_name))
    _fps = field_validator(
        "operation_receipt_fingerprint", "artifact_fingerprint",
        "design_truth_fingerprint", mode="before"
    )(lambda value, info: fp(value, info.field_name))
    _plan_fp = field_validator("schematic_plan_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "schematic_plan_fingerprint")
    )
    _path = field_validator("artifact_path", mode="before")(_schematic_path)
    _texts = field_validator(
        "kicad_version", "command_identity", "tool_execution_status", mode="before"
    )(lambda value, info: text(value, info.field_name, 256))
    _status = field_validator("engineering_status", mode="before")(
        lambda value: value if isinstance(value, SchematicErcEngineeringStatus) else SchematicErcEngineeringStatus(value)
    )
    _result = field_validator("result", mode="before")(
        lambda value: SchematicErcResult.model_validate(
            value.model_dump(mode="python") if isinstance(value, SchematicErcResult) else value,
            strict=False,
        )
    )
    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.result.operation_id != self.operation_id
            or self.result.schematic_artifact_ref != self.artifact_path
            or self.result.schematic_content_fingerprint != self.artifact_fingerprint
            or self.result.kicad_version != self.kicad_version
            or self.result.command_identity != self.command_identity
            or self.result.engineering_status is not self.engineering_status
        ):
            raise ValueError("ERC receipt binding mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicRepairAssessment(Fingerprinted):
    assessment_id: str
    erc_result_fingerprint: str
    kind: SchematicRepairAssessmentKind
    rationale: str
    source_refs: tuple[str, ...]
    proposal_allowed: bool
    issue_refs: tuple[str, ...] = ()
    classification: SchematicErcIssueClassification = SchematicErcIssueClassification.UNCLASSIFIED
    correlations: tuple[SchematicErcCorrelation, ...] = ()

    _ids = field_validator("assessment_id", mode="before")(lambda value: ident(value, "assessment_id"))
    _fp = field_validator("erc_result_fingerprint", mode="before")(lambda value: fp(value, "erc_result_fingerprint"))
    _kind = field_validator("kind", mode="before")(lambda value: value if isinstance(value, SchematicRepairAssessmentKind) else SchematicRepairAssessmentKind(value))
    _rationale = field_validator("rationale", mode="before")(lambda value: text(value, "rationale", 2048))
    _refs = field_validator("source_refs", mode="before")(lambda value: _schematic_refs(value, "source_refs", required=True))
    _issue_refs = field_validator("issue_refs", mode="before")(
        lambda value: _schematic_refs(value, "issue_refs")
    )
    _classification = field_validator("classification", mode="before")(
        lambda value: value if isinstance(value, SchematicErcIssueClassification) else SchematicErcIssueClassification(value)
    )
    _correlations = field_validator("correlations", mode="before")(
        lambda value: _schematic_objects(value, "correlations", SchematicErcCorrelation)
    )
    _allowed = field_validator("proposal_allowed", mode="before")(lambda value: value if type(value) is bool else (_ for _ in ()).throw(TypeError("proposal_allowed is invalid")))

    @model_validator(mode="after")
    def _repair_boundary(self):
        if self.proposal_allowed and self.kind is not SchematicRepairAssessmentKind.DETERMINISTIC_RENDERING_DEFECT:
            raise ValueError("only deterministic rendering defects may produce repair proposals")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicRepairProposal(Fingerprinted):
    """A narrowly scoped repair proposal; it is never applied automatically."""

    repair_id: str
    erc_result_fingerprint: str
    base_proposal_id: str
    target_relative_path: str
    corrected_content: str
    corrected_content_fingerprint: str
    source_refs: tuple[str, ...]
    status: SchematicProductionStatus = SchematicProductionStatus.AWAITING_APPROVAL
    issue_refs: tuple[str, ...] = ()
    classification: SchematicErcIssueClassification = SchematicErcIssueClassification.UNCLASSIFIED
    root_cause: str = ""
    proposed_change: str = ""
    target_authority: str = ""
    affected_truth_refs: tuple[str, ...] = ()
    affected_plan_refs: tuple[str, ...] = ()
    supporting_evidence: tuple[str, ...] = ()
    source_erc_receipt_fingerprint: str | None = None
    expected_effect: str = ""
    risk: str = ""
    requires_user_approval: bool = True
    source_fingerprint: str | None = None

    _ids = field_validator("repair_id", "base_proposal_id", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _fps = field_validator("erc_result_fingerprint", "corrected_content_fingerprint", mode="before")(
        lambda value, info: fp(value, info.field_name)
    )
    _path = field_validator("target_relative_path", mode="before")(_schematic_path)
    _content = field_validator("corrected_content", mode="before")(
        lambda value: text(value, "corrected_content", 16 * 1024 * 1024)
    )
    _refs = field_validator("source_refs", mode="before")(
        lambda value: _schematic_refs(value, "source_refs", required=True)
    )
    _issue_refs = field_validator("issue_refs", "affected_truth_refs", "affected_plan_refs", "supporting_evidence", mode="before")(
        lambda value, info: _schematic_refs(value, info.field_name)
    )
    _classification = field_validator("classification", mode="before")(
        lambda value: value if isinstance(value, SchematicErcIssueClassification) else SchematicErcIssueClassification(value)
    )
    _proposal_text = field_validator(
        "root_cause", "proposed_change", "target_authority", "expected_effect", "risk", mode="before"
    )(lambda value, info: text(value or "", info.field_name, 2048))
    _requires_approval = field_validator("requires_user_approval", mode="before")(
        lambda value: value if type(value) is bool else (_ for _ in ()).throw(TypeError("requires_user_approval is invalid"))
    )
    _source_fp = field_validator("source_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "source_fingerprint")
    )
    _source_erc_receipt_fp = field_validator("source_erc_receipt_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "source_erc_receipt_fingerprint")
    )
    _status = field_validator("status", mode="before")(
        lambda value: value if isinstance(value, SchematicProductionStatus) else SchematicProductionStatus(value)
    )

    @model_validator(mode="after")
    def _binding(self):
        expected = "sha256:" + hashlib.sha256(self.corrected_content.encode("utf-8")).hexdigest()
        if self.corrected_content_fingerprint != expected:
            raise ValueError("schematic repair content fingerprint mismatch")
        if self.status is not SchematicProductionStatus.AWAITING_APPROVAL:
            raise ValueError("schematic repair proposal content is immutable")
        if not self.requires_user_approval:
            raise ValueError("schematic repair proposals require user approval")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicRepairDecision(Fingerprinted):
    decision_id: str
    repair_id: str
    proposal_fingerprint: str
    artifact_fingerprint: str
    action: SchematicRepairDecisionAction
    reviewer: str
    decided_at: datetime
    comment: str = ""
    edited_proposal_fingerprint: str | None = None

    _ids = field_validator("decision_id", "repair_id", "reviewer", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )
    _fps = field_validator("proposal_fingerprint", "artifact_fingerprint", mode="before")(
        lambda value, info: fp(value, info.field_name)
    )
    _action = field_validator("action", mode="before")(
        lambda value: value if isinstance(value, SchematicRepairDecisionAction) else SchematicRepairDecisionAction(value)
    )
    _comment = field_validator("comment", mode="before")(
        lambda value: text(value or "", "comment", 2048)
    )
    _edited_fp = field_validator("edited_proposal_fingerprint", mode="before")(
        lambda value: None if value is None else fp(value, "edited_proposal_fingerprint")
    )

    @field_validator("decided_at", mode="before")
    @classmethod
    def _time(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("decided_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _action_boundary(self):
        if self.action is SchematicRepairDecisionAction.EDIT_FIX and self.edited_proposal_fingerprint is None:
            raise ValueError("EDIT_FIX requires an edited proposal fingerprint")
        if self.action is not SchematicRepairDecisionAction.EDIT_FIX and self.edited_proposal_fingerprint is not None:
            raise ValueError("edited proposal fingerprint is only valid for EDIT_FIX")
        if self.action is SchematicRepairDecisionAction.WAIVE_WITH_REASON and not self.comment:
            raise ValueError("WAIVE_WITH_REASON requires a reason")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class SchematicRepairApplicationReceipt(Fingerprinted):
    """Immutable lineage for an approved repair applied through G3 and ERC."""

    receipt_id: str
    project_id: str
    conversation_id: str
    repair_id: str
    repair_proposal_fingerprint: str
    repair_decision_id: str
    repair_decision_fingerprint: str
    repair_action: SchematicRepairDecisionAction
    artifact_path: str
    pre_design_truth_fingerprint: str
    post_design_truth_fingerprint: str
    pre_schematic_plan_fingerprint: str
    post_schematic_plan_fingerprint: str
    pre_artifact_fingerprint: str
    post_artifact_fingerprint: str
    pre_erc_receipt_fingerprint: str
    pre_erc_result_fingerprint: str
    workspace_apply_receipt_fingerprint: str
    post_generation_receipt_fingerprint: str
    post_parse_validation_fingerprint: str
    post_erc_receipt_fingerprint: str
    post_erc_result_fingerprint: str
    status: SchematicRepairApplicationStatus = SchematicRepairApplicationStatus.APPLIED
    design_truth_changed: bool = False
    schematic_plan_changed: bool = False
    generator_changed: bool = False
    source_refs: tuple[str, ...]
    applied_at: datetime

    _ids = field_validator(
        "receipt_id", "project_id", "conversation_id", "repair_id", "repair_decision_id", mode="before"
    )(lambda value, info: ident(value, info.field_name))
    _fps = field_validator(
        "repair_proposal_fingerprint", "repair_decision_fingerprint",
        "pre_design_truth_fingerprint", "post_design_truth_fingerprint",
        "pre_schematic_plan_fingerprint", "post_schematic_plan_fingerprint",
        "pre_artifact_fingerprint", "post_artifact_fingerprint",
        "pre_erc_receipt_fingerprint", "pre_erc_result_fingerprint",
        "workspace_apply_receipt_fingerprint", "post_generation_receipt_fingerprint",
        "post_parse_validation_fingerprint", "post_erc_receipt_fingerprint",
        "post_erc_result_fingerprint", mode="before",
    )(lambda value, info: fp(value, info.field_name))
    _action = field_validator("repair_action", mode="before")(
        lambda value: value if isinstance(value, SchematicRepairDecisionAction) else SchematicRepairDecisionAction(value)
    )
    _status = field_validator("status", mode="before")(
        lambda value: value if isinstance(value, SchematicRepairApplicationStatus) else SchematicRepairApplicationStatus(value)
    )
    _path = field_validator("artifact_path", mode="before")(_schematic_path)
    _refs = field_validator("source_refs", mode="before")(
        lambda value: _schematic_refs(value, "source_refs", required=True)
    )

    @field_validator("applied_at", mode="before")
    @classmethod
    def _time(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("applied_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("design_truth_changed", "schematic_plan_changed", "generator_changed", mode="before")
    @classmethod
    def _changed(cls, value, info):
        if type(value) is not bool:
            raise TypeError(f"{info.field_name} is invalid")
        return value

    @model_validator(mode="after")
    def _boundary(self):
        if self.repair_action not in {
            SchematicRepairDecisionAction.APPROVE_FIX,
            SchematicRepairDecisionAction.EDIT_FIX,
        }:
            raise ValueError("only approved repair decisions may produce an application receipt")
        if self.status is SchematicRepairApplicationStatus.APPLIED and self.pre_artifact_fingerprint == self.post_artifact_fingerprint:
            raise ValueError("applied repair must produce a new artifact fingerprint")
        if self.design_truth_changed != (self.pre_design_truth_fingerprint != self.post_design_truth_fingerprint):
            raise ValueError("design truth lineage flag mismatch")
        if self.schematic_plan_changed != (self.pre_schematic_plan_fingerprint != self.post_schematic_plan_fingerprint):
            raise ValueError("schematic plan lineage flag mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class TruthLevel(StrEnum):
    EXPLICIT = "EXPLICIT"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"
    INVALIDATED = "INVALIDATED"


class DesignTruthOrigin(StrEnum):
    CREATE = "CREATE"
    REVERSE = "REVERSE"


class DesignTruthStatus(StrEnum):
    APPROVED = "APPROVED"


class EngineeringSessionMode(StrEnum):
    UNDETERMINED = "UNDETERMINED"
    CREATE = "CREATE"
    REVERSE = "REVERSE"
    HYBRID = "HYBRID"


class EvidenceType(StrEnum):
    SCHEMATIC_COMPONENT = "SCHEMATIC_COMPONENT"
    SCHEMATIC_PIN = "SCHEMATIC_PIN"
    SCHEMATIC_NET = "SCHEMATIC_NET"
    PCB_FOOTPRINT = "PCB_FOOTPRINT"
    PCB_ROUTE = "PCB_ROUTE"
    BOM_COMPONENT = "BOM_COMPONENT"
    FIRMWARE_SYMBOL = "FIRMWARE_SYMBOL"
    DATASHEET_PARAMETER = "DATASHEET_PARAMETER"
    USER_STATEMENT = "USER_STATEMENT"


class EngineeringEvidenceStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class DesignSource(StrEnum):
    ARCHITECTURE = "ARCHITECTURE"
    SCHEMATIC = "SCHEMATIC"
    PCB = "PCB"
    GERBER = "GERBER"
    BOM = "BOM"
    FIRMWARE = "FIRMWARE"
    DATASHEET = "DATASHEET"
    README = "README"
    IMAGE = "IMAGE"
    USER_INPUT = "USER_INPUT"
    OTHER = "OTHER"


class ArtifactType(StrEnum):
    SCHEMATIC = "SCHEMATIC"
    PCB = "PCB"
    BOM = "BOM"
    GERBER = "GERBER"
    FIRMWARE = "FIRMWARE"
    DATASHEET = "DATASHEET"
    README = "README"
    IMAGE = "IMAGE"
    OTHER = "OTHER"


class ArtifactStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    UNSUPPORTED = "UNSUPPORTED"
    PARSED = "PARSED"
    FAILED = "FAILED"


_LEGACY_CONVERSATION_ID = "legacy-conversation"


def _logical_path(value: object, field: str) -> str:
    checked = text(value, field, 1024).replace("\\", "/")
    parts = checked.split("/")
    if checked.startswith("/") or ":" in parts[0] or any(
        part in {"", ".", ".."} for part in parts
    ):
        raise ValueError(f"{field} is invalid")
    return "/".join(parts)


def _attributes(value: object, field: str = "attributes") -> tuple[tuple[str, str], ...]:
    values = _plan_sequence(value, field)
    result: list[tuple[str, str]] = []
    for item in values:
        if type(item) is not tuple or len(item) != 2:
            raise TypeError(f"{field} is invalid")
        key = ident(item[0], f"{field}_key")
        result.append((key, text(item[1], f"{field}_value", 1024)))
    if len({item[0] for item in result}) != len(result):
        raise ValueError(f"{field} must be unique")
    return tuple(result)


class Artifact(Fingerprinted):
    artifact_id: str
    tenant_id: str = "default-tenant"
    project_id: str
    type: ArtifactType
    name: str
    path_reference: str
    hash: str
    version: str = "workspace"
    status: ArtifactStatus = ArtifactStatus.AVAILABLE
    metadata: tuple[tuple[str, str], ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator("artifact_id", "tenant_id", "project_id", "version", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value):
        return text(value, "name", 512)

    @field_validator("path_reference", mode="before")
    @classmethod
    def _path(cls, value):
        return _logical_path(value, "path_reference")

    @field_validator("hash", mode="before")
    @classmethod
    def _hash(cls, value):
        return fp(value, "hash")

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value):
        return _attributes(value, "metadata")

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @model_validator(mode="after")
    def _bound(self):
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("artifact provenance identity mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArtifactEvidence(Fingerprinted):
    evidence_id: str
    tenant_id: str = "default-tenant"
    project_id: str
    conversation_id: str
    artifact_id: str
    evidence_type: EvidenceType
    source: DesignSource
    claim_type: str
    claim: str
    locator: str
    truth_level: TruthLevel
    confidence: float
    artifact_version: str
    content_fingerprint: str
    attributes: tuple[tuple[str, str], ...] = ()

    @field_validator(
        "evidence_id", "tenant_id", "project_id", "conversation_id", "artifact_id", "artifact_version", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("claim_type", mode="before")
    @classmethod
    def _claim_type(cls, value):
        return ident(value, "claim_type")

    @field_validator("claim", mode="before")
    @classmethod
    def _claim(cls, value):
        return text(value, "claim", 4096)

    @field_validator("locator", mode="before")
    @classmethod
    def _locator(cls, value):
        return text(value, "locator", 1024)

    @field_validator("content_fingerprint", mode="before")
    @classmethod
    def _content_fingerprint(cls, value):
        return fp(value, "content_fingerprint")

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @field_validator("attributes", mode="before")
    @classmethod
    def _attributes(cls, value):
        return _attributes(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringEvidence(Fingerprinted):
    evidence_id: str
    tenant_id: str = "default-tenant"
    project_id: str
    conversation_id: str
    artifact_id: str | None = None
    source: DesignSource
    evidence_type: EvidenceType
    claim_type: str
    claim: str
    locator: str
    truth_level: TruthLevel
    confidence: float
    verification_status: EngineeringEvidenceStatus = EngineeringEvidenceStatus.CANDIDATE
    artifact_version: str = "workspace"
    content_fingerprint: str
    attributes: tuple[tuple[str, str], ...] = ()
    provenance: tuple[PlanProvenance, ...] = ()

    @field_validator(
        "evidence_id", "tenant_id", "project_id", "conversation_id", "artifact_version", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("artifact_id", mode="before")
    @classmethod
    def _artifact_id(cls, value):
        return None if value is None else ident(value, "artifact_id")

    @field_validator("claim_type", mode="before")
    @classmethod
    def _claim_type(cls, value):
        return ident(value, "claim_type")

    @field_validator("claim", mode="before")
    @classmethod
    def _claim(cls, value):
        return text(value, "claim", 4096)

    @field_validator("locator", mode="before")
    @classmethod
    def _locator(cls, value):
        return text(value, "locator", 1024)

    @field_validator("content_fingerprint", mode="before")
    @classmethod
    def _content_fingerprint(cls, value):
        return fp(value, "content_fingerprint")

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @field_validator("attributes", mode="before")
    @classmethod
    def _attributes(cls, value):
        return _attributes(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _plan_sequence(value, "provenance")

    @model_validator(mode="after")
    def _bound(self):
        if any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("engineering evidence provenance identity mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringEvidenceBundle(Fingerprinted):
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset({"status", "bundle_fingerprint"})
    bundle_id: str
    tenant_id: str = "default-tenant"
    project_id: str
    conversation_id: str
    artifact_fingerprints: tuple[str, ...]
    evidence: tuple[EngineeringEvidence, ...]
    status: EngineeringEvidenceStatus = EngineeringEvidenceStatus.CANDIDATE
    bundle_fingerprint: str = "sha256:" + "0" * 64

    @field_validator("bundle_id", "tenant_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("artifact_fingerprints", mode="before")
    @classmethod
    def _artifact_fingerprints(cls, value):
        values = tuple(fp(item, "artifact_fingerprint") for item in _plan_sequence(value, "artifact_fingerprints"))
        if len(values) != len(set(values)):
            raise ValueError("artifact fingerprints must be unique")
        return values

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value):
        return _plan_sequence(value, "evidence")

    @field_validator("bundle_fingerprint", mode="before")
    @classmethod
    def _bundle_fingerprint(cls, value):
        return fp(value, "bundle_fingerprint")

    @model_validator(mode="after")
    def _bound(self):
        if not self.evidence:
            raise ValueError("engineering evidence bundle cannot be empty")
        if any(
            item.tenant_id != self.tenant_id
            or item.project_id != self.project_id
            or item.conversation_id != self.conversation_id
            for item in self.evidence
        ):
            raise ValueError("engineering evidence bundle identity mismatch")
        artifact_ids = {item.artifact_id for item in self.evidence if item.artifact_id is not None}
        if self.artifact_fingerprints and not artifact_ids:
            raise ValueError("engineering evidence bundle has no artifact binding")
        if self.status is EngineeringEvidenceStatus.APPROVED and any(
            item.verification_status is not EngineeringEvidenceStatus.APPROVED
            for item in self.evidence
        ):
            raise ValueError("approved bundle contains unapproved evidence")
        expected = self._stable_bundle_fingerprint()
        if self.bundle_fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "bundle_fingerprint", expected)
        elif self.bundle_fingerprint != expected:
            raise ValueError("engineering evidence bundle fingerprint mismatch")
        return self

    def _stable_bundle_fingerprint(self) -> str:
        """Fingerprint bundle contents without binding the review lifecycle.

        Evidence approval is a lifecycle transition, not a content edit. The
        bundle fingerprint therefore ignores both bundle status and each
        evidence item's lifecycle status/fingerprint, while retaining every
        source claim, locator, artifact content fingerprint and binding.
        """

        payload = self.model_dump(
            mode="json", exclude={"fingerprint", "bundle_fingerprint", "status"}
        )
        for item in payload.get("evidence", ()):
            item["verification_status"] = EngineeringEvidenceStatus.CANDIDATE.value
            item["fingerprint"] = "sha256:" + "0" * 64
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @property
    def content_fingerprint(self) -> str:
        return self.bundle_fingerprint

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArtifactCoverage(Fingerprinted):
    tenant_id: str = "default-tenant"
    project_id: str
    conversation_id: str
    schematic_status: ArtifactStatus = ArtifactStatus.MISSING
    pcb_status: ArtifactStatus = ArtifactStatus.MISSING
    bom_status: ArtifactStatus = ArtifactStatus.MISSING
    firmware_status: ArtifactStatus = ArtifactStatus.MISSING
    gerber_status: ArtifactStatus = ArtifactStatus.MISSING
    documentation_status: ArtifactStatus = ArtifactStatus.MISSING
    completeness_score: float
    missing_recommendations: tuple[str, ...] = ()

    @field_validator("tenant_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("completeness_score", mode="before")
    @classmethod
    def _score(cls, value):
        return confidence(value)

    @field_validator("missing_recommendations", mode="before")
    @classmethod
    def _missing(cls, value):
        return tuple(text(item, "missing_recommendation", 256) for item in _plan_sequence(value, "missing_recommendations"))

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArtifactInventory(Fingerprinted):
    tenant_id: str = "default-tenant"
    project_id: str
    conversation_id: str
    artifacts: tuple[Artifact, ...]

    @field_validator("tenant_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("artifacts", mode="before")
    @classmethod
    def _artifacts(cls, value):
        return _plan_sequence(value, "artifacts")

    @model_validator(mode="after")
    def _bound(self):
        if any(item.tenant_id != self.tenant_id or item.project_id != self.project_id for item in self.artifacts):
            raise ValueError("artifact inventory identity mismatch")
        ids = tuple(item.artifact_id for item in self.artifacts)
        if len(ids) != len(set(ids)):
            raise ValueError("artifact inventory ids must be unique")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ArtifactIntelligenceSnapshot(Fingerprinted):
    tenant_id: str = "default-tenant"
    project_id: str
    conversation_id: str
    inventory: ArtifactInventory
    coverage: ArtifactCoverage
    engineering_evidence_bundle: EngineeringEvidenceBundle | None = None
    review_id: str | None = None

    @field_validator("tenant_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("inventory", "coverage", "engineering_evidence_bundle", mode="before")
    @classmethod
    def _objects(cls, value, info):
        if value is None:
            return None
        return value

    @field_validator("review_id", mode="before")
    @classmethod
    def _review_id(cls, value):
        return None if value is None else ident(value, "review_id")

    @model_validator(mode="after")
    def _bound(self):
        if (
            self.inventory.tenant_id != self.tenant_id
            or self.inventory.project_id != self.project_id
            or self.inventory.conversation_id != self.conversation_id
            or self.coverage.tenant_id != self.tenant_id
            or self.coverage.project_id != self.project_id
            or self.coverage.conversation_id != self.conversation_id
        ):
            raise ValueError("artifact intelligence snapshot identity mismatch")
        bundle = self.engineering_evidence_bundle
        if bundle is not None and (
            bundle.tenant_id != self.tenant_id
            or bundle.project_id != self.project_id
            or bundle.conversation_id != self.conversation_id
        ):
            raise ValueError("artifact intelligence evidence identity mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class DesignEvidenceBinding(Fingerprinted):
    binding_id: str
    entity_id: str
    project_id: str
    source: DesignSource
    source_id: str
    artifact_ref: str
    locator: str | None = None
    artifact_revision: str | None = None
    evidence_id: str | None = None
    artifact_content_fingerprint: str | None = None
    source_fingerprint: str
    relationship: str
    truth_level: TruthLevel

    @field_validator(
        "binding_id", "entity_id", "project_id", "source_id", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("artifact_ref", "relationship", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("locator", mode="before")
    @classmethod
    def _locator(cls, value):
        return None if value is None else text(value, "locator", 256)

    @field_validator("artifact_revision", mode="before")
    @classmethod
    def _revision(cls, value):
        return None if value is None else ident(value, "artifact_revision")

    @field_validator("evidence_id", mode="before")
    @classmethod
    def _evidence_id(cls, value):
        return None if value is None else ident(value, "evidence_id")

    @field_validator("artifact_content_fingerprint", mode="before")
    @classmethod
    def _artifact_content_fingerprint(cls, value):
        return None if value is None else fp(value, "artifact_content_fingerprint")

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value):
        return fp(value, "source_fingerprint")

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class DesignComponent(Fingerprinted):
    component_id: str
    name: str
    type: str
    category: str
    description: str
    # Explicit KiCad library binding; absence is a readiness blocker, never an
    # invitation to infer a symbol from the component name or category.
    symbol_binding: str | None = None
    confidence: float
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("component_id", "type", "category", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("symbol_binding", mode="before")
    @classmethod
    def _symbol_binding(cls, value):
        return None if value is None else text(value, "symbol_binding", 512)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value):
        refs = tuple(ident(item, "evidence_ref") for item in _plan_sequence(value, "evidence_refs"))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("design component evidence references are invalid")
        return refs

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class DesignPin(Fingerprinted):
    pin_id: str
    component_id: str
    pin_name: str
    pin_number: str
    function: str
    signal_id: str | None = None
    intentionally_unconnected: bool = False
    unconnected_reason: str | None = None
    confidence: float
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("pin_id", "component_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("pin_name", "pin_number", "function", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 512)

    @field_validator("signal_id", mode="before")
    @classmethod
    def _signal(cls, value):
        return None if value is None else ident(value, "signal_id")

    @field_validator("unconnected_reason", mode="before")
    @classmethod
    def _unconnected_reason(cls, value):
        return None if value is None else text(value, "unconnected_reason", 1024)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        if self.intentionally_unconnected or self.unconnected_reason is not None:
            return ()
        return (
            canonical(
                self,
                exclude={"fingerprint", "intentionally_unconnected", "unconnected_reason"},
            ),
        )

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value):
        refs = tuple(ident(item, "evidence_ref") for item in _plan_sequence(value, "evidence_refs"))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("design pin evidence references are invalid")
        return refs

    @model_validator(mode="after")
    def _connection_state(self):
        if self.intentionally_unconnected and (self.signal_id is not None or self.unconnected_reason is None):
            raise ValueError("intentionally unconnected pin requires reason and no signal")
        if not self.intentionally_unconnected and self.unconnected_reason is not None:
            raise ValueError("connected design pin cannot have unconnected reason")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class DesignSignal(Fingerprinted):
    signal_id: str
    name: str
    type: str
    source: str
    targets: tuple[str, ...]
    protocol: str
    direction: str
    confidence: float
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("signal_id", "type", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator(
        "name", "source", "protocol", "direction", mode="before"
    )
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("targets", mode="before")
    @classmethod
    def _targets(cls, value):
        refs = tuple(text(item, "target", 512) for item in _plan_sequence(value, "targets"))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("design signal targets are invalid")
        return refs

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value):
        refs = tuple(ident(item, "evidence_ref") for item in _plan_sequence(value, "evidence_refs"))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("design signal evidence references are invalid")
        return refs

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class PeripheralBinding(Fingerprinted):
    binding_id: str
    peripheral: str
    component: str
    pin_refs: tuple[str, ...]
    function: str
    confidence: float
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("binding_id", "peripheral", "component", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("pin_refs", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        refs = tuple(ident(item, info.field_name.removesuffix("s")) for item in _plan_sequence(value, info.field_name))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} are invalid")
        return refs

    @field_validator("function", mode="before")
    @classmethod
    def _function(cls, value):
        return text(value, "function", 1024)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class NetBinding(Fingerprinted):
    binding_id: str
    net_name: str
    nodes: tuple[str, ...]
    signal: str
    purpose: str
    confidence: float
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("binding_id", "signal", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("net_name", "purpose", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("nodes", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        refs = tuple(text(item, "node", 512) for item in _plan_sequence(value, info.field_name)) if info.field_name == "nodes" else tuple(ident(item, "evidence_ref") for item in _plan_sequence(value, info.field_name))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} are invalid")
        return refs

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class DesignInterface(Fingerprinted):
    interface_id: str
    source: str
    target: str
    protocol: str
    direction: str
    confidence: float
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("interface_id", "source", "target", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("protocol", "direction", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value):
        refs = tuple(ident(item, "evidence_ref") for item in _plan_sequence(value, "evidence_refs"))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError("design interface evidence references are invalid")
        return refs

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class DesignPowerDomain(Fingerprinted):
    power_domain_id: str
    name: str
    component_refs: tuple[str, ...]
    purpose: str
    voltage: str | None = None
    ground_net: str | None = None
    confidence: float
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("power_domain_id", mode="before")
    @classmethod
    def _id(cls, value):
        return ident(value, "power_domain_id")

    @field_validator("name", "purpose", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("voltage", "ground_net", mode="before")
    @classmethod
    def _optional_power_fact(cls, value, info):
        return None if value is None else text(value, info.field_name, 128)

    @field_validator("component_refs", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        refs = tuple(ident(item, info.field_name.removesuffix("s")) for item in _plan_sequence(value, info.field_name))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} are invalid")
        return refs

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return confidence(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class FirmwareBinding(Fingerprinted):
    binding_id: str
    firmware_symbol: str
    source_file: str
    hardware_refs: tuple[str, ...]
    function: str
    truth_level: TruthLevel
    evidence_refs: tuple[str, ...]

    @field_validator("binding_id", mode="before")
    @classmethod
    def _id(cls, value):
        return ident(value, "binding_id")

    @field_validator("firmware_symbol", mode="before")
    @classmethod
    def _symbol(cls, value):
        return ident(value, "firmware_symbol")

    @field_validator("source_file", "function", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 1024)

    @field_validator("hardware_refs", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        refs = tuple(ident(item, "hardware_ref" if info.field_name == "hardware_refs" else "evidence_ref") for item in _plan_sequence(value, info.field_name))
        if not refs or len(refs) != len(set(refs)):
            raise ValueError(f"{info.field_name} are invalid")
        return refs

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class DesignTruth(Fingerprinted):
    truth_id: str
    tenant_id: str = "default-tenant"
    project_id: str
    design_revision: str
    conversation_id: str = _LEGACY_CONVERSATION_ID
    origin: DesignTruthOrigin = DesignTruthOrigin.CREATE
    status: DesignTruthStatus = DesignTruthStatus.APPROVED
    architecture_id: str | None = None
    components: tuple[DesignComponent, ...] = ()
    signals: tuple[DesignSignal, ...] = ()
    pins: tuple[DesignPin, ...] = ()
    peripherals: tuple[PeripheralBinding, ...] = ()
    nets: tuple[NetBinding, ...] = ()
    interfaces: tuple[DesignInterface, ...] = ()
    power_domains: tuple[DesignPowerDomain, ...] = ()
    firmware_bindings: tuple[FirmwareBinding, ...] = ()
    evidence_bindings: tuple[DesignEvidenceBinding, ...]
    provenance: tuple[PlanProvenance, ...]

    @field_validator(
        "truth_id", "tenant_id", "project_id", "conversation_id", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("architecture_id", mode="before")
    @classmethod
    def _architecture_id(cls, value):
        return None if value is None else ident(value, "architecture_id")

    @field_validator("design_revision", mode="before")
    @classmethod
    def _revision(cls, value):
        return ident(value, "design_revision")

    @field_validator(
        "components", "signals", "pins", "peripherals", "nets", "interfaces",
        "power_domains", "firmware_bindings", "evidence_bindings", "provenance",
        mode="before",
    )
    @classmethod
    def _objects(cls, value, info):
        return _plan_sequence(value, info.field_name)

    @model_validator(mode="after")
    def _bound(self):
        if self.origin is DesignTruthOrigin.CREATE and self.architecture_id is None:
            raise ValueError("create design truth requires architecture")
        if self.origin is DesignTruthOrigin.REVERSE and self.architecture_id is not None:
            raise ValueError("reverse design truth cannot bind architecture")
        components = {item.component_id for item in self.components}
        pins = {item.pin_id for item in self.pins}
        signals = {item.signal_id for item in self.signals}
        interfaces = {item.interface_id for item in self.interfaces}
        power_domains = {item.power_domain_id for item in self.power_domains}
        peripherals = {item.binding_id for item in self.peripherals}
        nets = {item.binding_id for item in self.nets}
        firmware_bindings = {item.binding_id for item in self.firmware_bindings}
        binding_entities = (*peripherals, *nets, *firmware_bindings)
        child_ids = (*components, *pins, *signals, *interfaces, *power_domains, *binding_entities)
        binding_ids = {item.binding_id for item in self.evidence_bindings}
        if len(child_ids) != len(set(child_ids)):
            raise ValueError("design truth entity ids must be unique")
        if len(binding_ids) != len(self.evidence_bindings) or not binding_ids:
            raise ValueError("design truth evidence bindings are invalid")
        if set(child_ids) & binding_ids:
            raise ValueError("design truth entity and evidence ids must be disjoint")
        if not self.provenance:
            raise ValueError("design truth requires provenance")
        for item in self.components:
            if not set(item.evidence_refs) <= binding_ids:
                raise ValueError("design component evidence reference is invalid")
        for item in self.pins:
            if item.component_id not in components or (item.signal_id is not None and item.signal_id not in signals):
                raise ValueError("design pin reference is invalid")
            if not set(item.evidence_refs) <= binding_ids:
                raise ValueError("design pin evidence reference is invalid")
        for item in self.signals:
            if not set(item.evidence_refs) <= binding_ids:
                raise ValueError("design signal evidence reference is invalid")
        for item in self.interfaces:
            if item.source not in components or item.target not in components:
                raise ValueError("design interface component reference is invalid")
            if not set(item.evidence_refs) <= binding_ids:
                raise ValueError("design interface evidence reference is invalid")
        for item in self.peripherals:
            if item.component not in components or not set(item.pin_refs) <= pins:
                raise ValueError("peripheral binding reference is invalid")
            if not set(item.evidence_refs) <= binding_ids:
                raise ValueError("peripheral evidence reference is invalid")
        for item in self.nets:
            if item.signal not in signals or not set(item.evidence_refs) <= binding_ids:
                raise ValueError("net binding reference is invalid")
        for item in self.power_domains:
            if not set(item.component_refs) <= components or not set(item.evidence_refs) <= binding_ids:
                raise ValueError("power domain reference is invalid")
        known_hardware = components | pins | interfaces | power_domains | peripherals | nets
        for item in self.firmware_bindings:
            if not set(item.hardware_refs) <= known_hardware or not set(item.evidence_refs) <= binding_ids:
                raise ValueError("firmware binding reference is invalid")
        known_entities = set(child_ids) | {self.truth_id}
        for binding in self.evidence_bindings:
            if binding.project_id != self.project_id or binding.entity_id not in known_entities:
                raise ValueError("design evidence identity mismatch")
            if binding.evidence_id is not None and not binding.artifact_ref:
                raise ValueError("design evidence artifact binding is invalid")
        return self

    def _legacy_fingerprint_candidates(self) -> tuple[str, ...]:
        """Accept the pre-tenant-bound Create snapshot during one-version migration."""

        candidates: list[str] = []
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        if all(
            not pin.intentionally_unconnected and pin.unconnected_reason is None
            for pin in self.pins
        ):
            legacy_pin_payload = copy.deepcopy(payload)
            for pin in legacy_pin_payload["pins"]:
                pin.pop("intentionally_unconnected", None)
                pin.pop("unconnected_reason", None)
            encoded = json.dumps(
                legacy_pin_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        tenant_values = [self.tenant_id]
        if self.tenant_id != "default-tenant":
            tenant_values.append("default-tenant")
        conversation_values = [self.conversation_id]
        if self.conversation_id != _LEGACY_CONVERSATION_ID:
            conversation_values.append(_LEGACY_CONVERSATION_ID)
        for tenant_id in tenant_values:
            for conversation_id in conversation_values:
                candidate = dict(payload)
                candidate["tenant_id"] = tenant_id
                candidate["conversation_id"] = conversation_id
                encoded = json.dumps(
                    candidate,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
                legacy_without_status = dict(candidate)
                legacy_without_status.pop("status", None)
                encoded = json.dumps(
                    legacy_without_status,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        if "conversation_id" not in self.model_fields_set:
            for tenant_id in tenant_values:
                legacy_without_conversation = dict(payload)
                legacy_without_conversation.pop("conversation_id", None)
                legacy_without_conversation["tenant_id"] = tenant_id
                encoded = json.dumps(
                    legacy_without_conversation,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
                legacy_without_status = dict(legacy_without_conversation)
                legacy_without_status.pop("status", None)
                encoded = json.dumps(
                    legacy_without_status,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                candidates.append("sha256:" + hashlib.sha256(encoded).hexdigest())
        return tuple(dict.fromkeys(candidates))

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class KnowledgeConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EngineeringKnowledgeEntityType(StrEnum):
    SYSTEM = "SYSTEM"
    SUBSYSTEM = "SUBSYSTEM"
    COMPONENT = "COMPONENT"
    INTERFACE = "INTERFACE"
    SIGNAL = "SIGNAL"
    FUNCTION = "FUNCTION"
    DESIGN_DECISION = "DESIGN_DECISION"
    RISK = "RISK"
    LEARNING_TOPIC = "LEARNING_TOPIC"


class EngineeringKnowledgeRelationshipType(StrEnum):
    CONTAINS = "CONTAINS"
    CONNECTS = "CONNECTS"
    CONTROLS = "CONTROLS"
    DEPENDS_ON = "DEPENDS_ON"
    IMPLEMENTS = "IMPLEMENTS"
    VERIFIED_BY = "VERIFIED_BY"
    MODIFIED_BY = "MODIFIED_BY"


def _knowledge_refs(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if required and not refs:
        raise ValueError(f"{field} are invalid")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _knowledge_texts(value: object, field: str) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    values = tuple(text(item, field.removesuffix("s"), 1024) for item in tuple_values(value, field))
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must be unique")
    return values


def _knowledge_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


class EngineeringKnowledgeEntity(Fingerprinted):
    entity_id: str
    knowledge_id: str
    entity_type: EngineeringKnowledgeEntityType
    name: str
    description: str
    responsibility: str
    truth_level: TruthLevel
    confidence: KnowledgeConfidence
    source_truth_refs: tuple[str, ...]
    evidence_bindings: tuple[str, ...]

    @field_validator("entity_id", "knowledge_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("name", "description", "responsibility", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("source_truth_refs", "evidence_bindings", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _knowledge_refs(value, info.field_name, required=True)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringKnowledgeRelationship(Fingerprinted):
    relationship_id: str
    knowledge_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: EngineeringKnowledgeRelationshipType
    description: str
    evidence_bindings: tuple[str, ...]

    @field_validator(
        "relationship_id", "knowledge_id", "source_entity_id", "target_entity_id", mode="before"
    )
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value):
        return text(value, "description", 2048)

    @field_validator("evidence_bindings", mode="before")
    @classmethod
    def _evidence(cls, value):
        return _knowledge_refs(value, "evidence_bindings", required=True)

    @model_validator(mode="after")
    def _no_self_relation(self):
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("knowledge relationship cannot reference itself")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringLearningTopic(Fingerprinted):
    topic_id: str
    title: str
    description: str
    related_entities: tuple[str, ...]
    difficulty: str
    source_refs: tuple[str, ...]
    evidence_bindings: tuple[str, ...]

    @field_validator("topic_id", mode="before")
    @classmethod
    def _id(cls, value):
        return ident(value, "topic_id")

    @field_validator("title", "description", "difficulty", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 2048)

    @field_validator("related_entities", "source_refs", "evidence_bindings", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _knowledge_refs(value, info.field_name, required=True)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class EngineeringKnowledgeModel(Fingerprinted):
    schema_version: Literal["1.0"] = "1.0"
    knowledge_id: str
    project_id: str
    conversation_id: str
    source_truth_fingerprint: str
    title: str
    summary: str
    system_context: tuple[str, ...] = ()
    subsystems: tuple[str, ...] = ()
    entities: tuple[EngineeringKnowledgeEntity, ...] = ()
    relationships: tuple[EngineeringKnowledgeRelationship, ...] = ()
    learning_topics: tuple[EngineeringLearningTopic, ...] = ()
    modification_points: tuple[str, ...] = ()
    design_decisions: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    evidence_bindings: tuple[str, ...]
    provenance: tuple[PlanProvenance, ...]

    @field_validator("knowledge_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("source_truth_fingerprint", mode="before")
    @classmethod
    def _source_truth_fingerprint(cls, value):
        return fp(value, "source_truth_fingerprint")

    @field_validator("title", "summary", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("system_context", "subsystems", "evidence_bindings", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _knowledge_refs(value, info.field_name, required=info.field_name == "evidence_bindings")

    @field_validator("modification_points", "design_decisions", "risks", mode="before")
    @classmethod
    def _texts(cls, value, info):
        return _knowledge_texts(value, info.field_name)

    @field_validator("entities", "relationships", "learning_topics", mode="before")
    @classmethod
    def _objects(cls, value, info):
        model = {
            "entities": EngineeringKnowledgeEntity,
            "relationships": EngineeringKnowledgeRelationship,
            "learning_topics": EngineeringLearningTopic,
        }[info.field_name]
        return _knowledge_objects(value, info.field_name, model)

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value):
        return _knowledge_objects(value, "provenance", PlanProvenance)

    @model_validator(mode="after")
    def _bound(self):
        entity_ids = tuple(item.entity_id for item in self.entities)
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("knowledge entity ids must be unique")
        entity_set = set(entity_ids)
        if any(item.knowledge_id != self.knowledge_id for item in self.entities):
            raise ValueError("knowledge entity identity mismatch")
        if not set(self.system_context) <= entity_set or not set(self.subsystems) <= entity_set:
            raise ValueError("knowledge context reference is invalid")
        if any(
            next(item for item in self.entities if item.entity_id == entity_id).entity_type
            is not EngineeringKnowledgeEntityType.SYSTEM
            for entity_id in self.system_context
        ):
            raise ValueError("knowledge system context reference is invalid")
        if any(
            next(item for item in self.entities if item.entity_id == entity_id).entity_type
            is not EngineeringKnowledgeEntityType.SUBSYSTEM
            for entity_id in self.subsystems
        ):
            raise ValueError("knowledge subsystem reference is invalid")
        binding_set = set(self.evidence_bindings)
        if any(not set(item.evidence_bindings) <= binding_set for item in self.entities):
            raise ValueError("knowledge entity evidence binding is invalid")
        relationship_ids = tuple(item.relationship_id for item in self.relationships)
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("knowledge relationship ids must be unique")
        for item in self.relationships:
            if item.knowledge_id != self.knowledge_id:
                raise ValueError("knowledge relationship identity mismatch")
            if item.source_entity_id not in entity_set or item.target_entity_id not in entity_set:
                raise ValueError("knowledge relationship entity reference is invalid")
            if not set(item.evidence_bindings) <= binding_set:
                raise ValueError("knowledge relationship evidence binding is invalid")
        topic_ids = tuple(item.topic_id for item in self.learning_topics)
        if len(topic_ids) != len(set(topic_ids)):
            raise ValueError("learning topic ids must be unique")
        for item in self.learning_topics:
            if not set(item.related_entities) <= entity_set:
                raise ValueError("learning topic entity reference is invalid")
            if not set(item.evidence_bindings) <= binding_set:
                raise ValueError("learning topic evidence binding is invalid")
        if not self.provenance or any(item.project_id != self.project_id for item in self.provenance):
            raise ValueError("knowledge provenance identity mismatch")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class LearningMode(StrEnum):
    OVERVIEW = "OVERVIEW"
    ENGINEERING = "ENGINEERING"
    DEEP_DIVE = "DEEP_DIVE"


class TeachingScope(StrEnum):
    OVERVIEW = "OVERVIEW"
    ENGINEERING = "ENGINEERING"
    DEEP_DIVE = "DEEP_DIVE"


class TeachingDifficulty(StrEnum):
    FOUNDATION = "FOUNDATION"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"


class TeachingStepType(StrEnum):
    SYSTEM_OVERVIEW = "SYSTEM_OVERVIEW"
    ARCHITECTURE = "ARCHITECTURE"
    COMPONENT = "COMPONENT"
    INTERFACE = "INTERFACE"
    SIGNAL = "SIGNAL"
    VERIFICATION = "VERIFICATION"


class TeachingSubjectEntityType(StrEnum):
    SYSTEM = "SYSTEM"
    SUBSYSTEM = "SUBSYSTEM"
    COMPONENT = "COMPONENT"
    INTERFACE = "INTERFACE"
    SIGNAL = "SIGNAL"
    FUNCTION = "FUNCTION"
    DESIGN_DECISION = "DESIGN_DECISION"


def _teaching_refs(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if type(value) is list:
        value = tuple(value)
    refs = tuple(ident(item, field.removesuffix("s")) for item in tuple_values(value, field))
    if required and not refs:
        raise ValueError(f"{field} are invalid")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{field} must be unique")
    return refs


def _teaching_objects(value: object, field: str, model: type[Fingerprinted]) -> tuple:
    if type(value) is list:
        value = tuple(value)
    return tuple(
        model.model_validate(
            copy.deepcopy(item.model_dump(mode="python") if isinstance(item, model) else item),
            strict=False,
        )
        for item in tuple_values(value, field)
    )


class TeachingSubject(Fingerprinted):
    subject_id: str
    entity_id: str
    entity_type: TeachingSubjectEntityType
    title: str
    summary: str
    related_entities: tuple[str, ...] = ()
    source_refs: tuple[str, ...]
    evidence_bindings: tuple[str, ...]
    confidence: KnowledgeConfidence

    @field_validator("subject_id", "entity_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("entity_type", mode="before")
    @classmethod
    def _entity_type(cls, value):
        return value if isinstance(value, TeachingSubjectEntityType) else TeachingSubjectEntityType(value)

    @field_validator("title", "summary", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 4096)

    @field_validator("related_entities", "source_refs", "evidence_bindings", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _teaching_refs(value, info.field_name, required=info.field_name != "related_entities")

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value):
        return value if isinstance(value, KnowledgeConfidence) else KnowledgeConfidence(value)

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class TeachingLearningStep(Fingerprinted):
    step_id: str
    step_type: TeachingStepType
    title: str
    step_summary: str
    related_entities: tuple[str, ...] = ()
    order: int
    evidence_refs: tuple[str, ...] = ()

    @field_validator("step_id", mode="before")
    @classmethod
    def _id(cls, value):
        return ident(value, "step_id")

    @field_validator("step_type", mode="before")
    @classmethod
    def _step_type(cls, value):
        return value if isinstance(value, TeachingStepType) else TeachingStepType(value)

    @field_validator("title", "step_summary", mode="before")
    @classmethod
    def _text(cls, value, info):
        return text(value, info.field_name, 512)

    @field_validator("related_entities", "evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _teaching_refs(value, info.field_name)

    @field_validator("order", mode="before")
    @classmethod
    def _order(cls, value):
        if type(value) is not int or value < 1:
            raise ValueError("step order is invalid")
        return value

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class TeachingLearningPath(Fingerprinted):
    path_id: str
    teaching_context_id: str
    steps: tuple[TeachingLearningStep, ...] = ()

    @field_validator("path_id", "teaching_context_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, value):
        return _teaching_objects(value, "steps", TeachingLearningStep)

    @model_validator(mode="after")
    def _ordered(self):
        if tuple(item.order for item in self.steps) != tuple(range(1, len(self.steps) + 1)):
            raise ValueError("teaching learning steps must be ordered")
        if len({item.step_id for item in self.steps}) != len(self.steps):
            raise ValueError("teaching learning step ids must be unique")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class TeachingContext(Fingerprinted):
    schema_version: Literal["1.0"] = "1.0"
    teaching_context_id: str
    project_id: str
    conversation_id: str
    knowledge_id: str
    subject: TeachingSubject
    scope: TeachingScope
    difficulty: TeachingDifficulty
    learning_mode: LearningMode
    related_entities: tuple[str, ...] = ()
    learning_path: TeachingLearningPath
    evidence_bindings: tuple[str, ...] = ()
    source_knowledge_schema_version: Literal["1.0"] = "1.0"
    source_knowledge_fingerprint: str

    @field_validator("teaching_context_id", "project_id", "conversation_id", "knowledge_id", mode="before")
    @classmethod
    def _ids(cls, value, info):
        return ident(value, info.field_name)

    @field_validator("subject", mode="before")
    @classmethod
    def _subject(cls, value):
        return _teaching_objects((value,), "subject", TeachingSubject)[0]

    @field_validator("scope", mode="before")
    @classmethod
    def _scope(cls, value):
        return value if isinstance(value, TeachingScope) else TeachingScope(value)

    @field_validator("difficulty", mode="before")
    @classmethod
    def _difficulty(cls, value):
        return value if isinstance(value, TeachingDifficulty) else TeachingDifficulty(value)

    @field_validator("learning_mode", mode="before")
    @classmethod
    def _learning_mode(cls, value):
        return value if isinstance(value, LearningMode) else LearningMode(value)

    @field_validator("related_entities", "evidence_bindings", mode="before")
    @classmethod
    def _refs(cls, value, info):
        return _teaching_refs(value, info.field_name)

    @field_validator("learning_path", mode="before")
    @classmethod
    def _learning_path(cls, value):
        return _teaching_objects((value,), "learning_path", TeachingLearningPath)[0]

    @field_validator("source_knowledge_fingerprint", mode="before")
    @classmethod
    def _source_fingerprint(cls, value):
        return fp(value, "source_knowledge_fingerprint")

    @model_validator(mode="after")
    def _bound(self):
        if self.scope.value != self.learning_mode.value:
            raise ValueError("teaching scope and learning mode mismatch")
        if self.learning_path.teaching_context_id != self.teaching_context_id:
            raise ValueError("teaching learning path context mismatch")
        if self.subject.entity_id in self.related_entities:
            raise ValueError("teaching related entities cannot contain subject")
        return self

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


class ProposalReviewHandoff(Fingerprinted):
    source_type: str
    source_id: str
    project_id: str
    proposal_fingerprint: str
    evidence_refs: tuple[str, ...] = ()
    governance_reference: str

    @field_validator(
        "source_type", "source_id", "project_id", "governance_reference", mode="before"
    )
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fp(cls, v):
        from .common import fp

        return fp(v, "proposal_fingerprint")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, v):
        return tuple(
            text(x, "evidence_ref", 256) for x in _plan_sequence(v, "evidence_refs")
        )

    @classmethod
    def create(cls, **values):
        return finalize(cls, values)


SchematicProductionState.model_rebuild()


__all__ = (
    "ArchitectureBoundary",
    "ArchitectureComponent",
    "ArchitectureConfidence",
    "ArchitectureDecision",
    "ArchitectureDecisionType",
    "ArchitectureDesign",
    "ArchitectureDesignStatus",
    "ArchitectureEvidenceBinding",
    "ArchitectureFlow",
    "ArchitectureFlowType",
    "ArchitectureInterface",
    "ArchitectureInterfaceType",
    "ArchitectureLevel",
    "ArchitectureReviewDecision",
    "ArchitectureReviewFinding",
    "ArchitectureReviewFindingStatus",
    "ArchitectureReviewStatus",
    "ArchitectureVerification",
    "Artifact",
    "ArtifactCoverage",
    "ArtifactEvidence",
    "ArtifactIntelligenceSnapshot",
    "ArtifactInventory",
    "ArtifactStatus",
    "ArtifactType",
    "DesignComponent",
    "DesignEvidenceBinding",
    "DesignInterface",
    "DesignPin",
    "DesignPowerDomain",
    "DesignSignal",
    "DesignSource",
    "DesignTruth",
    "DesignTruthStatus",
    "EngineeringEvidence",
    "EngineeringEvidenceBundle",
    "EngineeringEvidenceStatus",
    "EngineeringKnowledgeEntity",
    "EngineeringKnowledgeEntityType",
    "EngineeringKnowledgeModel",
    "EngineeringKnowledgeRelationship",
    "EngineeringKnowledgeRelationshipType",
    "EngineeringLearningTopic",
    "EngineeringPlan",
    "EngineeringPlanNote",
    "EngineeringPlanPhase",
    "EngineeringPlanTask",
    "EngineeringRequirement",
    "EngineeringSessionMode",
    "EngineeringTask",
    "EngineeringWorkflow",
    "EngineeringWorkflowArtifact",
    "EngineeringWorkflowArtifactType",
    "EngineeringWorkflowItemStatus",
    "EngineeringWorkflowPhase",
    "EngineeringWorkflowPhaseType",
    "EngineeringWorkflowStatus",
    "EngineeringWorkflowTask",
    "EngineeringWorkflowTaskType",
    "EngineeringWorkflowVerification",
    "EvidenceType",
    "ExpectedArtifact",
    "FirmwareBinding",
    "FirmwareInterfaceRole",
    "FirmwareModule",
    "FirmwareModuleType",
    "FirmwareSpecification",
    "FirmwareTask",
    "FirmwareTaskStatus",
    "FirmwareTaskType",
    "FirmwareVerificationRequirement",
    "HardwareCandidateStatus",
    "HardwareCandidateType",
    "HardwareComponentCandidate",
    "HardwareDecision",
    "HardwareDecisionStatus",
    "HardwareDesignPlanStatus",
    "HardwareDesignSpecification",
    "HardwareDomain",
    "HardwareDomainType",
    "HardwareFirmwareBinding",
    "HardwareInterface",
    "HardwareInterfaceType",
    "ImplementationArtifactType",
    "ImplementationIntent",
    "ImplementationSpecification",
    "ImplementationStatus",
    "ImplementationTask",
    "ImplementationTaskType",
    "KnowledgeConfidence",
    "LearningMode",
    "NetBinding",
    "PCBDesignPlan",
    "PeripheralBinding",
    "PlanEvidenceBinding",
    "PlanOutput",
    "PlanPhaseStatus",
    "PlanProvenance",
    "PlanReviewDecision",
    "PlanReviewStatus",
    "PlanRisk",
    "PlanRiskLevel",
    "PlanStatus",
    "PlanTaskStatus",
    "PlanTaskType",
    "PlanVerificationStrategy",
    "ProposalReviewHandoff",
    "SchematicCircuitType",
    "SchematicDesignPlan",
    "SchematicComponentProposal",
    "SchematicErcCorrelation",
    "SchematicErcCorrelationStatus",
    "SchematicErcEngineeringStatus",
    "SchematicErcGateStatus",
    "SchematicErcIssue",
    "SchematicErcIssueClassification",
    "SchematicErcIssueSeverity",
    "SchematicErcIssueStatus",
    "SchematicErcReceipt",
    "SchematicErcResult",
    "SchematicErcStatus",
    "SchematicErcViolation",
    "SchematicParseValidationEvidence",
    "SchematicGenerationReceipt",
    "SchematicGenerationStatus",
    "SchematicNetProposal",
    "SchematicPinProposal",
    "SchematicPowerProposal",
    "SchematicProductionRequest",
    "SchematicProductionState",
    "SchematicProductionStatus",
    "SchematicProposal",
    "SchematicProposalDecision",
    "SchematicProposalDecisionStatus",
    "SchematicReadinessResult",
    "SchematicReadinessStatus",
    "SchematicRepairAssessment",
    "SchematicRepairAssessmentKind",
    "SchematicRepairDecision",
    "SchematicRepairDecisionAction",
    "SchematicRepairProposal",
    "SimulationDesignIntent",
    "SimulationEvidenceRequirement",
    "SimulationEvidenceType",
    "SimulationModel",
    "SimulationScenario",
    "SimulationScenarioType",
    "SimulationSpecification",
    "SimulationStatus",
    "SimulationTargetType",
    "TeachingContext",
    "TeachingDifficulty",
    "TeachingLearningPath",
    "TeachingLearningStep",
    "TeachingScope",
    "TeachingStepType",
    "TeachingSubject",
    "TeachingSubjectEntityType",
    "TruthLevel",
    "VerificationAcceptanceMapping",
    "VerificationCase",
    "VerificationCaseType",
    "VerificationEvidenceRequirement",
    "VerificationEvidenceType",
    "VerificationIntent",
    "VerificationSpecification",
    "VerificationStatus",
)
