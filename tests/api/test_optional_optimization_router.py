from __future__ import annotations

import logging

from embedded_copilot.api import main as api_main
from embedded_copilot.services.config import Settings


class _ChatService:
    async def chat(self, request):
        raise AssertionError("chat should not be called")


def test_missing_optimization_dependency_does_not_block_app_creation(monkeypatch, caplog) -> None:
    real_import = api_main.importlib.import_module

    def missing_optimization(name: str):
        if name == "embedded_copilot.api.optimization_v26_routes":
            raise ModuleNotFoundError("optimization dependency is unavailable")
        return real_import(name)

    monkeypatch.setattr(api_main.importlib, "import_module", missing_optimization)
    with caplog.at_level(logging.WARNING, logger="embedded_copilot.api.main"):
        app = api_main.create_app(
            service=_ChatService(),
            settings=Settings(_env_file=None),
        )

    assert app is not None
    assert any(
        record.getMessage() == "optional_router_unavailable"
        and getattr(record, "router", None) == "optimization_v26"
        for record in caplog.records
    )


def test_core_copilot_routes_are_registered_when_optimization_is_unavailable(monkeypatch) -> None:
    real_import = api_main.importlib.import_module

    def missing_optimization(name: str):
        if name == "embedded_copilot.api.optimization_v26_routes":
            raise ImportError("optimization contract is unavailable")
        return real_import(name)

    monkeypatch.setattr(api_main.importlib, "import_module", missing_optimization)
    app = api_main.create_app(
        service=_ChatService(),
        settings=Settings(_env_file=None),
    )
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)
            continue
        original_router = getattr(route, "original_router", None)
        paths.update(
            nested.path
            for nested in getattr(original_router, "routes", ())
            if getattr(nested, "path", None) is not None
        )

    assert "/api/v1/copilot/projects" in paths
    assert "/api/v1/copilot/projects/{project_id}/conversations" in paths
    assert "/api/v1/copilot/chat/events" in paths
    assert "/api/optimization/v26/{project_id}" not in paths


def test_non_import_error_is_not_downgraded(monkeypatch) -> None:
    def broken_import(name: str):
        if name == "embedded_copilot.api.optimization_v26_routes":
            raise RuntimeError("optimization initialization failed")
        return __import__(name, fromlist=["*"])

    monkeypatch.setattr(api_main.importlib, "import_module", broken_import)

    try:
        api_main.create_app(service=_ChatService(), settings=Settings(_env_file=None))
    except RuntimeError as error:
        assert str(error) == "optimization initialization failed"
    else:
        raise AssertionError("non-import startup errors must propagate")
