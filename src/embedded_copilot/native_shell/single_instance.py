"""User-scoped Windows single-instance guard."""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlopen

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
SW_RESTORE = 9
HEALTH_PROBE_TIMEOUT_SECONDS = 0.75


def _scope() -> str:
    return hashlib.sha256(getpass.getuser().encode("utf-8", "replace")).hexdigest()[:24]


def _state_scope(identity: str | Path | None) -> str:
    """Return a non-reversible namespace suffix for an isolated state root."""

    if identity is None:
        return ""
    try:
        value = str(Path(identity).expanduser().resolve()).casefold()
    except (OSError, RuntimeError, TypeError, ValueError):
        value = str(identity).casefold()
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:24]
    return f"-state-{digest}"


@dataclass(slots=True)
class SingleInstanceGuard:
    """Hold a user-scoped mutex and distinguish healthy owners from orphans.

    The mutex remains the normal ownership boundary.  A process that owns the
    mutex but has not published a ready backend is treated as a stale owner;
    the new invocation may take over the launch lease without killing or
    closing handles owned by the old process.  A ready owner is accepted only
    when its recorded health endpoint still responds.
    """

    product_name: str = "EmbeddedCopilot"
    status_path: Path | None = None
    health_probe: Callable[[int], bool] | None = None
    _mutex: int | None = None
    _activation: int | None = None
    _stop: threading.Event | None = None
    _activation_thread: threading.Thread | None = None
    _status_owned: bool = False
    _recovered_stale: bool = False
    # Appended after the legacy positional fields to preserve compatibility
    # for callers that supplied ``status_path`` or ``health_probe`` positionally.
    state_identity: str | Path | None = None

    @property
    def mutex_name(self) -> str:
        return f"Local\\{self.product_name}-{_scope()}{_state_scope(self.state_identity)}"

    @property
    def activation_name(self) -> str:
        return f"Local\\{self.product_name}-activation-{_scope()}{_state_scope(self.state_identity)}"

    @property
    def resolved_status_path(self) -> Path:
        if self.status_path is not None:
            if self.state_identity is None:
                return self.status_path
            return self.status_path.with_name(
                f"{self.status_path.stem}{_state_scope(self.state_identity)}{self.status_path.suffix}"
            )
        return (
            Path(tempfile.gettempdir())
            / f"{self.product_name}-{_scope()}{_state_scope(self.state_identity)}-instance-status.json"
        )

    @property
    def recovered_stale(self) -> bool:
        return self._recovered_stale

    def acquire(self) -> bool:
        if os.name != "nt":
            return self._claim_status("STARTING", None)
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, self.mutex_name)
        if not handle:
            return False
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            if self._healthy_existing_instance():
                return False
            self._recovered_stale = True
            return self._claim_status("STARTING", None)
        self._mutex = int(handle)
        self._activation = int(kernel32.CreateEventW(None, False, False, self.activation_name) or 0)
        if not self._claim_status("STARTING", None):
            self.close()
            return False
        return True

    def mark_ready(self, port: int) -> bool:
        """Publish backend readiness after listener and health checks pass."""

        return self._claim_status("READY", int(port))

    def _claim_status(self, state: str, port: int | None) -> bool:
        path = self.resolved_status_path
        temporary: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.product_name}-instance-",
                suffix=".tmp",
                dir=path.parent,
                text=True,
            )
            temporary = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"pid": os.getpid(), "state": state, "port": port},
                    stream,
                    separators=(",", ":"),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._status_owned = True
            return True
        except (OSError, ValueError, TypeError):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return False

    def _read_status(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self.resolved_status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _healthy_existing_instance(self) -> bool:
        payload = self._read_status()
        if payload is None or payload.get("state") != "READY":
            return False
        pid = payload.get("pid")
        port = payload.get("port")
        if not isinstance(pid, int) or not isinstance(port, int) or pid <= 0 or port <= 0:
            return False
        if not self._pid_alive(pid):
            return False
        probe = self.health_probe or self._default_health_probe
        try:
            return bool(probe(port))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except SystemError:
            # Windows can surface an invalid/stale PID probe as SystemError
            # instead of an OSError. Treat it as stale so takeover remains
            # available without weakening the live-owner health check.
            return False
        except (ProcessLookupError, OSError):
            return False
        return True

    @staticmethod
    def _default_health_probe(port: int) -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=HEALTH_PROBE_TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
                return isinstance(payload, dict) and payload.get("status") == "ok"
        except Exception:  # noqa: BLE001
            return False

    def signal_existing(self) -> None:
        if os.name != "nt":
            return
        kernel32 = ctypes.windll.kernel32
        # Create-or-open closes the narrow race where the first process owns
        # the mutex but has not yet created its activation event/window.
        kernel32.CreateEventW.restype = ctypes.c_void_p
        handle = kernel32.CreateEventW(None, False, False, self.activation_name)
        if handle:
            kernel32.SetEvent(handle)
            kernel32.CloseHandle(handle)
        self.foreground_existing_window()

    def serve_activation(self, title: str = "Embrix") -> None:
        if self._activation is None or os.name != "nt":
            return
        self._stop = threading.Event()

        def wait_loop() -> None:
            kernel32 = ctypes.windll.kernel32
            while not self._stop.is_set():
                result = kernel32.WaitForSingleObject(self._activation, 100)
                if result == WAIT_OBJECT_0:
                    self.foreground_existing_window(title)

        self._activation_thread = threading.Thread(target=wait_loop, name="embedded-copilot-activation", daemon=True)
        self._activation_thread.start()

    @staticmethod
    def foreground_existing_window(title: str = "Embrix") -> None:
        if os.name != "nt":
            return
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)

    def close(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._activation_thread is not None:
            self._activation_thread.join(timeout=1)
        if self._status_owned:
            try:
                payload = self._read_status()
                if payload is not None and payload.get("pid") == os.getpid():
                    self.resolved_status_path.unlink(missing_ok=True)
            except OSError:
                pass
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            for handle in (self._activation, self._mutex):
                if handle:
                    kernel32.CloseHandle(handle)
        self._activation = None
        self._mutex = None
        self._status_owned = False
