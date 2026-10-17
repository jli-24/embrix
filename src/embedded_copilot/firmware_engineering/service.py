from __future__ import annotations

import copy

from .contracts import (
    FirmwareBuildPort,
    BuildReadinessResult,
    FirmwareBuildRequest,
    FirmwareBuildResult,
    FirmwareDebugAnalyzerPort,
    FirmwareFailureReference,
    FirmwareParserPort,
    FirmwareProjectSnapshot,
    validate_build_request,
    validate_build_result,
    validate_project_snapshot,
)
from .exceptions import (
    BuildApprovalRequired,
    BuildFailed,
    BuildUnavailable,
    FirmwareEngineeringError,
    FirmwareDebugRejected,
    FirmwareRejected,
    FirmwareUnavailable,
)
from .contracts import FirmwareBuildStatus
from .models import identifier
from .readiness import evaluate_build_readiness


class FirmwareEngineeringService:
    __slots__ = ("_parser",)

    def __init__(self, parser: FirmwareParserPort | None = None) -> None:
        if parser is not None and not isinstance(parser, FirmwareParserPort):
            raise TypeError("firmware parser port is invalid")
        self._parser = parser

    def parse(self, firmware_reference: str) -> FirmwareProjectSnapshot:
        if self._parser is None:
            raise FirmwareUnavailable()
        try:
            reference = identifier(firmware_reference, field="firmware_reference")
            result = validate_project_snapshot(
                self._parser.parse(copy.deepcopy(reference))
            )
            if result.firmware_reference != reference:
                raise ValueError("firmware reference binding mismatch")
            return result
        except FirmwareEngineeringError:
            raise
        except Exception as error:
            raise FirmwareRejected() from error


class FirmwareBuildService:
    __slots__ = ("_port",)

    def __init__(self, port: FirmwareBuildPort | None = None) -> None:
        if port is not None and not isinstance(port, FirmwareBuildPort):
            raise TypeError("firmware build port is invalid")
        self._port = port

    def build(self, request: FirmwareBuildRequest) -> FirmwareBuildResult:
        checked = validate_build_request(request)
        if checked.approval_reference is None:
            raise BuildApprovalRequired()
        if self._port is None:
            raise BuildUnavailable()
        try:
            result = validate_build_result(self._port.build(copy.deepcopy(checked)))
            if result.status is FirmwareBuildStatus.UNAVAILABLE or result.build_status is FirmwareBuildStatus.UNAVAILABLE:
                raise BuildUnavailable()
            if result.status is FirmwareBuildStatus.FAILED or result.build_status is FirmwareBuildStatus.FAILED:
                raise BuildFailed()
            if result.status is FirmwareBuildStatus.SUCCESS:
                return result
            return result
        except (BuildApprovalRequired, BuildUnavailable, BuildFailed):
            raise
        except Exception as error:
            raise BuildFailed() from error

    def readiness(self, request: FirmwareBuildRequest, *, workspace_root: object | None = None, tool: object | None = None) -> BuildReadinessResult:
        """Expose the deterministic preflight without granting execution authority."""

        return evaluate_build_readiness(
            validate_build_request(request),
            workspace_root=workspace_root,
            tool=tool,
        )

    def get_snapshot(self, project_id: str) -> FirmwareBuildResult | None:
        if self._port is None:
            raise BuildUnavailable()
        try:
            result = self._port.get_snapshot(
                copy.deepcopy(identifier(project_id, field="project_id"))
            )
            return None if result is None else validate_build_result(result)
        except FirmwareEngineeringError:
            raise
        except Exception as error:
            raise BuildFailed() from error


class FirmwareDebugService:
    __slots__ = ("_analyzer",)

    def __init__(self, analyzer: FirmwareDebugAnalyzerPort | None = None) -> None:
        if analyzer is not None and not isinstance(analyzer, FirmwareDebugAnalyzerPort):
            raise TypeError("firmware debug analyzer port is invalid")
        self._analyzer = analyzer

    def analyze(self, failure: FirmwareFailureReference):
        from embedded_copilot.debug_analysis.contracts import validate_analysis_snapshot

        try:
            checked = FirmwareFailureReference.model_validate(copy.deepcopy(failure))
            if self._analyzer is None:
                raise FirmwareUnavailable()
            result = validate_analysis_snapshot(
                self._analyzer.analyze(copy.deepcopy(checked))
            )
            if (
                result.project_id != checked.project_id
                or result.failure_reference != checked.evidence_reference
            ):
                raise ValueError("firmware debug identity mismatch")
            return result
        except (FirmwareUnavailable, FirmwareDebugRejected):
            raise
        except Exception as error:
            raise FirmwareDebugRejected() from error

__all__ = ["FirmwareBuildService", "FirmwareDebugService", "FirmwareEngineeringService"]
