"""Canonical v6.4 validation and proposal production boundary.

This package is deliberately service-oriented.  It does not expose agents,
workspace writers, build runners, device handles, or execution ports.
"""

from .exceptions import (
    ProposalConflict,
    ProposalRejected,
    ProposalUnavailable,
    ValidationProposalError,
)
from .models import (
    ApprovedContextRecord,
    AuditEvent,
    AuditEventType,
    EngineeringFinding,
    EvidenceRecord,
    FindingDraft,
    FindingSeverity,
    FindingType,
    OperationProposal,
    ProposalChange,
    ProposalReviewBinding,
    ProposalStatus,
    ProposalType,
    TargetIdentity,
    ValidationRequest,
    ValidationResult,
    ValidationStatus,
    ValidationType,
    canonical_fingerprint,
    fingerprint,
    identifier,
    utc_datetime,
)
from .ports import (
    ApprovedContextPort,
    ClockPort,
    EvidenceSourcePort,
    ProposalStorePort,
    ValidationRulePort,
)
from .service import ValidationProposalService
from .service import DeterministicValidationRules

__all__ = (
    "ApprovedContextPort",
    "ApprovedContextRecord",
    "AuditEvent",
    "AuditEventType",
    "ClockPort",
    "DeterministicValidationRules",
    "EngineeringFinding",
    "EvidenceRecord",
    "EvidenceSourcePort",
    "FindingSeverity",
    "FindingDraft",
    "FindingType",
    "OperationProposal",
    "ProposalChange",
    "ProposalConflict",
    "ProposalRejected",
    "ProposalReviewBinding",
    "ProposalStatus",
    "ProposalStorePort",
    "ProposalType",
    "ProposalUnavailable",
    "TargetIdentity",
    "ValidationProposalError",
    "ValidationProposalService",
    "ValidationRequest",
    "ValidationResult",
    "ValidationRulePort",
    "ValidationStatus",
    "ValidationType",
    "canonical_fingerprint",
    "fingerprint",
    "identifier",
    "utc_datetime",
)
