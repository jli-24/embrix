from typing import TYPE_CHECKING, Any

from .contracts import (
    BuildConfigurationProjection,
    BuildReadinessResult,
    BuildReadinessStatus,
    FirmwareBuildArtifact,
    FirmwareBuildConfiguration,
    FirmwareBuildPort,
    FirmwareBuildRequest,
    FirmwareBuildResult,
    FirmwareBuildResultStatus,
    FirmwareBuildStatus,
    FirmwareDebugAnalyzerPort,
    FirmwareEngineeringPort,
    FirmwareFailureReference,
    FirmwareFailureType,
    FirmwareFramework,
    FirmwareParserPort,
    FirmwareProjectSnapshot,
    FirmwareSourceArtifact,
    SourceProjection,
    validate_build_request,
    validate_build_result,
    validate_failure_reference,
    validate_project_snapshot,
)
from .exceptions import (
    BuildApprovalRequired,
    BuildFailed,
    BuildUnavailable,
    FirmwareDebugRejected,
    FirmwareEngineeringError,
    FirmwareNotFound,
    FirmwareRejected,
    FirmwareUnavailable,
)

__all__ = [
    "BuildApprovalRequired",
    "BuildConfigurationProjection",
    "BuildReadinessResult",
    "BuildReadinessStatus",
    "BuildFailed",
    "BuildUnavailable",
    "FirmwareBuildPort",
    "FirmwareBuildArtifact",
    "FirmwareBuildConfiguration",
    "FirmwareBuildRequest",
    "FirmwareBuildResult",
    "FirmwareBuildResultStatus",
    "FirmwareBuildService",
    "FirmwareBuildStatus",
    "FirmwareEngineeringError",
    "FirmwareEngineeringPort",
    "FirmwareDebugAnalyzerPort",
    "FirmwareDebugRejected",
    "FirmwareDebugService",
    "FirmwareNotFound",
    "FirmwareEngineeringService",
    "FirmwareFailureReference",
    "FirmwareFailureType",
    "FirmwareFramework",
    "FirmwareParserPort",
    "FirmwareProjectSnapshot",
    "FirmwareSourceArtifact",
    "FirmwareRejected",
    "FirmwareUnavailable",
    "SourceProjection",
    "validate_build_request",
    "validate_build_result",
    "validate_failure_reference",
    "validate_project_snapshot",
    "blocked_build_result",
    "create_firmware_build_request",
    "evaluate_build_readiness",
    "is_firmware_build_request",
]


_LAZY_EXPORTS = {'blocked_build_result': 'embedded_copilot.firmware_engineering.readiness', 'create_firmware_build_request': 'embedded_copilot.firmware_engineering.readiness', 'evaluate_build_readiness': 'embedded_copilot.firmware_engineering.readiness', 'is_firmware_build_request': 'embedded_copilot.firmware_engineering.readiness', 'FirmwareBuildService': 'embedded_copilot.firmware_engineering.service', 'FirmwareDebugService': 'embedded_copilot.firmware_engineering.service', 'FirmwareEngineeringService': 'embedded_copilot.firmware_engineering.service'}

def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module
    return getattr(import_module(target, __name__), name)

if TYPE_CHECKING:
    from embedded_copilot.firmware_engineering.readiness import (
        blocked_build_result as blocked_build_result,
    )
    from embedded_copilot.firmware_engineering.readiness import (
        create_firmware_build_request as create_firmware_build_request,
    )
    from embedded_copilot.firmware_engineering.readiness import (
        evaluate_build_readiness as evaluate_build_readiness,
    )
    from embedded_copilot.firmware_engineering.readiness import (
        is_firmware_build_request as is_firmware_build_request,
    )
    from embedded_copilot.firmware_engineering.service import (
        FirmwareBuildService as FirmwareBuildService,
    )
    from embedded_copilot.firmware_engineering.service import (
        FirmwareDebugService as FirmwareDebugService,
    )
    from embedded_copilot.firmware_engineering.service import (
        FirmwareEngineeringService as FirmwareEngineeringService,
    )
