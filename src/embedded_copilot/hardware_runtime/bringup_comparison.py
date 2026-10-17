from __future__ import annotations

from datetime import datetime

from pydantic import field_validator, model_validator

from .bringup_contracts import BringupResult, HardwareLineage
from .contracts import (
    FingerprintedHardwareContract,
    identifier,
    safe_text,
    tuple_only,
    utc_datetime,
)


class HardwareObservationDifference(FingerprintedHardwareContract):
    index: int
    expected: str | None
    simulation_observed: str | None
    hardware_observed: str | None

    @field_validator("index", mode="before")
    @classmethod
    def _index(cls, value: object) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("difference index is invalid")
        return value

    @field_validator("expected", "simulation_observed", "hardware_observed", mode="before")
    @classmethod
    def _values(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="observation_value", maximum=256)


class HardwareVerificationComparison(FingerprintedHardwareContract):
    comparison_id: str
    project_id: str
    hardware_session_id: str
    simulation_evidence_id: str
    hardware_evidence_id: str
    expected: tuple[str, ...]
    simulation_observed: tuple[str, ...]
    hardware_observed: tuple[str, ...]
    difference: tuple[HardwareObservationDifference, ...]
    result: BringupResult
    lineage: HardwareLineage
    created_at: datetime

    _ids = field_validator(
        "comparison_id", "project_id", "hardware_session_id", "simulation_evidence_id", "hardware_evidence_id", mode="before"
    )(lambda value, info: identifier(value, field=info.field_name))
    _values = field_validator("expected", "simulation_observed", "hardware_observed", mode="before")(
        lambda value, info: tuple(safe_text(item, field=info.field_name.removesuffix("_observed"), maximum=256) for item in tuple_only(value, field=info.field_name))
    )
    _difference = field_validator("difference", mode="before")(
        lambda value: tuple(
            HardwareObservationDifference.model_validate(item.model_dump(mode="python") if isinstance(item, HardwareObservationDifference) else item)
            for item in tuple_only(value, field="difference")
        )
    )
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))

    @model_validator(mode="after")
    def _binding(self):
        if self.project_id != self.lineage.project_id or self.hardware_session_id != self.lineage.hardware_session_id:
            raise ValueError("hardware comparison lineage mismatch")
        if self.result is BringupResult.PASS and (not self.simulation_observed or not self.hardware_observed or self.difference):
            raise ValueError("comparison PASS requires matching observed evidence")
        return self


def compare_hardware_observations(
    *,
    comparison_id: str,
    lineage: HardwareLineage,
    simulation_evidence_id: str,
    hardware_evidence_id: str,
    expected: tuple[str, ...],
    simulation_observed: tuple[str, ...],
    hardware_observed: tuple[str, ...],
    created_at: datetime,
) -> HardwareVerificationComparison:
    expected = tuple(expected)
    simulation_observed = tuple(simulation_observed)
    hardware_observed = tuple(hardware_observed)
    differences = tuple(
        HardwareObservationDifference(
            index=index,
            expected=expected[index] if index < len(expected) else None,
            simulation_observed=simulation_observed[index] if index < len(simulation_observed) else None,
            hardware_observed=hardware_observed[index] if index < len(hardware_observed) else None,
        )
        for index in range(max(len(expected), len(simulation_observed), len(hardware_observed)))
        if (
            (expected[index] if index < len(expected) else None)
            != (simulation_observed[index] if index < len(simulation_observed) else None)
            or (simulation_observed[index] if index < len(simulation_observed) else None)
            != (hardware_observed[index] if index < len(hardware_observed) else None)
        )
    )
    if not simulation_observed or not hardware_observed:
        result = BringupResult.UNKNOWN
    elif not differences:
        result = BringupResult.PASS
    else:
        result = BringupResult.FAIL
    return HardwareVerificationComparison(
        comparison_id=comparison_id,
        project_id=lineage.project_id,
        hardware_session_id=lineage.hardware_session_id,
        simulation_evidence_id=simulation_evidence_id,
        hardware_evidence_id=hardware_evidence_id,
        expected=expected,
        simulation_observed=simulation_observed,
        hardware_observed=hardware_observed,
        difference=differences,
        result=result,
        lineage=lineage,
        created_at=created_at,
    )


__all__ = ("HardwareObservationDifference", "HardwareVerificationComparison", "compare_hardware_observations")
