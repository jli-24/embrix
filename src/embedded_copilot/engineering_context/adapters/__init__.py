"""Internal read-only adapters for existing Engineering Context ports."""

from .project_knowledge import ProjectKnowledgeEvidenceAdapter
from .external_knowledge import ExternalKnowledgeEvidenceAdapter
from .project_engineering import ProjectEngineeringEvidenceAdapter

__all__ = ("ExternalKnowledgeEvidenceAdapter", "ProjectEngineeringEvidenceAdapter", "ProjectKnowledgeEvidenceAdapter",)
