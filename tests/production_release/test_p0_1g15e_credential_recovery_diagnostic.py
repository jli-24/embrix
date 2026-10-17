from __future__ import annotations

import ctypes
import json
import logging
from pathlib import Path

import pytest

from embedded_copilot.production_release import credential_recovery_diagnostic as diagnostic
from embedded_copilot.production_release import credentials
from embedded_copilot.production_release.credentials import (
    CHAT_PROVIDER_CREDENTIAL_PURPOSE,
    PROVIDER_BINDING_INTEGRITY_PURPOSE,
    CredentialUnavailable,
    InMemoryCredentialProvider,
    WindowsCredentialManager,
)
from embedded_copilot.production_release.provider import ProviderConfigurationService


class _FakeAdvapi:
    def __init__(self, *, outcome: str, error_code: int = 0) -> None:
        self.outcome = outcome
        self.error_code = error_code
        self.calls: list[str] = []
        self.free_calls = 0
        self._blob = None
        self._record = None

    def CredReadW(self, target, _credential_type, _flags, pointer):  # noqa: N802
        self.calls.append(target)
        if self.outcome != "PRESENT":
            return 0
        value = b"TEST_CANARY_API_KEY_DO_NOT_LEAK"
        self._blob = (ctypes.c_byte * len(value)).from_buffer_copy(value)
        self._record = credentials._Credential()
        self._record.CredentialBlobSize = len(value)
        self._record.CredentialBlob = ctypes.cast(self._blob, ctypes.POINTER(ctypes.c_byte))
        ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(credentials._Credential)))[0] = ctypes.pointer(
            self._record
        )
        return 1

    def CredFree(self, _pointer):  # noqa: N802
        self.free_calls += 1


def _manager(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: str,
    error_code: int = 0,
) -> tuple[WindowsCredentialManager, _FakeAdvapi]:
    fake = _FakeAdvapi(outcome=outcome, error_code=error_code)
    manager = object.__new__(WindowsCredentialManager)
    manager._advapi = fake  # type: ignore[attr-defined]
    monkeypatch.setattr(credentials.ctypes, "get_last_error", lambda: error_code, raising=False)
    return manager, fake


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _configure_sink(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: path)


def test_recovery_diagnostic_is_disabled_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "recovery.jsonl"
    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    manager, fake = _manager(monkeypatch, outcome="NOT_FOUND", error_code=1168)

    assert manager.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) is None
    assert len(fake.calls) == 1
    assert not output.exists()


@pytest.mark.parametrize(
    ("purpose", "target_kind", "stage_prefix"),
    [
        (CHAT_PROVIDER_CREDENTIAL_PURPOSE, "PRIMARY", "PRIMARY_CREDENTIAL_LOOKUP"),
        (PROVIDER_BINDING_INTEGRITY_PURPOSE, "INTEGRITY", "INTEGRITY_TARGET_LOOKUP"),
    ],
)
@pytest.mark.parametrize(
    ("error_code", "result", "stage_suffix"),
    [
        (1168, "NOT_FOUND", "NOT_FOUND"),
        (5, "ACCESS_DENIED", "ACCESS_DENIED"),
        (1234, "ERROR", "ERROR"),
    ],
)
def test_lookup_preserves_native_result_and_emits_safe_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
    target_kind: str,
    stage_prefix: str,
    error_code: int,
    result: str,
    stage_suffix: str,
) -> None:
    output = tmp_path / "recovery.jsonl"
    _configure_sink(monkeypatch, output)
    manager, fake = _manager(monkeypatch, outcome="ERROR", error_code=error_code)
    stage = f"{stage_prefix}_{stage_suffix}"

    if error_code == 1168:
        assert manager.get(purpose) is None
    else:
        with pytest.raises(CredentialUnavailable) as raised:
            manager.get(purpose)
        assert raised.value.win32_error_code == error_code  # type: ignore[attr-defined]

    expected_target = (
        "EmbeddedCopilot/v7/CHAT_PROVIDER_API_KEY:deepseek"
        if target_kind == "PRIMARY"
        else "EmbeddedCopilot/v7/PROVIDER_BINDING_INTEGRITY:deepseek"
    )
    assert fake.calls == [expected_target]
    rows = _events(output)
    assert [row["stage"] for row in rows] == [f"{stage_prefix}_BEGIN", stage]
    assert rows[-1]["result"] == result
    assert rows[-1]["target_kind"] == target_kind
    assert rows[-1]["safe_win32_error_code"] == error_code
    assert set(rows[-1]) <= diagnostic.ALLOWED_EVENT_FIELDS


@pytest.mark.parametrize(
    ("purpose", "target_kind", "stage"),
    [
        (
            CHAT_PROVIDER_CREDENTIAL_PURPOSE,
            "PRIMARY",
            "PRIMARY_CREDENTIAL_LOOKUP_PRESENT",
        ),
        (
            PROVIDER_BINDING_INTEGRITY_PURPOSE,
            "INTEGRITY",
            "INTEGRITY_TARGET_LOOKUP_PRESENT",
        ),
    ],
)
def test_present_lookup_is_observed_without_logging_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    purpose: str,
    target_kind: str,
    stage: str,
) -> None:
    output = tmp_path / "recovery.jsonl"
    _configure_sink(monkeypatch, output)
    manager, fake = _manager(monkeypatch, outcome="PRESENT")

    assert manager.get(purpose) == b"TEST_CANARY_API_KEY_DO_NOT_LEAK"
    assert len(fake.calls) == 1
    assert fake.free_calls == 1
    rows = _events(output)
    begin_stage = (
        "PRIMARY_CREDENTIAL_LOOKUP_BEGIN"
        if target_kind == "PRIMARY"
        else "INTEGRITY_TARGET_LOOKUP_BEGIN"
    )
    assert [row["stage"] for row in rows] == [begin_stage, stage]
    content = output.read_text(encoding="utf-8")
    assert "TEST_CANARY_API_KEY_DO_NOT_LEAK" not in content
    assert all(set(row) <= diagnostic.ALLOWED_EVENT_FIELDS for row in rows)


def test_diagnostic_does_not_add_a_native_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    disabled_manager, disabled_fake = _manager(monkeypatch, outcome="PRESENT")
    disabled_manager.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE)

    output = tmp_path / "recovery.jsonl"
    _configure_sink(monkeypatch, output)
    enabled_manager, enabled_fake = _manager(monkeypatch, outcome="PRESENT")
    enabled_manager.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE)

    assert len(disabled_fake.calls) == 1
    assert len(enabled_fake.calls) == 1


def test_integrity_recovery_and_projection_events_are_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "recovery.jsonl"
    _configure_sink(monkeypatch, output)
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=InMemoryCredentialProvider(),
    )

    projection = service.projection()

    assert projection.credential_status == "NOT_CONFIGURED"
    stages = [row["stage"] for row in _events(output)]
    assert "CREDENTIAL_RECOVERY_PRIMARY_ABSENT" in stages
    assert "CREDENTIAL_RECOVERY_PROJECTION_UNCONFIGURED" in stages
    assert all(set(row) <= diagnostic.ALLOWED_EVENT_FIELDS for row in _events(output))


class _FailingStream:
    def __enter__(self) -> _FailingStream:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def write(self, _value: str) -> int:
        raise OSError("TEST_CANARY_write_failure")

    def flush(self) -> None:
        raise OSError("TEST_CANARY_flush_failure")


class _FailingPath:
    def __init__(self, mode: str) -> None:
        self.parent = self
        self.mode = mode

    def mkdir(self, **_kwargs: object) -> None:
        if self.mode in {"invalid-directory", "mkdir"}:
            raise OSError("TEST_CANARY_directory_failure")

    def open(self, **_kwargs: object) -> _FailingStream:
        if self.mode == "open":
            raise OSError("TEST_CANARY_open_failure")
        return _FailingStream()


@pytest.mark.parametrize("mode", ["invalid-directory", "open", "write"])
@pytest.mark.parametrize("error_code", [1168, 5])
def test_sink_failures_are_non_blocking_and_preserve_lookup_behavior(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    error_code: int,
) -> None:
    _configure_sink(monkeypatch, tmp_path / "unused.jsonl")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: _FailingPath(mode))
    manager, fake = _manager(monkeypatch, outcome="ERROR", error_code=error_code)

    if error_code == 1168:
        assert manager.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) is None
    else:
        with pytest.raises(CredentialUnavailable):
            manager.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE)
    assert len(fake.calls) == 1


def test_provider_recovery_is_unchanged_when_sink_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_sink(monkeypatch, tmp_path / "unused.jsonl")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: _FailingPath("open"))
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=InMemoryCredentialProvider(),
    )

    assert service.projection().credential_status == "NOT_CONFIGURED"


def test_schema_rejects_unknown_stage_and_never_emits_canary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "recovery.jsonl"
    _configure_sink(monkeypatch, output)

    diagnostic.emit("UNKNOWN_STAGE_TEST_CANARY", result="ERROR")
    diagnostic.emit(
        "PRIMARY_CREDENTIAL_LOOKUP_ERROR",
        result="ERROR",
        target_kind="PRIMARY",
        error=CredentialUnavailable("TEST_CANARY_INTEGRITY_SECRET_DO_NOT_LEAK"),
        safe_code=5,
    )

    rows = _events(output)
    assert len(rows) == 1
    assert rows[0]["safe_error_class"] == "CREDENTIAL_STORE_UNAVAILABLE"
    assert rows[0]["safe_win32_error_code"] == 5
    content = output.read_text(encoding="utf-8")
    assert "TEST_CANARY" not in content
    assert set(rows[0]) <= diagnostic.ALLOWED_EVENT_FIELDS


def test_secret_canary_is_absent_from_file_stdout_stderr_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = tmp_path / "recovery.jsonl"
    _configure_sink(monkeypatch, output)
    manager, _ = _manager(monkeypatch, outcome="PRESENT")

    with caplog.at_level(logging.DEBUG):
        manager.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE)

    captured = capsys.readouterr()
    content = output.read_text(encoding="utf-8")
    assert "TEST_CANARY_API_KEY_DO_NOT_LEAK" not in content
    assert "TEST_CANARY_API_KEY_DO_NOT_LEAK" not in captured.out
    assert "TEST_CANARY_API_KEY_DO_NOT_LEAK" not in captured.err
    assert "TEST_CANARY_API_KEY_DO_NOT_LEAK" not in caplog.text
