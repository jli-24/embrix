from __future__ import annotations

import copy
from typing import Literal, Protocol, runtime_checkable

from pydantic import field_validator

from embedded_copilot.engineering_context import EngineeringRuntimeContextOverlay

from .contracts import FingerprintedHardwareContract, fingerprint, identifier
from .exceptions import HardwareRejected, HardwareUnavailable


class RuntimeMemoryCandidateReference(FingerprintedHardwareContract):
    project_id: str
    overlay_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    review_status: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"

    @field_validator("project_id", "candidate_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("overlay_fingerprint", "candidate_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)


@runtime_checkable
class RuntimeMemoryProjectionPort(Protocol):
    def project(
        self, overlay: EngineeringRuntimeContextOverlay
    ) -> tuple[RuntimeMemoryCandidateReference, ...]: ...


class RuntimeMemoryProjectionService:
    """Narrow bridge to existing Memory projection; it has no promotion method."""

    __slots__ = ("_port",)

    def __init__(self, port: RuntimeMemoryProjectionPort) -> None:
        if not isinstance(port, RuntimeMemoryProjectionPort):
            raise TypeError("runtime memory projection port is invalid")
        self._port = port

    def project(
        self, overlay: EngineeringRuntimeContextOverlay
    ) -> tuple[RuntimeMemoryCandidateReference, ...]:
        checked = EngineeringRuntimeContextOverlay.model_validate(
            copy.deepcopy(overlay.model_dump(mode="python"))
        )
        try:
            raw = self._port.project(copy.deepcopy(checked))
            if type(raw) is not tuple:
                raise TypeError("memory projection must be a tuple")
            results = tuple(
                RuntimeMemoryCandidateReference.model_validate(
                    copy.deepcopy(item.model_dump(mode="python"))
                )
                for item in raw
            )
        except Exception as error:
            raise HardwareUnavailable("memory projection unavailable") from error
        if not results or any(
            item.project_id != checked.project_id
            or item.overlay_fingerprint != checked.fingerprint
            or item.review_status != "REVIEW_REQUIRED"
            for item in results
        ):
            raise HardwareRejected("memory projection binding mismatch")
        return results


__all__ = (
    "RuntimeMemoryCandidateReference",
    "RuntimeMemoryProjectionPort",
    "RuntimeMemoryProjectionService",
)
