"""Public release identity with a dependency-free, single-source product version."""

from __future__ import annotations

from embedded_copilot._version import PRODUCT_VERSION

PRODUCT_NAME = "Embedded Copilot"
RELEASE_STATUS = "PARTIAL VERIFIED"


def product_version_payload() -> dict[str, str]:
    """Return only public, non-secret release identity fields."""

    return {
        "product_name": PRODUCT_NAME,
        "product_version": PRODUCT_VERSION,
        "release_status": RELEASE_STATUS,
    }


__all__ = ("PRODUCT_NAME", "PRODUCT_VERSION", "RELEASE_STATUS", "product_version_payload")
