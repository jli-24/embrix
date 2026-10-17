from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
)
from embedded_copilot.knowledge.external import (
    ExternalKnowledgeSearchUnavailable,
    OfficialWebSearchClient,
    OfficialWebSearchSettings,
    OfficialWebSearchTransport,
)
from embedded_copilot.engineering_context.exceptions import (
    EngineeringContextExternalEvidenceUnavailable,
)


class ExternalKnowledgeEvidenceAdapter:
    """Project-bound projection of explicit, sanitized external search results."""

    __slots__ = ("_client",)

    def __init__(
        self,
        *,
        settings: OfficialWebSearchSettings,
        transport: OfficialWebSearchTransport | None = None,
    ) -> None:
        self._client = OfficialWebSearchClient(settings=settings, transport=transport)

    def list_evidence(
        self, project_id: str, task: str, constraints: tuple[str, ...]
    ) -> tuple[EngineeringContextEvidence, ...]:
        try:
            values = self._client.search(task, constraints)
        except ExternalKnowledgeSearchUnavailable as error:
            raise EngineeringContextExternalEvidenceUnavailable() from error
        return tuple(
            EngineeringContextEvidence.create(
                evidence_id=item.result_id,
                project_id=project_id,
                source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
                subject=item.title,
                summary=item.summary,
                source_reference=item.url,
                source_fingerprint=item.source_fingerprint,
                source_domain=item.domain,
                retrieved_at=item.retrieved_at,
                document_date=item.document_date,
                license_reference=item.license_reference,
                confidence=None,
                verification_status=ContextVerificationStatus.UNVERIFIED,
            )
            for item in values
        )


__all__ = ("ExternalKnowledgeEvidenceAdapter",)
