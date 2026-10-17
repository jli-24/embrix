from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_intelligence import EvidenceGate, EvidenceGateRejected
from embedded_copilot.engineering_qa import EngineeringQAService, EngineeringQuestion, InsufficientEvidence


def test_approved_memory_is_context_only_and_cannot_satisfy_current_evidence_gate() -> None:
    memory = EngineeringContextEvidence.create(
        evidence_id="memory-1",
        tenant_id="tenant-1",
        project_id="project-1",
        source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="decision",
        summary="Approved historical decision.",
        source_reference="src:memory-1",
        source_fingerprint="sha256:" + "a" * 64,
        verification_status=ContextVerificationStatus.APPROVED,
        provenance=("memory-fingerprint",),
    )
    with pytest.raises(EvidenceGateRejected, match="insufficient evidence"):
        EvidenceGate().require((memory,), tenant_id="tenant-1", project_id="project-1")


def test_projection_dto_rejects_raw_secret_or_local_path() -> None:
    with pytest.raises(ValueError):
        EngineeringContextEvidence.create(
            evidence_id="memory-1",
            tenant_id="tenant-1",
            project_id="project-1",
            source_type=ContextSourceType.ENGINEERING_MEMORY,
            subject="decision",
            summary="C:\\private\\source.txt",
            source_reference="src:memory-1",
            source_fingerprint="sha256:" + "a" * 64,
            verification_status=ContextVerificationStatus.APPROVED,
            provenance=("memory-fingerprint",),
        )


def test_approved_memory_plus_zero_current_evidence_never_invokes_model_gateway() -> None:
    memory = EngineeringContextEvidence.create(
        evidence_id="memory-1", tenant_id="tenant-1", project_id="project-1",
        source_type=ContextSourceType.ENGINEERING_MEMORY, subject="decision",
        summary="Approved historical decision.", source_reference="src:memory-1",
        source_fingerprint="sha256:" + "a" * 64,
        verification_status=ContextVerificationStatus.APPROVED,
        provenance=("sha256:" + "c" * 64,),
    )
    snapshot = EngineeringContextSnapshot.create(
        tenant_id="tenant-1", project_id="project-1", task_description="Question",
        memory_evidence=(memory,), graph_evidence=(), knowledge_evidence=(),
        multimodal_evidence=(), conflicts=(), confidence=0.8,
        provenance=(memory.source(),), verification_status=ContextVerificationStatus.APPROVED,
    )

    class Context:
        def get_context(self, request):
            return snapshot

    class Gateway:
        calls = 0

        def generate(self, request):
            self.calls += 1
            raise AssertionError("ModelGateway must not be invoked")

        async def agenerate(self, request):
            self.calls += 1
            raise AssertionError("ModelGateway must not be invoked")

    gateway = Gateway()
    with pytest.raises(InsufficientEvidence):
        EngineeringQAService(
            context_provider=Context(), model_gateway=gateway
        ).answer(EngineeringQuestion(
            question_id="question-1", project_id="project-1", question="Question"
        ))
    assert gateway.calls == 0
