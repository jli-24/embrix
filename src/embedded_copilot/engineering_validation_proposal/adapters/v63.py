from __future__ import annotations

from ..models import ApprovedContextRecord, EvidenceRecord


class V63EvidenceSource:
    """Read-only projection from the existing v6.3 SQLite governance store."""

    def __init__(self, store: object) -> None:
        self._store = store

    def get_evidence(self, tenant_id: str, project_id: str, evidence_id: str) -> EvidenceRecord | None:
        value = self._store.get_evidence(tenant_id, project_id, evidence_id)
        if value is None:
            return None
        return EvidenceRecord(
            evidence_id=value.evidence_id,
            tenant_id=value.tenant_id,
            project_id=value.project_id,
            source_reference=value.source_reference,
            evidence_fingerprint=value.evidence_fingerprint,
            status="ACTIVE",
            summary="Registered v6.3 Engineering Evidence.",
        )


class V63ApprovedContextSource:
    """Expose only approved/active v6.3 Memory and Knowledge as context."""

    def __init__(self, store: object) -> None:
        self._store = store

    @staticmethod
    def _record(value: object, *, kind: str) -> ApprovedContextRecord | None:
        if value is None:
            return None
        status = getattr(getattr(value, "status", None), "value", getattr(value, "status", ""))
        if status not in {"ACTIVE", "APPROVED"}:
            return None
        fingerprint = getattr(value, "memory_fingerprint", getattr(value, "knowledge_fingerprint", None))
        context_id = getattr(value, "memory_id", getattr(value, "knowledge_id", None))
        if not isinstance(fingerprint, str) or not isinstance(context_id, str):
            return None
        return ApprovedContextRecord(
            context_id=context_id,
            tenant_id=value.tenant_id,
            project_id=value.project_id,
            context_kind=kind,
            status=status,
            context_fingerprint=fingerprint,
        )

    def get_memory(self, tenant_id: str, project_id: str, memory_id: str) -> ApprovedContextRecord | None:
        return self._record(self._store.get_memory(tenant_id, project_id, memory_id), kind="MEMORY")

    def get_knowledge(self, tenant_id: str, project_id: str, knowledge_id: str) -> ApprovedContextRecord | None:
        return self._record(self._store.get_knowledge(tenant_id, project_id, knowledge_id), kind="KNOWLEDGE")


__all__ = ("V63ApprovedContextSource", "V63EvidenceSource")
