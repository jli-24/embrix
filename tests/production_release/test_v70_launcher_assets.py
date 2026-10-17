from __future__ import annotations

import importlib.util
import json
import socket
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest
import uvicorn
from fastapi import FastAPI


def _launcher_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "v70_desktop_launcher.py"
    spec = importlib.util.spec_from_file_location("v70_desktop_launcher_asset_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_mode_resolves_frontend_dist(tmp_path: Path) -> None:
    source_root = tmp_path / "repo"
    assets = source_root / "frontend" / "dist"
    assets.mkdir(parents=True)

    resolved = _launcher_module()._resolve_assets(bundled_root=None, source_root=source_root)

    assert resolved == assets


def test_source_mode_missing_frontend_dist_fails_closed(tmp_path: Path) -> None:
    source_root = tmp_path / "repo"
    (source_root / "frontend").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="STATIC_ASSET_UNAVAILABLE"):
        _launcher_module()._resolve_assets(bundled_root=None, source_root=source_root)


def test_packaged_mode_resolves_bundled_frontend_dist(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    assets = bundle_root / "frontend-dist"
    assets.mkdir(parents=True)

    resolved = _launcher_module()._resolve_assets(bundled_root=bundle_root, source_root=tmp_path / "repo")

    assert resolved == assets


def test_packaged_mode_missing_bundled_assets_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="STATIC_ASSET_UNAVAILABLE"):
        _launcher_module()._resolve_assets(bundled_root=tmp_path / "bundle", source_root=tmp_path / "repo")


def test_source_mode_does_not_use_stale_frontend_dist_or_vite_fallback(tmp_path: Path) -> None:
    source_root = tmp_path / "repo"
    (source_root / "frontend-dist").mkdir(parents=True)
    (source_root / "frontend" / "vite.config.ts").parent.mkdir(parents=True)
    (source_root / "frontend" / "vite.config.ts").write_text("export default {}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="STATIC_ASSET_UNAVAILABLE"):
        _launcher_module()._resolve_assets(bundled_root=None, source_root=source_root)


def test_server_worker_is_non_daemon_and_stops_only_after_explicit_shutdown() -> None:
    class _Server:
        def __init__(self) -> None:
            self.started = False
            self.should_exit = False
            self.run_called = threading.Event()

        def run(self, *, sockets: list[object]) -> None:
            assert sockets == ["listener"]
            self.started = True
            self.run_called.set()
            while not self.should_exit:
                threading.Event().wait(0.01)

    launcher = _launcher_module()
    server = _Server()

    worker = launcher._start_server_worker(server, listener="listener")

    assert worker.daemon is False
    assert launcher._wait_for_server_startup(server, worker, timeout_seconds=1) is True
    assert worker.is_alive() is True
    launcher._request_graceful_shutdown(server, worker)
    assert worker.is_alive() is False


def test_uvicorn_worker_keeps_loopback_endpoint_alive_until_launcher_shutdown() -> None:
    application = FastAPI()

    @application.get("/desktop/")
    def desktop() -> dict[str, str]:
        return {"status": "ready"}

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning"))
    launcher = _launcher_module()
    worker = launcher._start_server_worker(server, listener=listener)
    try:
        assert launcher._wait_for_server_startup(server, worker, timeout_seconds=5) is True
        with urlopen(f"http://127.0.0.1:{port}/desktop/", timeout=5) as response:
            assert response.status == 200
            assert response.read() == b'{"status":"ready"}'
    finally:
        launcher._request_graceful_shutdown(server, worker)
        listener.close()

    assert worker.is_alive() is False


@pytest.mark.parametrize("status", ["ok", "degraded", "unavailable"])
def test_health_probe_accepts_every_valid_process_readiness_status(
    monkeypatch, status: str
) -> None:
    launcher = _launcher_module()

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self) -> bytes:
            return json.dumps({"status": status}).encode("utf-8")

    monkeypatch.setattr(launcher, "urlopen", lambda *args, **kwargs: _Response())

    assert launcher._health_endpoint_ready(43123) is True
