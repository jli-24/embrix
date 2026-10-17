from __future__ import annotations

import copy

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
    fuse_projections,
)

from .contracts import deterministic_id
from .exceptions import MultimodalRejected
from .models import EngineeringObservation, validate_observation
from .observation import content_values


class MultimodalContextProjector:
    __slots__ = ()

    def project(
        self, observation: EngineeringObservation
    ) -> tuple[EngineeringContextEvidence, ...]:
        try:
            checked = validate_observation(observation)
            values = content_values(checked.structured_content)
            if not values:
                raise ValueError("visual observation has no projectable content")
            return tuple(
                EngineeringContextEvidence.create(
                    evidence_id=deterministic_id(
                        "visual", checked.observation_id, field, str(index)
                    ),
                    project_id=checked.artifact_reference.project_id,
                    source_type=ContextSourceType.MULTIMODAL_OBSERVATION,
                    subject=checked.artifact_reference.artifact_id,
                    summary=f"projected visual {field}: {value}",
                    fact_key=None,
                    fact_value=None,
                    source_reference=checked.artifact_reference.source_reference,
                    source_fingerprint=checked.fingerprint,
                    confidence=checked.confidence,
                    verification_status="PROJECTED",
                )
                for index, (field, value) in enumerate(values)
            )
        except Exception as error:
            raise MultimodalRejected() from error

    def fuse(
        self,
        *,
        question: ApplicationQuestionRequest,
        observation: EngineeringObservation,
        base_snapshot: EngineeringContextSnapshot | None,
    ) -> EngineeringContextSnapshot:
        try:
            checked_question = ApplicationQuestionRequest.model_validate(
                copy.deepcopy(question.model_dump(mode="python"))
            )
            checked_observation = validate_observation(observation)
            if (
                checked_observation.artifact_reference.project_id
                != checked_question.project_id
            ):
                raise ValueError("visual observation project mismatch")
            if base_snapshot is None:
                memory = graph = knowledge = multimodal = ()
                conflicts = ()
            else:
                checked_base = EngineeringContextSnapshot.model_validate(
                    copy.deepcopy(base_snapshot.model_dump(mode="python"))
                )
                if (
                    checked_base.project_id != checked_question.project_id
                    or checked_base.task_description != checked_question.question
                ):
                    raise ValueError("base context binding mismatch")
                memory = checked_base.memory_evidence
                graph = checked_base.graph_evidence
                knowledge = checked_base.knowledge_evidence
                multimodal = checked_base.multimodal_evidence
                conflicts = checked_base.conflicts
            projected = self.project(checked_observation)
            result = fuse_projections(
                request=EngineeringContextRequest(
                    project_id=checked_question.project_id,
                    task=checked_question.question,
                    constraints=checked_question.constraints,
                ),
                memory=memory,
                graph=graph,
                knowledge=knowledge,
                multimodal=(*multimodal, *projected),
                graph_conflicts=conflicts,
            )
            if result is None:
                raise ValueError("visual context fusion returned no context")
            return EngineeringContextSnapshot.model_validate(
                copy.deepcopy(result.model_dump(mode="python"))
            )
        except MultimodalRejected:
            raise
        except Exception as error:
            raise MultimodalRejected() from error


__all__ = ("MultimodalContextProjector",)
