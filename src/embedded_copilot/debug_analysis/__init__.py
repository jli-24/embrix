from typing import TYPE_CHECKING, Any

from .contracts import (
    DebugAnalysisPort,
    DebugAnalysisSnapshot,
    DebugCategory,
    DebugFinding,
    DebugInputSnapshot,
    DebugSeverity,
    DebugSourceType,
    DebugStatus,
    validate_analysis_snapshot,
    validate_finding,
)
from .exceptions import (
    DebugAnalysisError,
    DebugAnalysisRejected,
    DebugAnalysisUnavailable,
)

__all__ = [
    "DebugAnalysisError",
    "DebugAnalysisPort",
    "DebugAnalysisRejected",
    "DebugAnalysisService",
    "DebugAnalysisSnapshot",
    "DebugCategory",
    "DebugFinding",
    "DebugInputSnapshot",
    "DebugSeverity",
    "DebugSourceType",
    "DebugStatus",
    "validate_analysis_snapshot",
    "validate_finding",
]

_LAZY_EXPORTS = {"DebugAnalysisService": "embedded_copilot.debug_analysis.service"}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module
    return getattr(import_module(target, __name__), name)

if TYPE_CHECKING:
    from embedded_copilot.debug_analysis.service import (
        DebugAnalysisService as DebugAnalysisService,
    )
