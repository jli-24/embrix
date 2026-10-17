from __future__ import annotations

import copy
from datetime import datetime

from pydantic import ValidationError

from embedded_copilot.engineering_context import ContextConflict
from embedded_copilot.engineering_knowledge import (
    EngineeringGraphSnapshot,
    EngineeringRelation,
    RelationType,
)

from .contracts import ConflictResolution, ConflictReview
from .exceptions import GovernanceConflictError, GovernanceRejected
from .ports import ReviewStorePort


class ConflictService:
    __slots__ = ("_store",)

    def __init__(self, *, store: ReviewStorePort) -> None:
        if not isinstance(store, ReviewStorePort):
            raise TypeError("review store port is invalid")
        self._store = store

    def resolve_context_conflict(
        self,
        conflict: ContextConflict,
        *,
        resolution: ConflictResolution,
        reviewer_identity: str,
        resolved_at: datetime,
    ) -> ConflictReview:
        if type(conflict) is not ContextConflict:
            raise GovernanceRejected()
        try:
            checked = ContextConflict.model_validate(copy.deepcopy(conflict.model_dump(mode="python")))
            review = ConflictReview.create(
                conflict_id=checked.conflict_id, project_id=checked.project_id,
                source_a=checked.source_a, source_b=checked.source_b, reason=checked.reason,
                resolution=resolution, reviewer_identity=reviewer_identity, resolved_at=resolved_at,
                input_fingerprint=checked.fingerprint,
            )
            return self._store.record_conflict(review)
        except GovernanceConflictError:
            raise
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error

    def resolve_graph_conflict(
        self,
        snapshot: EngineeringGraphSnapshot,
        relation: EngineeringRelation,
        *,
        resolution: ConflictResolution,
        reviewer_identity: str,
        resolved_at: datetime,
    ) -> ConflictReview:
        if type(snapshot) is not EngineeringGraphSnapshot or type(relation) is not EngineeringRelation:
            raise GovernanceRejected()
        try:
            checked_snapshot = EngineeringGraphSnapshot.model_validate(copy.deepcopy(snapshot.model_dump(mode="python")))
            checked_relation = EngineeringRelation.model_validate(copy.deepcopy(relation.model_dump(mode="python")))
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error
        matching = next((item for item in checked_snapshot.relations if item.relation_id == checked_relation.relation_id), None)
        if (
            checked_relation.relation_type is not RelationType.CONFLICTS_WITH
            or matching is None
            or matching.fingerprint != checked_relation.fingerprint
        ):
            raise GovernanceRejected()
        review = ConflictReview.create(
            conflict_id=checked_relation.relation_id, project_id=checked_snapshot.project_id,
            source_a=checked_relation.source_node_id, source_b=checked_relation.target_node_id,
            reason="graph declares CONFLICTS_WITH", resolution=resolution,
            reviewer_identity=reviewer_identity, resolved_at=resolved_at,
            input_fingerprint=checked_relation.fingerprint,
        )
        try:
            return self._store.record_conflict(review)
        except GovernanceConflictError:
            raise
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error


__all__ = ("ConflictService",)
