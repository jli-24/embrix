from .contracts import (
    ApprovedMemoryProjectionPort,
    ContextCategory,
    ContextConflict,
    ContextContract,
    ContextSourceReference,
    ContextSourceType,
    ContextVerificationStatus,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextProviderPort,
    EngineeringEvidenceProjectionPort,
    EngineeringContextQuery,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
    EngineeringContextSourcePort,
    EngineeringGraphProjectionPort,
    ExternalKnowledgeEvidenceProjectionPort,
    GraphProjection,
    KnowledgeEvidenceProjectionPort,
    MultimodalContextEvidence,
    MultimodalContextFusionRequest,
    MultimodalProjectionPort,
)
from .exceptions import (
    EngineeringContextError,
    EngineeringContextExternalEvidenceUnavailable,
    EngineeringContextRejected,
    EngineeringContextUnavailable,
)
from .factory import create_engineering_context_service
from .fusion import (
    ContextFusionService,
    EngineeringContextFusionService,
    MultimodalContextFusionService,
    fuse_projections,
)
from .projection import (
    ReadOnlyProjectionSource,
    project_graph_snapshot,
    project_knowledge_evidence,
    project_memory_projection,
    project_multimodal_projection,
    project_vision_observation,
)
from .retrieval import EngineeringContextRetrievalService, retrieve_context
from .context_overlay import (
    ContextEvidenceType,
    ContextOverlayEvidence,
    EngineeringContextOverlay,
)
from .service import EngineeringContextService

# Compatibility aliases preserve existing imports without defining runtime behavior
# inside the engineering-context package.
RuntimeEvidenceType = ContextEvidenceType
RuntimeContextEvidence = ContextOverlayEvidence
EngineeringRuntimeContextOverlay = EngineeringContextOverlay

__all__ = (
    "ApprovedMemoryProjectionPort",
    "ContextCategory",
    "ContextConflict",
    "ContextContract",
    "ContextFusionService",
    "ContextSourceReference",
    "ContextSourceType",
    "ContextVerificationStatus",
    "EvidenceTrustLevel",
    "EvidenceVerificationStatus",
    "EngineeringContextError",
    "EngineeringContextExternalEvidenceUnavailable",
    "EngineeringContextEvidence",
    "EngineeringContextFusionService",
    "EngineeringContextProviderPort",
    "EngineeringEvidenceProjectionPort",
    "EngineeringContextQuery",
    "EngineeringContextRejected",
    "EngineeringContextRequest",
    "EngineeringContextRetrievalService",
    "EngineeringRuntimeContextOverlay",
    "EngineeringContextService",
    "EngineeringContextSnapshot",
    "EngineeringContextSourcePort",
    "ExternalKnowledgeEvidenceProjectionPort",
    "EngineeringContextUnavailable",
    "EngineeringGraphProjectionPort",
    "GraphProjection",
    "KnowledgeEvidenceProjectionPort",
    "MultimodalContextEvidence",
    "MultimodalContextFusionRequest",
    "MultimodalContextFusionService",
    "MultimodalProjectionPort",
    "ReadOnlyProjectionSource",
    "RuntimeContextEvidence",
    "RuntimeEvidenceType",
    "create_engineering_context_service",
    "fuse_projections",
    "project_graph_snapshot",
    "project_knowledge_evidence",
    "project_memory_projection",
    "project_multimodal_projection",
    "project_vision_observation",
    "retrieve_context",
)
