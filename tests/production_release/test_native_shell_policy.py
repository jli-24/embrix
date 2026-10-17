from __future__ import annotations

from embedded_copilot.native_shell.policy import (
    NavigationAction,
    decide_navigation,
)

ORIGIN = "http://127.0.0.1:43123"


def test_policy_allows_only_exact_current_runtime_origin() -> None:
    assert decide_navigation(f"{ORIGIN}/desktop/#bootstrap", internal_origin=ORIGIN).action is NavigationAction.ALLOW_INTERNAL
    assert decide_navigation("http://localhost:43123/desktop/", internal_origin=ORIGIN).action is NavigationAction.BLOCK
    assert decide_navigation("http://127.0.0.1:43124/desktop/", internal_origin=ORIGIN).action is NavigationAction.BLOCK
    assert decide_navigation("http://127.0.0.1:43123.evil.example/", internal_origin=ORIGIN).action is NavigationAction.BLOCK


def test_policy_blocks_file_and_unapproved_external_navigation() -> None:
    assert decide_navigation("file:///C:/secret.txt", internal_origin=ORIGIN).action is NavigationAction.BLOCK
    assert decide_navigation("https://evil.example/", internal_origin=ORIGIN).action is NavigationAction.BLOCK


def test_policy_hands_only_approved_https_origin_to_external_browser() -> None:
    decision = decide_navigation(
        "https://docs.example/reference",
        internal_origin=ORIGIN,
        approved_external_origins=("https://docs.example",),
    )
    assert decision.action is NavigationAction.OPEN_EXTERNAL
