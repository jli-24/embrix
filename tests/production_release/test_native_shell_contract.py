from __future__ import annotations

from embedded_copilot.native_shell import NativeDesktopHostPort, NativeShellConfig


def test_native_host_contract_is_presentation_only() -> None:
    config = NativeShellConfig()
    assert config.title == "Embrix"
    assert (config.width, config.height) == (1280, 800)
    assert config.approved_external_origins == ()
    assert NativeDesktopHostPort is not None
