from __future__ import annotations


class EngineeringAuditError(Exception):
    """Base error for the isolated engineering audit boundary."""


class AuditCreationError(EngineeringAuditError):
    """Audit evidence could not be created from the supplied provenance."""


class AuditIncomplete(AuditCreationError):
    """Required engineering evidence is incomplete."""


class InvalidAuditEvent(AuditCreationError):
    """An event type, decision, or lifecycle relation is invalid."""


class AuditConflict(EngineeringAuditError):
    """An immutable audit identity conflicts with existing content."""


class AuditWriteFailed(EngineeringAuditError):
    """The append-only audit store could not persist an event safely."""


class AuditUnavailable(EngineeringAuditError):
    """An injected read-only audit dependency is unavailable or malformed."""


class ReviewRecordNotFound(AuditCreationError):
    """The referenced v3.8 ReviewRecord does not exist."""


class ComplianceReportError(EngineeringAuditError):
    """A compliance report could not be generated or verified safely."""


__all__ = (
    "AuditConflict",
    "AuditCreationError",
    "AuditIncomplete",
    "AuditUnavailable",
    "AuditWriteFailed",
    "ComplianceReportError",
    "EngineeringAuditError",
    "InvalidAuditEvent",
    "ReviewRecordNotFound",
)

