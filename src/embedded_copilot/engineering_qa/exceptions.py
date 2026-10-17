from __future__ import annotations


class EngineeringQAError(RuntimeError):
    pass


class InsufficientEvidence(EngineeringQAError):
    def __init__(self) -> None:
        super().__init__("insufficient engineering evidence")


class EngineeringAnswerRejected(EngineeringQAError):
    def __init__(
        self,
        *,
        reason_code: str = "ANSWER_CONTRACT_MISMATCH",
        validation_stage: str = "ENGINEERING_ANSWER",
        gateway_invocation_count: int = 1,
        provider_response_present: bool = True,
        proposed_requirements_count: int = 0,
        open_questions_count: int = 0,
        answer_text_present: bool = False,
    ) -> None:
        super().__init__("engineering answer rejected")
        self.reason_code = reason_code
        self.validation_stage = validation_stage
        self.gateway_invocation_count = gateway_invocation_count
        self.provider_response_present = provider_response_present
        self.proposed_requirements_count = proposed_requirements_count
        self.open_questions_count = open_questions_count
        self.answer_text_present = answer_text_present


__all__ = ("EngineeringAnswerRejected", "EngineeringQAError", "InsufficientEvidence")
