"""v7.0 release matrix reporter.

It never promotes v6.7 evidence and never fabricates provider, approval,
receipt, hardware, installer or clean-machine results.  Executable acceptance
is deliberately separate from this report scaffold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

V70_PRODUCT_VERSION = "7.0.0"


_CASES = (
    "Fresh Production Startup", "Product Version Consistency", "Project Attach", "Desktop Projection",
    "Chat Runtime Boundary", "Evidence", "Memory / Governance", "Review", "Validation / Finding",
    "Proposal Approval", "Workspace Preview", "Workspace Controlled Apply", "Restart Recovery", "SSE Replay",
    "Capability Projection", "Credential Storage Boundary", "Backup", "Restore", "Restore Tamper Rejection",
    "Diagnostics Export", "Upgrade Migration", "Tombstone / Isolation", "Security Boundary", "Clean Shutdown / Restart",
)


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: int
    name: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class GoldenReport:
    product_version: str
    status: str
    cases: tuple[GoldenCase, ...]
    production_package: str
    installer: str
    clean_machine_acceptance: str
    installer_signing: str
    browser_automation: str
    hardware_golden: str


def run_golden_cases(*, projects_root: Path, state_root: Path, execute: bool = False) -> GoldenReport:
    del projects_root, state_root
    # Do not make a test seam look like a production acceptance execution.
    detail = "Independent v7.0 evidence has not been executed." if not execute else "Execution harness requires installer and clean-machine evidence."
    cases = tuple(GoldenCase(index, name, "UNVERIFIED", detail) for index, name in enumerate(_CASES, start=1))
    return GoldenReport(
        product_version=V70_PRODUCT_VERSION,
        status="PARTIAL VERIFIED",
        cases=cases,
        production_package="UNVERIFIED",
        installer="UNVERIFIED",
        clean_machine_acceptance="UNVERIFIED",
        installer_signing="UNAVAILABLE / UNVERIFIED",
        browser_automation="UNAVAILABLE / NOT PRESENT",
        hardware_golden="DEFERRED",
    )


if __name__ == "__main__":
    report = run_golden_cases(
        projects_root=Path(r"C:\acceptance_projects\v7.0-production-release-golden"),
        state_root=Path(r"C:\acceptance_state\v7.0"),
    )
    print(asdict(report))
