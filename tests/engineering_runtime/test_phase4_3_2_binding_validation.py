from __future__ import annotations

import pytest

from embedded_copilot.engineering_runtime import (
    BindingStatus,
    CircuitSchematicBinding,
    ContextRequest,
    ContextResolutionError,
    EngineeringObjectRuntime,
    LifecycleActor,
    LifecycleStatus,
    ObjectRef,
    Relation,
    RelationType,
    SchematicDesignObject,
)
from tests.application.test_phase4_3_1_binding import (
    _intent,
    _plan,
    _schematic,
    _service,
)


def _activate(runtime: EngineeringObjectRuntime, obj):
    draft = obj.model_copy(update={"status": LifecycleStatus.DRAFT})
    runtime.register_object(draft)
    for target, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        draft = runtime.transition_object(draft.object_id, draft.revision, target, actor)
    return draft


def _golden_projection():
    intent = _intent()
    schematic = _service().materialize_circuit_bindings(
        intent, _plan(intent), _schematic()
    )
    return intent, schematic


def _runtime_with_binding(*, binding=None):
    intent, schematic = _golden_projection()
    if binding is not None:
        schematic = SchematicDesignObject.create(
            object_id=schematic.object_id,
            project_id=schematic.project_id,
            revision=schematic.revision,
            status=schematic.status,
            symbols=schematic.symbols,
            pins=schematic.pins,
            nets=schematic.nets,
            circuit_bindings=(binding,),
            erc_result_evidence_id=schematic.erc_result_evidence_id,
            artifact_ref=schematic.artifact_ref,
            artifact_fingerprint=schematic.artifact_fingerprint,
            dependency_fingerprint=schematic.dependency_fingerprint,
            provenance=schematic.provenance,
        )
    runtime = EngineeringObjectRuntime(project_id=intent.project_id)
    active_intent = _activate(runtime, intent)
    active_schematic = _activate(runtime, schematic)
    return runtime, active_intent, active_schematic


def _context(runtime, intent, schematic):
    return runtime.resolve_context(
        ContextRequest.create(
            project_id=intent.project_id,
            target_object_type="CIRCUIT_INTENT",
            required_object_ids=(intent.object_id, schematic.object_id),
        )
    )


def _binding_with(binding: CircuitSchematicBinding, **changes: object):
    values = binding.model_dump(mode="python")
    values.update(changes)
    return CircuitSchematicBinding.create(**values)


def test_expressed_by_validates_stm32_power_can_and_swd_bindings() -> None:
    runtime, intent, schematic = _runtime_with_binding()

    relation = runtime.add_relation(intent, RelationType.EXPRESSED_BY, schematic)

    assert relation.relation_type is RelationType.EXPRESSED_BY
    assert {item.circuit_block_id for item in schematic.circuit_bindings} == {
        "block-power",
        "block-can",
        "block-swd",
    }


@pytest.mark.parametrize(
    ("block_id", "change"),
    [
        ("block-power", {"net_ids": ("GND",)}),
        ("block-can", {"component_instance_ids": ("mcu", "can-connector")}),
    ],
)
def test_expressed_by_rejects_missing_required_binding_element(
    block_id: str, change
) -> None:
    _, schematic = _golden_projection()
    target_binding = next(
        item for item in schematic.circuit_bindings if item.circuit_block_id == block_id
    )
    runtime, active_intent, active_schematic = _runtime_with_binding(
        binding=_binding_with(target_binding, **change)
    )

    with pytest.raises(ValueError, match="EXPRESSED_BY"):
        runtime.add_relation(active_intent, RelationType.EXPRESSED_BY, active_schematic)


def test_expressed_by_rejects_intent_fingerprint_mismatch() -> None:
    _, schematic = _golden_projection()
    binding = _binding_with(
        schematic.circuit_bindings[0],
        circuit_intent_fingerprint="sha256:" + "b" * 64,
    )
    runtime, active_intent, active_schematic = _runtime_with_binding(binding=binding)

    with pytest.raises(ValueError, match="identity mismatch"):
        runtime.add_relation(active_intent, RelationType.EXPRESSED_BY, active_schematic)


def test_expressed_by_rejects_cross_project_endpoint() -> None:
    runtime, intent, schematic = _runtime_with_binding()
    other = schematic.model_copy(update={"project_id": "project-other"})

    with pytest.raises(ValueError, match="same project"):
        runtime.relations.add_relation(intent, RelationType.EXPRESSED_BY, other)


def test_context_reports_current_binding_status() -> None:
    runtime, intent, schematic = _runtime_with_binding()
    runtime.add_relation(intent, RelationType.EXPRESSED_BY, schematic)

    assert _context(runtime, intent, schematic).binding_status is BindingStatus.CURRENT


def test_context_reports_stale_after_intent_revision_changes() -> None:
    runtime, intent, schematic = _runtime_with_binding()
    runtime.add_relation(intent, RelationType.EXPRESSED_BY, schematic)
    revised = runtime.create_revision(
        intent.object_id,
        {"function": "STM32 Power CAN SWD design intent revision 2"},
        provenance=intent.provenance,
    )
    for target, actor in (
        (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
        (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
        (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
    ):
        revised = runtime.transition_object(revised.object_id, revised.revision, target, actor)

    assert _context(runtime, revised, schematic).binding_status is BindingStatus.STALE


def test_context_reports_blocked_for_tampered_binding() -> None:
    runtime, intent, schematic = _runtime_with_binding()
    runtime.add_relation(intent, RelationType.EXPRESSED_BY, schematic)
    binding = schematic.circuit_bindings[0]
    tampered_binding = _binding_with(
        binding, component_instance_ids=("missing-component",)
    )
    tampered_schematic = schematic.model_copy(
        update={"circuit_bindings": (tampered_binding, *schematic.circuit_bindings[1:])}
    )
    runtime.registry.replace(tampered_schematic)

    with pytest.raises(ContextResolutionError, match="BLOCKED"):
        _context(runtime, intent, tampered_schematic)


def test_context_reports_blocked_for_missing_binding_element() -> None:
    runtime, intent, schematic = _runtime_with_binding()
    relation = runtime.add_relation(intent, RelationType.EXPRESSED_BY, schematic)
    binding = next(
        item for item in schematic.circuit_bindings if item.circuit_block_id == "block-power"
    )
    blocked_binding = _binding_with(binding, net_ids=("GND",))
    blocked_schematic = SchematicDesignObject.create(
        object_id=schematic.object_id,
        project_id=schematic.project_id,
        revision=schematic.revision,
        status=schematic.status,
        symbols=schematic.symbols,
        pins=schematic.pins,
        nets=schematic.nets,
        circuit_bindings=tuple(
            blocked_binding
            if item.circuit_block_id == blocked_binding.circuit_block_id
            else item
            for item in schematic.circuit_bindings
        ),
        erc_result_evidence_id=schematic.erc_result_evidence_id,
        artifact_ref=schematic.artifact_ref,
        artifact_fingerprint=schematic.artifact_fingerprint,
        dependency_fingerprint=schematic.dependency_fingerprint,
        provenance=schematic.provenance,
    )
    runtime.registry.replace(blocked_schematic)
    rebound = Relation.create(
        relation_id="rel-rebound-binding",
        project_id=relation.project_id,
        source=relation.source,
        relation_type=relation.relation_type,
        target=ObjectRef.from_object(blocked_schematic),
        evidence_refs=relation.evidence_refs,
    )
    del runtime.relations._relations[relation.relation_id]
    runtime.relations._relations[rebound.relation_id] = rebound

    assert _context(runtime, intent, blocked_schematic).binding_status is BindingStatus.BLOCKED


def test_context_reports_blocked_when_binding_is_missing() -> None:
    intent, schematic = _golden_projection()
    empty_schematic = SchematicDesignObject.create(
        **{
            **schematic.model_dump(mode="python"),
            "circuit_bindings": (),
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    runtime = EngineeringObjectRuntime(project_id=intent.project_id)
    active_intent = _activate(runtime, intent)
    active_schematic = _activate(runtime, empty_schematic)

    assert _context(runtime, active_intent, active_schematic).binding_status is BindingStatus.BLOCKED
