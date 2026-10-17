from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..ports import CancellationPort


@dataclass(frozen=True)
class ProcessOutcome:
    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessRunnerPort(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        cancellation_token: str | None,
    ) -> ProcessOutcome: ...


class SubprocessRunner:
    def __init__(self, cancellation: CancellationPort | None = None) -> None:
        self._cancellation = cancellation

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        cancellation_token: str | None,
    ) -> ProcessOutcome:
        if not argv or any(type(item) is not str or not item for item in argv):
            raise ValueError("process command is invalid")
        if cancellation_token and self._is_cancelled(cancellation_token):
            raise asyncio.CancelledError
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            # Tool output is intentionally not retained.  Adapters return only
            # fixed summaries, and test runners can inject bounded outcomes.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        try:
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                if cancellation_token and self._is_cancelled(cancellation_token):
                    process.kill()
                    process.wait()
                    raise asyncio.CancelledError
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise TimeoutError
                process.wait(timeout=min(0.05, remaining))
            process.wait()
            return ProcessOutcome(process.returncode or 0, b"", b"")
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError from None

    def _is_cancelled(self, token: str) -> bool:
        return bool(self._cancellation and self._cancellation.is_cancelled(token))


__all__ = ("ProcessOutcome", "ProcessRunnerPort", "SubprocessRunner")
