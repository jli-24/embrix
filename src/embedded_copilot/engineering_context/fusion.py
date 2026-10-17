from __future__ import annotations

import copy
from collections import defaultdict
from typing import NamedTuple

from .exceptions import EngineeringContextRejected
from .models import (
    ContextConflict,
    ContextSourceReference,
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
    MultimodalContextFusionRequest,
)
from .projection import project_vision_observation
from .ranking import rank_evidence


class FusionResult(NamedTuple):
    snapshot: EngineeringContextSnapshot | None


def _checked_evidence(
    values: tuple[EngineeringContextEvidence, ...],
    request: EngineeringContextRequest,
    allowed_sources: frozenset[ContextSourceType],
) -> tuple[EngineeringContextEvidence, ...]:
    if type(values) is not tuple:
        raise TypeError("context evidence must be a tuple")
    checked: list[EngineeringContextEvidence] = []
    for value in copy.deepcopy(values):
        if type(value) is not EngineeringContextEvidence:
            raise TypeError("context evidence is invalid")
        item = EngineeringContextEvidence.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )
        if item.project_id != request.project_id or item.tenant_id != request.tenant_id:
            raise ValueError("context evidence identity binding is invalid")
        if item.source_type not in allowed_sources:
            raise ValueError("context evidence source binding is invalid")
        checked.append(item)
    return tuple(checked)


def _merge_evidence(
    values: tuple[EngineeringContextEvidence, ...],
) -> tuple[EngineeringContextEvidence, ...]:
    by_id: dict[str, EngineeringContextEvidence] = {}
    for item in values:
        previous = by_id.get(item.evidence_id)
        if previous is None:
            by_id[item.evidence_id] = item
            continue
        if previous.fingerprint != item.fingerprint:
            raise ValueError("duplicate context evidence has conflicting fingerprints")
    return tuple(by_id[key] for key in sorted(by_id))


def _explicit_conflicts(
    values: tuple[EngineeringContextEvidence, ...],
) -> tuple[ContextConflict, ...]:
    grouped: defaultdict[tuple[str, str], list[EngineeringContextEvidence]] = defaultdict(list)
    for item in values:
        if item.fact_key is not None and item.fact_value is not None:
            grouped[(item.project_id, item.fact_key.casefold())].append(item)
    conflicts: dict[str, ContextConflict] = {}
    for group in grouped.values():
        for index, left in enumerate(sorted(group, key=lambda item: item.evidence_id)):
            for right in sorted(group, key=lambda item: item.evidence_id)[index + 1 :]:
                if left.fact_value.casefold() == right.fact_value.casefold():
                    continue
                conflict = ContextConflict.create(
                    project_id=left.project_id,
                    source_a=left.evidence_id,
                    source_b=right.evidence_id,
                    reason="explicit fact key has conflicting values",
                )
                conflicts[conflict.conflict_id] = conflict
    return tuple(conflicts[key] for key in sorted(conflicts))


def _status(
    values: tuple[EngineeringContextEvidence, ...],
    conflicts: tuple[ContextConflict, ...],
) -> ContextVerificationStatus:
    if conflicts:
        return ContextVerificationStatus.REQUIRES_REVIEW
    statuses = {value.verification_status for value in values}
    if ContextVerificationStatus.UNVERIFIED in statuses:
        return ContextVerificationStatus.UNVERIFIED
    if ContextVerificationStatus.PROJECTED in statuses:
        return ContextVerificationStatus.PROJECTED
    if (
        ContextVerificationStatus.APPROVED in statuses
        and ContextVerificationStatus.VERIFIED in statuses
    ):
        return ContextVerificationStatus.VERIFIED
    if statuses == {ContextVerificationStatus.APPROVED}:
        return ContextVerificationStatus.APPROVED
    return ContextVerificationStatus.VERIFIED


def _provenance(
    values: tuple[EngineeringContextEvidence, ...],
) -> tuple[ContextSourceReference, ...]:
    sources = {item.evidence_id: item.source() for item in values}
    return tuple(sources[key] for key in sorted(sources))


def fuse_projections(
    *,
    request: EngineeringContextRequest,
    memory: tuple[EngineeringContextEvidence, ...],
    graph: tuple[EngineeringContextEvidence, ...],
    knowledge: tuple[EngineeringContextEvidence, ...],
    multimodal: tuple[EngineeringContextEvidence, ...],
    graph_conflicts: tuple[ContextConflict, ...] = (),
) -> EngineeringContextSnapshot | None:
    groups = (
        _checked_evidence(
            memory, request, frozenset({ContextSourceType.ENGINEERING_MEMORY})
        ),
        _checked_evidence(
            graph, request, frozenset({ContextSourceType.KNOWLEDGE_GRAPH})
        ),
        _checked_evidence(
            knowledge,
            request,
            frozenset(
                {
                    ContextSourceType.KNOWLEDGE_GATEWAY,
                    ContextSourceType.KNOWLEDGE_EVOLUTION,
                    ContextSourceType.DATASHEET_METADATA,
                    ContextSourceType.FIRMWARE_SOURCE,
                    ContextSourceType.KNOWLEDGE,
                    ContextSourceType.DEBUG,
                    ContextSourceType.HARDWARE,
                    ContextSourceType.PCB,
                    ContextSourceType.REQUIREMENT,
                    ContextSourceType.ARCHITECTURE,
                    ContextSourceType.VISION_OBSERVATION,
                }
            ),
        ),
        _checked_evidence(
            multimodal,
            request,
            frozenset({ContextSourceType.MULTIMODAL_OBSERVATION}),
        ),
    )
    merged = _merge_evidence(tuple(item for group in groups for item in group))
    if not merged:
        return None

    conflicts = list(_explicit_conflicts(merged))
    if type(graph_conflicts) is not tuple:
        raise TypeError("graph conflicts must be a tuple")
    for value in copy.deepcopy(graph_conflicts):
        if type(value) is not ContextConflict:
            raise TypeError("context conflict is invalid")
        conflict = ContextConflict.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )
        if conflict.project_id != request.project_id:
            raise ValueError("context conflict project binding is invalid")
        evidence_ids = {item.evidence_id for item in merged}
        if conflict.source_a not in evidence_ids or conflict.source_b not in evidence_ids:
            raise ValueError("context conflict references unknown evidence")
        conflicts.append(conflict)
    conflict_by_id = {value.conflict_id: value for value in conflicts}
    checked_conflicts = tuple(conflict_by_id[key] for key in sorted(conflict_by_id))

    ranked = rank_evidence(merged, request.task, request.constraints)
    by_source = {
        ContextSourceType.ENGINEERING_MEMORY: tuple(
            item for item in ranked if item.source_type is ContextSourceType.ENGINEERING_MEMORY
        ),
        ContextSourceType.KNOWLEDGE_GRAPH: tuple(
            item for item in ranked if item.source_type is ContextSourceType.KNOWLEDGE_GRAPH
        ),
        ContextSourceType.KNOWLEDGE_GATEWAY: tuple(
            item
            for item in ranked
            if item.source_type
            in {
                ContextSourceType.KNOWLEDGE_GATEWAY,
                ContextSourceType.KNOWLEDGE_EVOLUTION,
                ContextSourceType.DATASHEET_METADATA,
                ContextSourceType.FIRMWARE_SOURCE,
                ContextSourceType.KNOWLEDGE,
                ContextSourceType.DEBUG,
                ContextSourceType.HARDWARE,
                ContextSourceType.PCB,
                ContextSourceType.REQUIREMENT,
                ContextSourceType.ARCHITECTURE,
                ContextSourceType.VISION_OBSERVATION,
            }
        ),
        ContextSourceType.MULTIMODAL_OBSERVATION: tuple(
            item
            for item in ranked
            if item.source_type is ContextSourceType.MULTIMODAL_OBSERVATION
        ),
    }
    known_confidences = tuple(
        item.confidence for item in merged if item.confidence is not None
    )
    confidence = min(known_confidences) if known_confidences else 0.0
    return EngineeringContextSnapshot.create(
        project_id=request.project_id,
        tenant_id=request.tenant_id,
        task_description=request.task,
        memory_evidence=by_source[ContextSourceType.ENGINEERING_MEMORY],
        graph_evidence=by_source[ContextSourceType.KNOWLEDGE_GRAPH],
        knowledge_evidence=by_source[ContextSourceType.KNOWLEDGE_GATEWAY],
        multimodal_evidence=by_source[ContextSourceType.MULTIMODAL_OBSERVATION],
        conflicts=checked_conflicts,
        confidence=confidence,
        provenance=_provenance(merged),
        verification_status=_status(merged, checked_conflicts),
    )


class EngineeringContextFusionService:
    def fuse(self, **kwargs: object) -> EngineeringContextSnapshot | None:
        return fuse_projections(**kwargs)


class ContextFusionService(EngineeringContextFusionService):
    pass


class MultimodalContextFusionService:
    """Read-only Vision observation projection into an existing Context snapshot."""

    def fuse(
        self, request: MultimodalContextFusionRequest
    ) -> EngineeringContextSnapshot | None:
        try:
            if type(request) is not MultimodalContextFusionRequest:
                raise TypeError("multimodal fusion request is invalid")
            checked = MultimodalContextFusionRequest.model_validate(
                copy.deepcopy(request.model_dump(mode="python"))
            )
            vision = project_vision_observation(
                checked.evidence,
                project_id=checked.project_id,
                source_reference=checked.source_reference,
                target_id=checked.target_id,
            )
            snapshot = checked.snapshot
            return fuse_projections(
                request=EngineeringContextRequest(
                    project_id=checked.project_id,
                    task=snapshot.task_description,
                ),
                memory=snapshot.memory_evidence,
                graph=snapshot.graph_evidence,
                knowledge=snapshot.knowledge_evidence,
                multimodal=(*snapshot.multimodal_evidence, *vision),
                graph_conflicts=snapshot.conflicts,
            )
        except EngineeringContextRejected:
            raise
        except (TypeError, ValueError) as error:
            raise EngineeringContextRejected() from error


__all__ = (
    "ContextFusionService",
    "EngineeringContextFusionService",
    "FusionResult",
    "MultimodalContextFusionService",
    "fuse_projections",
)
