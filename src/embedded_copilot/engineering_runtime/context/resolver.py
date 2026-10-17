"""Context Resolver: the only MVP read path used by agents."""

from __future__ import annotations

import copy

from embedded_copilot.engineering_runtime.context.models import (
    BindingStatus,
    ContextRequest,
    ContextResolutionError,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_runtime.objects.models import (
    BudgetLedgerObject,
    CircuitIntentObject,
    EvidenceObject,
    EvidenceQualificationStatus,
    LifecycleStatus,
    SchematicDesignObject,
)
from embedded_copilot.engineering_runtime.relations.models import RelationType


class EngineeringContextResolver:
    def __init__(self, registry, relation_manager) -> None:
        self._registry = registry
        self._relations = relation_manager

    def resolve(self, request: ContextRequest) -> EngineeringContextSnapshot:
        objects = []
        for object_id in request.required_object_ids:
            try:
                obj = self._registry.get_active_revision(object_id)
            except KeyError as exc:
                raise ContextResolutionError(f"MISSING object: {object_id}") from exc
            if obj.project_id != request.project_id:
                raise ContextResolutionError(f"CROSS_PROJECT object: {object_id}")
            objects.append(obj)

        binding_context = self._has_binding_context(objects)
        if binding_context:
            for obj in objects:
                if not isinstance(obj, SchematicDesignObject):
                    continue
                try:
                    SchematicDesignObject.model_validate(
                        copy.deepcopy(obj.model_dump(mode="python")), strict=False
                    )
                except Exception as error:
                    raise ContextResolutionError(
                        "BLOCKED binding contract is invalid"
                    ) from error
        for obj in objects:
            if obj.status != LifecycleStatus.ACTIVE and not (
                binding_context and obj.status is LifecycleStatus.STALE
            ):
                raise ContextResolutionError(
                    f"STALE or unavailable object: {obj.object_id} ({obj.status.value})"
                )

        evidence_bundle = []
        for evidence_id in request.evidence_ids:
            try:
                evidence = self._registry.get_active_revision(evidence_id)
            except KeyError as exc:
                raise ContextResolutionError(
                    f"MISSING evidence: {evidence_id}"
                ) from exc
            if not isinstance(evidence, EvidenceObject):
                raise ContextResolutionError(f"NOT_EVIDENCE object: {evidence_id}")
            if (
                evidence.project_id != request.project_id
                or evidence.status != LifecycleStatus.ACTIVE
            ):
                raise ContextResolutionError(
                    f"STALE or cross-project evidence: {evidence_id}"
                )
            if evidence.qualification_status is not EvidenceQualificationStatus.QUALIFIED:
                raise ContextResolutionError(
                    f"UNQUALIFIED evidence: {evidence_id}"
                )
            evidence_bundle.append(evidence)

        object_ids = {obj.object_id for obj in objects}
        constraints: list[str] = list(request.constraints)
        for obj in objects:
            if hasattr(obj, "constraints"):
                constraints.extend(obj.constraints)
            if isinstance(obj, BudgetLedgerObject):
                constraints.extend(obj.entries)
        constraints = list(dict.fromkeys(constraints))
        relations = self._relations.relations_for_object_ids(object_ids)
        return EngineeringContextSnapshot.create(
            project_id=request.project_id,
            target_object_type=request.target_object_type,
            objects=tuple(objects),
            relations=relations,
            evidence_bundle=tuple(evidence_bundle),
            constraints=tuple(constraints),
            binding_status=self._binding_status(objects, request.project_id),
        )

    def resolve_context(self, request: ContextRequest) -> EngineeringContextSnapshot:
        """Stable query name exposed to Aevum/Supervisor integration ports."""
        return self.resolve(request)

    @staticmethod
    def _has_binding_context(objects: list[object]) -> bool:
        return any(isinstance(obj, CircuitIntentObject) for obj in objects) and any(
            isinstance(obj, SchematicDesignObject) for obj in objects
        )

    def _binding_status(
        self, objects: list[object], project_id: str
    ) -> BindingStatus | None:
        intent = next(
            (obj for obj in objects if isinstance(obj, CircuitIntentObject)), None
        )
        schematic = next(
            (obj for obj in objects if isinstance(obj, SchematicDesignObject)), None
        )
        if intent is None and schematic is None:
            return None
        if intent is None or schematic is None:
            return BindingStatus.BLOCKED
        if intent.project_id != project_id or schematic.project_id != project_id:
            return BindingStatus.BLOCKED
        if not schematic.circuit_bindings:
            return BindingStatus.BLOCKED
        relations = self._relations.list_relations(
            source_id=intent.object_id,
            target_id=schematic.object_id,
            relation_type=RelationType.EXPRESSED_BY,
        )
        if len(relations) != 1:
            return BindingStatus.BLOCKED
        relation = relations[0]
        if (
            relation.source.project_id != project_id
            or relation.target.project_id != project_id
        ):
            return BindingStatus.BLOCKED
        if (
            relation.source.revision != intent.revision
            or relation.source.fingerprint != intent.fingerprint
            or relation.target.revision != schematic.revision
            or relation.target.fingerprint != schematic.fingerprint
        ):
            return BindingStatus.STALE
        if (
            intent.status is not LifecycleStatus.ACTIVE
            or schematic.status is not LifecycleStatus.ACTIVE
        ):
            return BindingStatus.STALE
        try:
            self._relations.validate_expressed_by(intent, schematic)
        except ValueError:
            return BindingStatus.BLOCKED
        return BindingStatus.CURRENT
