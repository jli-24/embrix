from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextQuery,
    EngineeringContextRequest,
    EngineeringContextService,
    GraphProjection,
)


class Source:
    def __init__(self, values: dict[str, tuple[EngineeringContextEvidence, ...]]):
        self.values = values
        self.calls = 0

    def read_memory(self, request):
        self.calls += 1
        return tuple(value.model_copy(deep=True) for value in self.values.get("memory", ()))

    def read_graph(self, request):
        return GraphProjection(
            tuple(value.model_copy(deep=True) for value in self.values.get("graph", ())),
            (),
        )

    def read_knowledge(self, request):
        return tuple(value.model_copy(deep=True) for value in self.values.get("knowledge", ()))

    def read_multimodal(self, request):
        return tuple(value.model_copy(deep=True) for value in self.values.get("multimodal", ()))

def evidence(source_type: ContextSourceType, evidence_id: str, *, key=None, value=None):
    return EngineeringContextEvidence.create(
        evidence_id=evidence_id,
        project_id="project-1",
        source_type=source_type,
        subject="GPIO12",
        summary=f"{evidence_id} summary",
        fact_key=key,
        fact_value=value,
        source_reference=f"{source_type.value.lower()}:{evidence_id}",
        source_fingerprint="sha256:" + (evidence_id[-1] * 64),
        confidence=0.8,
        verification_status=(
            "APPROVED"
            if source_type is ContextSourceType.ENGINEERING_MEMORY
            else "VERIFIED"
            if source_type is ContextSourceType.KNOWLEDGE_GRAPH
            else "PROJECTED"
        ),
    )


def test_fusion_combines_all_four_sources_and_is_read_only() -> None:
    source = Source(
        {
            "memory": (evidence(ContextSourceType.ENGINEERING_MEMORY, "memory-1"),),
            "graph": (evidence(ContextSourceType.KNOWLEDGE_GRAPH, "graph-1"),),
            "knowledge": (evidence(ContextSourceType.KNOWLEDGE_GATEWAY, "knowledge-1"),),
            "multimodal": (evidence(ContextSourceType.MULTIMODAL_OBSERVATION, "image-1"),),
        }
    )
    service = EngineeringContextService(source_port=source)
    request = EngineeringContextRequest(
        project_id="project-1", task="GPIO12", constraints=("SPI",)
    )
    snapshot = service.get_context(request)
    assert snapshot is not None
    assert len(snapshot.memory_evidence) == 1
    assert len(snapshot.graph_evidence) == 1
    assert len(snapshot.knowledge_evidence) == 1
    assert len(snapshot.multimodal_evidence) == 1
    assert snapshot.provenance
    assert source.calls == 1


def test_repeated_fusion_has_stable_identity_and_fingerprint() -> None:
    source = Source({"memory": (evidence(ContextSourceType.ENGINEERING_MEMORY, "memory-1"),)})
    service = EngineeringContextService(source_port=source)
    request = EngineeringContextRequest(project_id="project-1", task="GPIO12")
    snapshots = [service.get_context(request) for _ in range(20)]
    assert all(snapshot is not None for snapshot in snapshots)
    assert len({snapshot.context_id for snapshot in snapshots}) == 1
    assert len({snapshot.fingerprint for snapshot in snapshots}) == 1


def test_empty_sources_return_unavailable_none() -> None:
    service = EngineeringContextService(source_port=Source({}))
    assert service.get_context(
        EngineeringContextRequest(project_id="project-1", task="camera")
    ) is None


def test_firmware_source_uses_existing_knowledge_evidence_transport() -> None:
    firmware = EngineeringContextEvidence.create(
        evidence_id="firmware-i2c-init",
        project_id="project-1",
        source_type=ContextSourceType.FIRMWARE_SOURCE,
        subject="MX_I2C1_Init",
        summary="observed function MX_I2C1_Init at line 42",
        fact_key="line",
        fact_value="42",
        source_reference="Core/Src/i2c.c",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=1.0,
        verification_status="VERIFIED",
    )
    snapshot = EngineeringContextService(
        source_port=Source({"knowledge": (firmware,)})
    ).get_context(EngineeringContextRequest(project_id="project-1", task="I2C"))
    assert snapshot is not None
    assert snapshot.knowledge_evidence == (firmware,)
    assert snapshot.provenance[0].source_type is ContextSourceType.FIRMWARE_SOURCE


def test_project_mismatch_fails_closed() -> None:
    value = evidence(ContextSourceType.ENGINEERING_MEMORY, "memory-1").model_dump()
    value["project_id"] = "project-2"
    source = Source({"memory": (EngineeringContextEvidence.model_construct(**value),)})
    service = EngineeringContextService(source_port=source)
    from embedded_copilot.engineering_context import EngineeringContextRejected

    with pytest.raises(EngineeringContextRejected):
        service.get_context(EngineeringContextRequest(project_id="project-1", task="GPIO12"))


def test_explicit_fact_conflict_is_carried_without_overwriting_evidence() -> None:
    source = Source(
        {
            "memory": (
                evidence(
                    ContextSourceType.ENGINEERING_MEMORY,
                    "memory-1",
                    key="gpio12.function",
                    value="spi-flash",
                ),
            ),
            "graph": (
                evidence(
                    ContextSourceType.KNOWLEDGE_GRAPH,
                    "graph-1",
                    key="gpio12.function",
                    value="reserved",
                ),
            ),
        }
    )
    snapshot = EngineeringContextService(source_port=source).get_context(
        EngineeringContextRequest(project_id="project-1", task="GPIO12")
    )
    assert snapshot is not None
    assert len(snapshot.conflicts) == 1
    assert snapshot.conflicts[0].resolution_required is True
    assert snapshot.verification_status == "REQUIRES_REVIEW"
    assert snapshot.memory_evidence[0].fact_value == "spi-flash"
    assert snapshot.graph_evidence[0].fact_value == "reserved"


def test_different_free_text_without_fact_keys_is_not_a_conflict() -> None:
    source = Source(
        {
            "memory": (evidence(ContextSourceType.ENGINEERING_MEMORY, "memory-1"),),
            "graph": (evidence(ContextSourceType.KNOWLEDGE_GRAPH, "graph-1"),),
        }
    )
    snapshot = EngineeringContextService(source_port=source).get_context(
        EngineeringContextRequest(project_id="project-1", task="GPIO12")
    )
    assert snapshot is not None
    assert snapshot.conflicts == ()


def test_legacy_query_is_a_read_only_request_adapter() -> None:
    source = Source({"memory": (evidence(ContextSourceType.ENGINEERING_MEMORY, "memory-1"),)})
    snapshot = EngineeringContextService(source_port=source).get_context(
        EngineeringContextQuery(project_id="project-1", query="GPIO12")
    )
    assert snapshot is not None
    assert snapshot.task_description == "GPIO12"


def test_source_failures_are_mapped_to_fixed_sanitized_errors() -> None:
    class FailedSource(Source):
        def read_memory(self, request):
            raise RuntimeError("C:\\private\\secret-token.txt")

    from embedded_copilot.engineering_context import EngineeringContextUnavailable

    service = EngineeringContextService(source_port=FailedSource({}))
    with pytest.raises(EngineeringContextUnavailable) as captured:
        service.get_context(EngineeringContextRequest(project_id="project-1", task="GPIO12"))
    assert str(captured.value) == "engineering context projection unavailable"
    assert "private" not in str(captured.value)
