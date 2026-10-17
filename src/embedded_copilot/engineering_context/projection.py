from __future__ import annotations

import copy

from embedded_copilot.engineering_knowledge.models import (
    EngineeringGraphSnapshot,
    RelationType,
    validate_graph_snapshot,
)
from embedded_copilot.knowledge.source import KnowledgeEvidence
from embedded_copilot.multimodal_input.contracts import (
    MultimodalAnalysisProjection,
    validate_projection,
)

from .models import (
    ContextConflict,
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    MultimodalContextEvidence,
    canonical_fingerprint,
)
from .exceptions import EngineeringContextExternalEvidenceUnavailable
from .ports import (
    ApprovedMemoryProjectionPort,
    EngineeringEvidenceProjectionPort,
    EngineeringGraphProjectionPort,
    ExternalKnowledgeEvidenceProjectionPort,
    GraphProjection,
    KnowledgeEvidenceProjectionPort,
    MultimodalProjectionPort,
    V63KnowledgeContextProjectionPort,
    V63MemoryContextProjectionPort,
)


def _evidence_id(prefix: str, value: str) -> str:
    candidate = f"{prefix}-{value}"
    if len(candidate) <= 128:
        return candidate
    from hashlib import sha256

    return f"{prefix}-{sha256(value.encode('utf-8')).hexdigest()}"


def _validate_memory_projection(value: object) -> object:
    value_type = type(value)
    if (
        value_type.__name__ != "ApprovedMemoryProjection"
        or value_type.__module__ != "embedded_copilot.engineering_memory.projection"
    ):
        raise TypeError("approved memory projection is invalid")
    validator = getattr(value_type, "model_validate", None)
    dumper = getattr(value, "model_dump", None)
    if not callable(validator) or not callable(dumper):
        raise TypeError("approved memory projection is invalid")
    return validator(copy.deepcopy(dumper(mode="python")))


def project_memory_projection(
    values: tuple[object, ...], project_id: str
) -> tuple[EngineeringContextEvidence, ...]:
    result: list[EngineeringContextEvidence] = []
    for value in copy.deepcopy(values):
        checked = _validate_memory_projection(value)
        if checked.project_id != project_id or checked.status != "APPROVED":
            raise ValueError("approved memory projection binding is invalid")
        result.append(
            EngineeringContextEvidence.create(
                evidence_id=_evidence_id(
                    "memory", f"{checked.memory_id}-{checked.record_id}"
                ),
                project_id=project_id,
                source_type=ContextSourceType.ENGINEERING_MEMORY,
                subject=checked.title,
                summary=checked.summary,
                source_reference=checked.source_reference,
                source_fingerprint=checked.fingerprint,
                confidence=None,
                verification_status=ContextVerificationStatus.APPROVED,
            )
        )
    return tuple(sorted(result, key=lambda item: item.evidence_id))


def project_graph_snapshot(value: object | None, project_id: str) -> GraphProjection:
    if value is None:
        return GraphProjection((), ())
    if type(value) is not EngineeringGraphSnapshot:
        raise TypeError("engineering graph snapshot is invalid")
    graph = validate_graph_snapshot(value)
    if graph.project_id != project_id:
        raise ValueError("graph projection binding is invalid")
    evidence = tuple(
        EngineeringContextEvidence.create(
            evidence_id=_evidence_id("graph", node.node_id),
            project_id=project_id,
            source_type=ContextSourceType.KNOWLEDGE_GRAPH,
            subject=node.entity_name,
            summary=node.summary,
            source_reference=node.source_reference,
            source_fingerprint=node.fingerprint,
            confidence=node.confidence,
            verification_status=ContextVerificationStatus(node.verification_status),
        )
        for node in graph.nodes
    )
    evidence_by_node = {
        node.node_id: item for node, item in zip(graph.nodes, evidence, strict=True)
    }
    conflicts: list[ContextConflict] = []
    for relation in graph.relations:
        if relation.relation_type is not RelationType.CONFLICTS_WITH:
            continue
        source_a = evidence_by_node.get(relation.source_node_id)
        source_b = evidence_by_node.get(relation.target_node_id)
        if source_a is None or source_b is None:
            raise ValueError("graph conflict references unknown evidence")
        conflicts.append(
            ContextConflict.create(
                project_id=project_id,
                source_a=source_a.evidence_id,
                source_b=source_b.evidence_id,
                reason="graph declares CONFLICTS_WITH",
            )
        )
    return GraphProjection(
        tuple(sorted(evidence, key=lambda item: item.evidence_id)),
        tuple(sorted(conflicts, key=lambda item: item.conflict_id)),
    )


def project_knowledge_evidence(
    values: tuple[object, ...], project_id: str, tenant_id: str = "default-tenant"
) -> tuple[EngineeringContextEvidence, ...]:
    result: list[EngineeringContextEvidence] = []
    for value in copy.deepcopy(values):
        if type(value) is not KnowledgeEvidence:
            raise TypeError("knowledge evidence is invalid")
        checked = KnowledgeEvidence.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )
        if checked.project_id not in {None, project_id}:
            raise ValueError("knowledge evidence project binding is invalid")
        if checked.tenant_id not in {None, tenant_id}:
            raise ValueError("knowledge evidence tenant binding is invalid")
        source_name = checked.source_type.value.casefold()
        status = (
            ContextVerificationStatus.VERIFIED
            if source_name in {"datasheet", "official_doc"}
            else ContextVerificationStatus.UNVERIFIED
            if source_name in {"web", "github"}
            else ContextVerificationStatus.PROJECTED
        )
        result.append(
            EngineeringContextEvidence.create(
                evidence_id=_evidence_id("knowledge", checked.source_id),
                project_id=project_id,
                tenant_id=tenant_id,
                source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
                subject=checked.source_id,
                summary=checked.summary,
                source_reference=checked.source_reference or f"knowledge:{checked.source_id}",
                source_fingerprint=checked.source_fingerprint or canonical_fingerprint(checked),
                confidence=(
                    checked.relevance_score
                    if checked.relevance_score is not None
                    and 0.0 <= checked.relevance_score <= 1.0
                    else None
                ),
                verification_status=status,
            )
        )
    return tuple(sorted(result, key=lambda item: item.evidence_id))


def _external_context_evidence(
    values: tuple[object, ...], project_id: str
) -> tuple[EngineeringContextEvidence, ...]:
    result: list[EngineeringContextEvidence] = []
    for value in copy.deepcopy(values):
        if type(value) is not EngineeringContextEvidence:
            raise TypeError("external knowledge evidence is invalid")
        checked = EngineeringContextEvidence.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )
        if (
            checked.project_id != project_id
            or checked.source_type is not ContextSourceType.KNOWLEDGE_GATEWAY
            or not checked.source_reference.startswith("https://")
            or checked.verification_status is not ContextVerificationStatus.UNVERIFIED
        ):
            raise ValueError("external knowledge evidence binding is invalid")
        result.append(checked)
    return tuple(sorted(result, key=lambda item: item.evidence_id))


def project_multimodal_projection(
    values: tuple[object, ...], project_id: str
) -> tuple[EngineeringContextEvidence, ...]:
    result: list[EngineeringContextEvidence] = []
    for value in copy.deepcopy(values):
        if type(value) is not MultimodalAnalysisProjection:
            raise TypeError("multimodal projection is invalid")
        checked = validate_projection(value)
        observation = checked.observation
        if observation.project_id != project_id:
            raise ValueError("multimodal projection binding is invalid")
        for index, content in enumerate(observation.content):
            result.append(
                EngineeringContextEvidence.create(
                    evidence_id=_evidence_id(
                        "multimodal", f"{observation.observation_id}-{index}"
                    ),
                    project_id=project_id,
                    source_type=ContextSourceType.MULTIMODAL_OBSERVATION,
                    subject=observation.observation_id,
                    summary=content,
                    source_reference=observation.source_reference,
                    source_fingerprint=observation.fingerprint,
                    confidence=observation.confidence,
                    verification_status=ContextVerificationStatus.PROJECTED,
                )
            )
    return tuple(sorted(result, key=lambda item: item.evidence_id))


def project_vision_observation(
    value: MultimodalContextEvidence,
    *,
    project_id: str,
    source_reference: str,
    target_id: str | None,
) -> tuple[EngineeringContextEvidence, ...]:
    """Project a validated Vision observation without inferring engineering facts."""
    if type(value) is not MultimodalContextEvidence:
        raise TypeError("multimodal context evidence is invalid")
    checked = MultimodalContextEvidence.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )
    subject = target_id if target_id is not None else f"vision-{checked.observation_id}"
    result = [
        EngineeringContextEvidence.create(
            evidence_id=_evidence_id("vision", checked.observation_id),
            project_id=project_id,
            source_type=ContextSourceType.MULTIMODAL_OBSERVATION,
            subject=subject,
            summary=f"vision observation {checked.observation_id}",
            source_reference=source_reference,
            source_fingerprint=checked.observation_fingerprint,
            confidence=checked.confidence,
            verification_status=ContextVerificationStatus.PROJECTED,
        )
    ]
    categories = (
        ("object", checked.objects),
        ("component", checked.components),
        ("interface", checked.interfaces),
        ("issue", checked.issues),
        ("evidence", checked.evidence),
    )
    for category, values in categories:
        for index, item in enumerate(values):
            result.append(
                EngineeringContextEvidence.create(
                    evidence_id=_evidence_id(
                        "vision", f"{checked.observation_id}-{category}-{index}"
                    ),
                    project_id=project_id,
                    source_type=ContextSourceType.MULTIMODAL_OBSERVATION,
                    subject=subject,
                    summary=f"observed {category}: {item}",
                    fact_key=(
                        f"{target_id}.interface"
                        if category == "interface" and target_id is not None
                        else None
                    ),
                    fact_value=(
                        item
                        if category == "interface" and target_id is not None
                        else None
                    ),
                    source_reference=source_reference,
                    source_fingerprint=checked.observation_fingerprint,
                    confidence=checked.confidence,
                    verification_status=ContextVerificationStatus.PROJECTED,
                )
            )
    return tuple(sorted(result, key=lambda item: item.evidence_id))


class ReadOnlyProjectionSource:
    """Adapt approved source projections to the context source Port."""

    __slots__ = ("_engineering", "_external_knowledge", "_graph", "_knowledge", "_memory", "_multimodal", "_v63_memory", "_v63_knowledge")

    def __init__(
        self,
        *,
        memory_port: ApprovedMemoryProjectionPort | None = None,
        graph_port: EngineeringGraphProjectionPort | None = None,
        knowledge_port: KnowledgeEvidenceProjectionPort | None = None,
        external_knowledge_port: ExternalKnowledgeEvidenceProjectionPort | None = None,
        multimodal_port: MultimodalProjectionPort | None = None,
        engineering_port: EngineeringEvidenceProjectionPort | None = None,
        v63_memory_port: V63MemoryContextProjectionPort | None = None,
        v63_knowledge_port: V63KnowledgeContextProjectionPort | None = None,
    ) -> None:
        if memory_port is not None and not isinstance(
            memory_port, ApprovedMemoryProjectionPort
        ):
            raise TypeError("approved memory projection port is invalid")
        if graph_port is not None and not isinstance(
            graph_port, EngineeringGraphProjectionPort
        ):
            raise TypeError("graph projection port is invalid")
        if knowledge_port is not None and not isinstance(
            knowledge_port, KnowledgeEvidenceProjectionPort
        ):
            raise TypeError("knowledge evidence projection port is invalid")
        if external_knowledge_port is not None and not isinstance(
            external_knowledge_port, ExternalKnowledgeEvidenceProjectionPort
        ):
            raise TypeError("external knowledge evidence projection port is invalid")
        if multimodal_port is not None and not isinstance(
            multimodal_port, MultimodalProjectionPort
        ):
            raise TypeError("multimodal projection port is invalid")
        if engineering_port is not None and not isinstance(
            engineering_port, EngineeringEvidenceProjectionPort
        ):
            raise TypeError("engineering evidence projection port is invalid")
        if v63_memory_port is not None and not isinstance(v63_memory_port, V63MemoryContextProjectionPort):
            raise TypeError("v6.3 memory projection port is invalid")
        if v63_knowledge_port is not None and not isinstance(v63_knowledge_port, V63KnowledgeContextProjectionPort):
            raise TypeError("v6.3 knowledge projection port is invalid")
        self._engineering = engineering_port
        self._memory = memory_port
        self._graph = graph_port
        self._knowledge = knowledge_port
        self._external_knowledge = external_knowledge_port
        self._multimodal = multimodal_port
        self._v63_memory = v63_memory_port
        self._v63_knowledge = v63_knowledge_port

    def read_memory(self, request: EngineeringContextRequest):
        projected: tuple[EngineeringContextEvidence, ...] = ()
        if self._memory is not None:
            values = self._memory.list_approved(copy.deepcopy(request.project_id))
            if type(values) is not tuple:
                raise TypeError("approved memory projection must be a tuple")
            projected = project_memory_projection(values, request.project_id)
        if self._v63_memory is not None:
            values = self._v63_memory.list_memory_context(request.tenant_id, request.project_id)
            if type(values) is not tuple or any(type(item) is not EngineeringContextEvidence for item in values):
                raise TypeError("v6.3 memory projection must be typed")
            projected = projected + values
        return projected

    def read_graph(self, request: EngineeringContextRequest):
        if self._graph is None:
            return GraphProjection(evidence=(), conflicts=())
        graph = self._graph.project(copy.deepcopy(request.project_id))
        return project_graph_snapshot(graph, request.project_id)

    def read_knowledge(self, request: EngineeringContextRequest):
        values: tuple[object, ...] = ()
        if self._knowledge is not None:
            values = self._knowledge.list_evidence(
                request.project_id, request.task, request.constraints,
                tenant_id=request.tenant_id,
            )
            if type(values) is not tuple:
                raise TypeError("knowledge evidence projection must be a tuple")
        external_values: tuple[object, ...] = ()
        if request.external_search:
            if self._external_knowledge is None:
                raise EngineeringContextExternalEvidenceUnavailable()
            external = self._external_knowledge.list_evidence(
                request.project_id, request.task, request.constraints
            )
            if type(external) is not tuple:
                raise TypeError("external knowledge evidence projection must be a tuple")
            external_values = external
        engineering = ()
        if self._engineering is not None:
            engineering = self._engineering.list_evidence(copy.deepcopy(request))
            if type(engineering) is not tuple or any(
                type(item) is not EngineeringContextEvidence for item in engineering
            ):
                raise TypeError("engineering evidence projection must be typed")
        governed = ()
        if self._v63_knowledge is not None:
            governed = self._v63_knowledge.list_knowledge_context(request.tenant_id, request.project_id)
            if type(governed) is not tuple or any(type(item) is not EngineeringContextEvidence for item in governed):
                raise TypeError("v6.3 knowledge projection must be typed")
        return (*project_knowledge_evidence(values, request.project_id, request.tenant_id), *engineering, *governed, *_external_context_evidence(external_values, request.project_id))

    def read_multimodal(self, request: EngineeringContextRequest):
        if self._multimodal is None:
            return ()
        values = self._multimodal.list_projections(copy.deepcopy(request.project_id))
        if type(values) is not tuple:
            raise TypeError("multimodal projection must be a tuple")
        return project_multimodal_projection(values, request.project_id)


__all__ = (
    "GraphProjection",
    "ReadOnlyProjectionSource",
    "project_graph_snapshot",
    "_external_context_evidence",
    "project_knowledge_evidence",
    "project_memory_projection",
    "project_multimodal_projection",
    "project_vision_observation",
)
