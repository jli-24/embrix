class ProjectCatalogError(Exception):
    """Base error for the project-first desktop boundary."""


class ProjectCatalogRejected(ProjectCatalogError):
    pass


class ProjectLocationInvalid(ProjectCatalogRejected):
    pass


class ProjectLocationPermissionDenied(ProjectCatalogRejected):
    pass


class ProjectTargetNotWritable(ProjectLocationPermissionDenied):
    pass


class ProjectLocationUnsafe(ProjectCatalogRejected):
    pass


class ProjectCatalogConflict(ProjectCatalogError):
    pass


class ProjectCatalogForbidden(ProjectCatalogError):
    pass


class ProjectCatalogExpired(ProjectCatalogRejected):
    pass


class ProjectCatalogUnavailable(ProjectCatalogError):
    pass


class ProjectCatalogNotFound(ProjectCatalogRejected):
    pass


class EvidenceReviewRequired(ProjectCatalogRejected):
    """The candidate evidence has not passed the existing human review flow."""


class ArtifactIntelligenceRejected(ProjectCatalogRejected):
    """Artifact intelligence input or deterministic parsing was rejected."""


class ExecutionAuthorizationDecisionRejected(ProjectCatalogRejected):
    """A single authorization decision failed its identity or state checks."""


class FirmwareSourceGenerationRejected(ProjectCatalogRejected):
    """A firmware proposal or its canonical operation binding was rejected."""


class SchematicProductionRejected(ProjectCatalogRejected):
    """A schematic readiness, proposal, or canonical operation binding was rejected."""


class PCBProductionRejected(ProjectCatalogRejected):
    """A PCB readiness, proposal, or canonical operation binding was rejected."""


class HardwareSelectionRejected(ProjectCatalogRejected):
    """A hardware component selection proposal or decision was rejected."""


class SchematicPlanningRejected(ProjectCatalogRejected):
    """A production schematic planning proposal or decision was rejected."""


class RequirementSuggestionDecisionRejected(ProjectCatalogRejected):
    """A reference-derived requirement suggestion decision failed validation."""


class RequirementChangeRejected(ProjectCatalogRejected):
    """A G0R typed requirement proposal or confirmation failed validation."""
