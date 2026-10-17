from __future__ import annotations

import copy
import hashlib

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    EngineeringContextSourcePort,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
    GraphProjection,
)

from .foundation import (
    DebugMemoryPayload,
    DecisionMemoryPayload,
    EvidenceMemoryPayload,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
    ProjectMemoryPayload,
)
from .foundation_repository import EngineeringMemoryRepositoryPort


def _revalidate_record(value: object) -> FoundationEngineeringMemoryRecord:
    if type(value) is not FoundationEngineeringMemoryRecord:
        raise TypeError("foundation memory record is invalid")
    return FoundationEngineeringMemoryRecord.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _summary(record: FoundationEngineeringMemoryRecord) -> str:
    payload = record.payload
    if isinstance(payload, ProjectMemoryPayload):
        return (
            f"project type {payload.project_type}; hardware target {payload.hardware_target}; "
            f"requirements {payload.requirement_summary}; architecture {payload.architecture_summary}"
        )
    if isinstance(payload, DecisionMemoryPayload):
        return f"choice {payload.choice}; reason {payload.reason}; context {payload.context}"
    if isinstance(payload, DebugMemoryPayload):
        return (
            f"symptom {payload.symptom}; root cause {payload.root_cause}; "
            f"fix {payload.fix}; verification {payload.verification}"
        )
    if isinstance(payload, EvidenceMemoryPayload):
        values = (
            ("validation", payload.validation_result),
            ("test", payload.test_result),
            ("execution", payload.execution_result),
        )
        return "; ".join(
            f"{label} {value.status} ({value.source_reference})"
            for label, value in values
            if value is not None
        )
    raise TypeError("foundation memory payload is invalid")


class EngineeringMemoryService:
    __slots__ = ("_repository",)

    def __init__(self, repository: EngineeringMemoryRepositoryPort) -> None:
        if not isinstance(repository, EngineeringMemoryRepositoryPort):
            raise TypeError("foundation memory repository is invalid")
        self._repository = repository

    def save_memory(
        self, record: FoundationEngineeringMemoryRecord
    ) -> FoundationEngineeringMemoryRecord:
        checked = _revalidate_record(record)
        stored = self._repository.save(copy.deepcopy(checked))
        result = _revalidate_record(stored)
        if result.identity != checked.identity or result.fingerprint != checked.fingerprint:
            raise ValueError("foundation memory save binding is invalid")
        return result

    def search_memory(
        self, query: FoundationMemoryQuery
    ) -> tuple[FoundationEngineeringMemoryRecord, ...]:
        if type(query) is not FoundationMemoryQuery:
            raise TypeError("foundation memory query is invalid")
        checked = FoundationMemoryQuery.model_validate(
            copy.deepcopy(query.model_dump(mode="python"))
        )
        values = self._repository.search(copy.deepcopy(checked))
        if type(values) is not tuple:
            raise TypeError("foundation memory search result is invalid")
        result = tuple(_revalidate_record(value) for value in values)
        if any(
            value.tenant_id != checked.tenant_id or value.project_id != checked.project_id
            for value in result
        ):
            raise ValueError("foundation memory search binding is invalid")
        return result

    def build_context(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]:
        if type(request) is not EngineeringContextRequest:
            raise TypeError("engineering context request is invalid")
        checked = EngineeringContextRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        query = FoundationMemoryQuery(
            tenant_id=checked.tenant_id,
            project_id=checked.project_id,
            query=" ".join((checked.task, *checked.constraints)),
        )
        records = self.search_memory(query)
        # Preserve the Repository's deterministic relevance ordering so the
        # context layer does not discard token-overlap ranking information.
        return tuple(self._project(record) for record in records)

    @staticmethod
    def _project(
        record: FoundationEngineeringMemoryRecord,
    ) -> EngineeringContextEvidence:
        # The existing context identifier is bounded to the public contract's
        # identifier length.  Derive a stable digest from the record identity so
        # even a maximum-length Foundation memory id remains representable.
        evidence_id = "foundation-memory-" + hashlib.sha256(
            "\x1f".join(record.identity).encode("utf-8")
        ).hexdigest()
        return EngineeringContextEvidence.create(
            evidence_id=evidence_id,
            tenant_id=record.tenant_id,
            project_id=record.project_id,
            source_type=ContextSourceType.ENGINEERING_MEMORY,
            subject=f"{record.memory_type.value} {record.memory_id}",
            summary=_summary(record),
            source_reference=record.source_references[0],
            source_fingerprint=record.fingerprint,
            confidence=None,
            verification_status=ContextVerificationStatus.REQUIRES_REVIEW,
            trust_level=EvidenceTrustLevel.CANDIDATE,
            evidence_status=EvidenceVerificationStatus.CANDIDATE,
            observed_at=record.created_at.isoformat(),
            provenance=("foundation-candidate",),
        )


class EngineeringMemoryContextProjectionAdapter:
    __slots__ = ("_service", "_source")

    def __init__(
        self,
        *,
        service: EngineeringMemoryService,
        source: EngineeringContextSourcePort,
    ) -> None:
        if not isinstance(service, EngineeringMemoryService):
            raise TypeError("foundation memory service is invalid")
        if not isinstance(source, EngineeringContextSourcePort):
            raise TypeError("engineering context source is invalid")
        self._service = service
        self._source = source

    def read_memory(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]:
        base = self._source.read_memory(copy.deepcopy(request))
        if type(base) is not tuple or any(
            type(item) is not EngineeringContextEvidence for item in base
        ):
            raise TypeError("engineering memory source result is invalid")
        foundation = self._service.build_context(copy.deepcopy(request))
        return tuple(base) + tuple(foundation)

    def read_graph(self, request: EngineeringContextRequest) -> GraphProjection:
        return self._source.read_graph(copy.deepcopy(request))

    def read_knowledge(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]:
        return self._source.read_knowledge(copy.deepcopy(request))

    def read_multimodal(
        self, request: EngineeringContextRequest
    ) -> tuple[EngineeringContextEvidence, ...]:
        return self._source.read_multimodal(copy.deepcopy(request))


__all__ = (
    "EngineeringMemoryContextProjectionAdapter",
    "EngineeringMemoryService",
)
