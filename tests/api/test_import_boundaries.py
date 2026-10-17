from __future__ import annotations

from pathlib import Path


def test_api_layer_does_not_import_domain_agent_implementations() -> None:
    from tests.security.boundary_audit import Sources, api_domain_violations

    index = Sources(Path(__file__).resolve().parents[2] / "src")
    modules = index.modules_under("embedded_copilot.api")
    assert api_domain_violations(index, modules) == []
