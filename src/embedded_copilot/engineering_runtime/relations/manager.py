"""Relation registry and dependency traversal."""

from __future__ import annotations

import copy
import hashlib

from embedded_copilot.engineering_runtime.objects.models import (
    CircuitIntentObject,
    EngineeringObject,
    EvidenceObject,
    EvidenceQualificationStatus,
    SchematicDesignObject,
)
from embedded_copilot.engineering_runtime.relations.models import (
    ObjectRef,
    Relation,
    RelationType,
)


class RelationManager:
    def __init__(self, registry) -> None:
        self._registry = registry
        self._relations: dict[str, Relation] = {}

    def add_relation(
        self,
        source: EngineeringObject | ObjectRef,
        relation_type: RelationType,
        target: EngineeringObject | ObjectRef,
        evidence_refs: tuple[str, ...] = (),
    ) -> Relation:
        source_ref = (
            source if isinstance(source, ObjectRef) else ObjectRef.from_object(source)
        )
        target_ref = (
            target if isinstance(target, ObjectRef) else ObjectRef.from_object(target)
        )
        if source_ref.project_id != target_ref.project_id:
            raise ValueError("relation endpoints must belong to the same project")
        source_current = self._registry.get_object(
            source_ref.object_id, source_ref.revision
        )
        target_current = self._registry.get_object(
            target_ref.object_id, target_ref.revision
        )
        if (
            source_current.fingerprint != source_ref.fingerprint
            or target_current.fingerprint != target_ref.fingerprint
        ):
            raise ValueError("relation endpoint fingerprint is stale")
        if relation_type is RelationType.VALIDATED_BY:
            if not isinstance(source_current, SchematicDesignObject) or not isinstance(
                target_current, EvidenceObject
            ):
                raise ValueError("VALIDATED_BY requires schematic design and evidence")
            if target_current.qualification_status is not EvidenceQualificationStatus.QUALIFIED:
                raise ValueError("VALIDATED_BY requires qualified evidence")
            if (
                target_current.subject_object_id != source_current.object_id
                or target_current.subject_revision != source_current.revision
                or target_current.subject_fingerprint != source_current.fingerprint
            ):
                raise ValueError("validated evidence subject binding mismatch")
            if source_current.artifact_ref is not None and (
                target_current.subject_artifact_ref != source_current.artifact_ref
                or target_current.subject_artifact_fingerprint
                != source_current.artifact_fingerprint
            ):
                raise ValueError("validated evidence artifact binding mismatch")
        if relation_type is RelationType.EXPRESSED_BY:
            self.validate_expressed_by(source_current, target_current)
        relation_key = "|".join(
            (
                source_ref.model_dump_json(),
                relation_type.value,
                target_ref.model_dump_json(),
                ",".join(evidence_refs),
            )
        )
        relation_id = (
            "rel-" + hashlib.sha256(relation_key.encode("utf-8")).hexdigest()[:24]
        )
        relation = Relation.create(
            relation_id=relation_id,
            project_id=source_ref.project_id,
            source=source_ref,
            relation_type=relation_type,
            target=target_ref,
            evidence_refs=evidence_refs,
        )
        existing = self._relations.get(relation_id)
        if existing is not None and existing.fingerprint != relation.fingerprint:
            raise ValueError("relation identity collision")
        self._relations[relation_id] = relation
        return relation

    def validate_expressed_by(
        self, source: EngineeringObject, target: EngineeringObject
    ) -> None:
        """Validate the existing CircuitIntent-to-schematic binding contract."""

        if not isinstance(source, CircuitIntentObject) or not isinstance(
            target, SchematicDesignObject
        ):
            raise TypeError(
                "EXPRESSED_BY requires CircuitIntent source and SchematicDesign target"
            )
        if source.project_id != target.project_id:
            raise ValueError("EXPRESSED_BY endpoints must belong to the same project")
        if not target.circuit_bindings:
            return
        try:
            checked_target = SchematicDesignObject.model_validate(
                copy.deepcopy(target.model_dump(mode="python")), strict=False
            )
        except Exception as error:
            raise ValueError("EXPRESSED_BY binding contract is invalid") from error

        blocks_by_id = {block.block_id: block for block in source.blocks}
        if len(blocks_by_id) != len(source.blocks):
            raise ValueError("EXPRESSED_BY source contains duplicate CircuitBlock IDs")
        for binding in checked_target.circuit_bindings:
            if (
                binding.circuit_intent_id != source.object_id
                or binding.circuit_intent_revision != source.revision
                or binding.circuit_intent_fingerprint != source.fingerprint
            ):
                raise ValueError("EXPRESSED_BY CircuitIntent identity mismatch")
            block = blocks_by_id.get(binding.circuit_block_id)
            if block is None:
                raise ValueError("EXPRESSED_BY CircuitBlock is missing")
            role = self._binding_role(block)
            self._validate_binding_refs(checked_target, binding)
            self._validate_binding_requirements(block, binding, role)

    @staticmethod
    def _binding_role(block) -> str:
        roles = []
        for value in block.schematic_binding_refs:
            namespace, separator, payload = value.partition(":")
            if separator and namespace.casefold() == "role":
                roles.append(payload.strip().upper())
        if len(roles) != 1 or roles[0] not in {"POWER", "CAN", "SWD"}:
            raise ValueError("EXPRESSED_BY binding role is missing or ambiguous")
        return roles[0]

    @staticmethod
    def _validate_binding_refs(schematic: SchematicDesignObject, binding) -> None:
        symbol_components = {
            value.split(":", 1)[0] for value in schematic.symbols if ":" in value
        }
        if not set(binding.component_instance_ids) <= symbol_components:
            raise ValueError("EXPRESSED_BY component ref is missing from schematic")
        if not set(binding.pin_binding_ids) <= set(schematic.pins):
            raise ValueError("EXPRESSED_BY pin ref is missing from schematic")
        if not set(binding.net_ids) <= set(schematic.nets):
            raise ValueError("EXPRESSED_BY net ref is missing from schematic")
        projection_nets = set(schematic.nets)
        for domain_id in binding.power_domain_ids:
            normalized_domain = domain_id.removeprefix("power-").upper()
            if domain_id not in projection_nets and normalized_domain not in projection_nets:
                raise ValueError("EXPRESSED_BY power-domain ref is missing from schematic")

    @staticmethod
    def _validate_binding_requirements(binding_block, binding, role: str) -> None:
        if not binding.component_instance_ids or not binding.pin_binding_ids:
            raise ValueError("EXPRESSED_BY binding requires component and pin refs")
        if not binding.net_ids:
            raise ValueError("EXPRESSED_BY binding requires net refs")
        required_nets = {
            "POWER": {"3V3", "GND"},
            "CAN": {"CAN_TX", "CAN_RX", "CANH", "CANL"},
            "SWD": {"SWDIO", "SWCLK", "GND"},
        }[role]
        if not required_nets <= set(binding.net_ids):
            raise ValueError("EXPRESSED_BY binding is missing required nets")
        if role == "POWER" and not binding.power_domain_ids:
            raise ValueError("EXPRESSED_BY POWER binding requires power-domain refs")

        component_roles = set()
        for value in binding_block.schematic_binding_refs:
            namespace, separator, payload = value.partition(":")
            if namespace.casefold() != "component" or not separator:
                continue
            component_role, role_separator, component_id = payload.partition(":")
            if role_separator and component_id in binding.component_instance_ids:
                component_roles.add(component_role.strip().upper())
        required_roles = {
            "CAN": {"MCU", "CAN_TRANSCEIVER", "CONNECTOR"},
            "SWD": {"MCU", "SWD_HEADER"},
        }.get(role, set())
        if not required_roles <= component_roles:
            raise ValueError("EXPRESSED_BY binding is missing required component roles")

    def list_relations(
        self,
        *,
        source_id: str | None = None,
        target_id: str | None = None,
        relation_type: RelationType | None = None,
    ) -> tuple[Relation, ...]:
        result = [
            relation
            for relation in self._relations.values()
            if (source_id is None or relation.source.object_id == source_id)
            and (target_id is None or relation.target.object_id == target_id)
            and (relation_type is None or relation.relation_type == relation_type)
        ]
        return tuple(sorted(result, key=lambda item: item.relation_id))

    def relations_for_object_ids(self, object_ids: set[str]) -> tuple[Relation, ...]:
        return tuple(
            relation
            for relation in self.list_relations()
            if relation.source.object_id in object_ids
            and relation.target.object_id in object_ids
        )

    def downstream_object_ids(self, source_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    relation.target.object_id
                    for relation in self._relations.values()
                    if relation.source.object_id == source_id
                }
            )
        )
