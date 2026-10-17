"""Pure navigation policy for the Native Desktop Shell."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class NavigationAction(StrEnum):
    ALLOW_INTERNAL = "ALLOW_INTERNAL"
    OPEN_EXTERNAL = "OPEN_EXTERNAL"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class NavigationDecision:
    action: NavigationAction
    url: str
    reason: str


def _same_origin(url: str, origin: str) -> bool:
    parsed = urlsplit(url)
    expected = urlsplit(origin)
    return (
        parsed.scheme == expected.scheme == "http"
        and parsed.netloc == expected.netloc
        and parsed.username is None
        and parsed.password is None
    )


def decide_navigation(
    url: str,
    *,
    internal_origin: str,
    approved_external_origins: tuple[str, ...] = (),
) -> NavigationDecision:
    """Classify a top-level navigation without generic localhost trust."""

    value = str(url)
    if _same_origin(value, internal_origin):
        return NavigationDecision(NavigationAction.ALLOW_INTERNAL, value, "current_runtime_origin")

    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.username is None and parsed.password is None:
        external_origin = f"https://{parsed.netloc}"
        if external_origin in approved_external_origins:
            return NavigationDecision(NavigationAction.OPEN_EXTERNAL, value, "approved_external_origin")

    if parsed.scheme == "file":
        return NavigationDecision(NavigationAction.BLOCK, value, "file_url_forbidden")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return NavigationDecision(NavigationAction.BLOCK, value, "untrusted_loopback_origin")
    return NavigationDecision(NavigationAction.BLOCK, value, "navigation_not_allowlisted")
