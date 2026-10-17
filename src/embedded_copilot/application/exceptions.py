from __future__ import annotations


class ApplicationError(RuntimeError):
    """Base error for the application composition boundary."""


class ApplicationDependencyError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("application dependency is invalid")


class ApplicationRejected(ApplicationError):
    def __init__(self) -> None:
        super().__init__("application request was rejected")


class ApplicationContextUnavailable(ApplicationError):
    def __init__(self, *, context=None) -> None:
        super().__init__("application context is unavailable")
        self.context = context


class ApplicationExternalEvidenceUnavailable(ApplicationContextUnavailable):
    def __init__(self) -> None:
        super().__init__()


class ApplicationInsufficientEvidence(ApplicationContextUnavailable):
    def __init__(self, *, context=None, reason_code: str = "INSUFFICIENT_EVIDENCE") -> None:
        super().__init__()
        self.context = context
        self.reason_code = reason_code


class ApplicationModelUnavailable(ApplicationError):
    def __init__(self, *, context=None, configured: bool = False, reason_code: str | None = None) -> None:
        super().__init__("application model is unavailable")
        self.context = context
        self.configured = configured
        self.reason_code = reason_code
        self.gateway_invocation_count = 0
        self.provider_response_present = False
        self.validation_stage = "PROVIDER_RESOLVE"
        self.proposed_requirements_count = 0
        self.open_questions_count = 0
        self.answer_text_present = False


class ApplicationAnswerRejected(ApplicationError):
    def __init__(
        self,
        *,
        reason_code: str = "ANSWER_CONTRACT_MISMATCH",
        validation_stage: str = "APPLICATION_EVIDENCE",
        gateway_invocation_count: int = 1,
        provider_response_present: bool = True,
        proposed_requirements_count: int = 0,
        open_questions_count: int = 0,
        answer_text_present: bool = False,
    ) -> None:
        super().__init__("application answer was rejected")
        self.reason_code = reason_code
        self.validation_stage = validation_stage
        self.gateway_invocation_count = gateway_invocation_count
        self.provider_response_present = provider_response_present
        self.proposed_requirements_count = proposed_requirements_count
        self.open_questions_count = open_questions_count
        self.answer_text_present = answer_text_present


class ApplicationAuditUnavailable(ApplicationError):
    def __init__(self) -> None:
        super().__init__("application audit is unavailable")


class ApplicationAuditConflict(ApplicationAuditUnavailable):
    def __init__(self) -> None:
        super().__init__()


__all__ = (
    "ApplicationAnswerRejected",
    "ApplicationAuditConflict",
    "ApplicationAuditUnavailable",
    "ApplicationContextUnavailable",
    "ApplicationExternalEvidenceUnavailable",
    "ApplicationDependencyError",
    "ApplicationError",
    "ApplicationInsufficientEvidence",
    "ApplicationModelUnavailable",
    "ApplicationRejected",
)
