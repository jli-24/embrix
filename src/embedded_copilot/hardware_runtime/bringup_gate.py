from __future__ import annotations

from collections.abc import Mapping

from .bringup_contracts import BringupResult, GateCheck, HardwareBringupGateResult

REQUIRED_HARDWARE_BRINGUP_CHECKS = (
    "hardware_connected",
    "target_detected",
    "firmware_flash",
    "boot_verification",
    "uart_capture",
    "artifact_binding",
    "evidence_persistence",
    "restart_recovery",
)


def evaluate_hardware_bringup_gate(statuses: Mapping[str, BringupResult | str]) -> HardwareBringupGateResult:
    checks_list: list[GateCheck] = []
    for name in REQUIRED_HARDWARE_BRINGUP_CHECKS:
        if name not in statuses:
            checks_list.append(GateCheck(name=name, status=BringupResult.BLOCKED, reason="required check is missing"))
            continue
        value = statuses[name]
        checks_list.append(
            GateCheck(
                name=name,
                status=value if isinstance(value, BringupResult) else BringupResult(value),
                reason="required hardware bring-up check completed",
            )
        )
    checks = tuple(checks_list)
    if all(item.status is BringupResult.PASS for item in checks):
        status = "PASS"
        summary = "all hardware bring-up checks passed"
    elif any(item.status in {BringupResult.FAIL, BringupResult.STALE} for item in checks):
        status = "FAIL"
        summary = "hardware bring-up contains failed or stale evidence"
    else:
        status = "BLOCKED"
        summary = "hardware bring-up is incomplete or unavailable"
    return HardwareBringupGateResult(status=status, checks=checks, summary=summary)


__all__ = ("REQUIRED_HARDWARE_BRINGUP_CHECKS", "evaluate_hardware_bringup_gate")
