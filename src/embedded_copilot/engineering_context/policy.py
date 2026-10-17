from .models import (
    ContextCategory,
    ContextSourceType,
    ContextVerificationStatus,
)


class ContextPolicy:
    """Pure mappings used by source projection adapters."""

    @staticmethod
    def status_is_usable(value: str) -> bool:
        try:
            ContextVerificationStatus(value)
        except (TypeError, ValueError):
            return False
        return value != ContextVerificationStatus.REQUIRES_REVIEW.value


__all__ = (
    "ContextCategory",
    "ContextPolicy",
    "ContextSourceType",
    "ContextVerificationStatus",
)
