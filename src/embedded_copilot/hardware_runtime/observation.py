from .models import HardwareObservation, HardwareObservationStatus


def is_observed(value: HardwareObservation) -> bool:
    return value.status is HardwareObservationStatus.OBSERVED


__all__ = ("is_observed",)
