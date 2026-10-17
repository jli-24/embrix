from __future__ import annotations

import ctypes
import logging
from pathlib import Path
from types import SimpleNamespace

from embedded_copilot.native_shell import (
    NativeProjectRootBridge,
    NativeProjectRootPicker,
    NativeProjectRootSelection,
    NativeProjectRootSelectionStatus,
)


def test_picker_reports_unavailable_outside_windows(monkeypatch) -> None:
    monkeypatch.setattr("embedded_copilot.native_shell.picker.os.name", "posix")
    selection = NativeProjectRootPicker().select_project_root()
    # The offline test host is not a Windows WebView2 host.
    assert selection.status in {
        NativeProjectRootSelectionStatus.UNAVAILABLE,
        NativeProjectRootSelectionStatus.FAILED,
    }
    assert selection.reason_code == "PICKER_COM_INIT_FAILED"


def test_bridge_returns_typed_payload_and_does_not_log_candidate() -> None:
    class FakePicker:
        def select_project_root(self):
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.SELECTED, r"D:\Projects")

    payload = NativeProjectRootBridge(FakePicker()).select_project_root()
    assert payload == {"status": "SELECTED", "path": r"D:\Projects", "reason_code": None}


def test_cancel_payload_has_no_path() -> None:
    class FakePicker:
        def select_project_root(self):
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.CANCELLED, r"D:\ShouldNotEscape")

    assert NativeProjectRootBridge(FakePicker()).select_project_root() == {
        "status": "CANCELLED",
        "path": None,
        "reason_code": None,
    }


def test_native_picker_owner_provider_reads_only_native_handle() -> None:
    from embedded_copilot.native_shell.host import _window_hwnd

    class Handle:
        def ToInt64(self):
            return 1234

    native = type("Native", (), {"Handle": Handle()})()
    window = type("Window", (), {"native": native})()

    assert _window_hwnd(window) == 1234
    assert _window_hwnd(object()) == 0


def test_owner_resolution_requires_current_process_top_level_window(monkeypatch) -> None:
    from embedded_copilot.native_shell import picker

    class User32:
        class Api:
            def __init__(self, function):
                self.function = function
                self.argtypes = None
                self.restype = None

            def __call__(self, *args):
                return self.function(*args)

        def __init__(self, process_id: int, root_hwnd: int) -> None:
            self.process_id = process_id
            self._root_hwnd = root_hwnd
            self.IsWindow = self.Api(lambda _hwnd: 1)
            self.GetWindowThreadProcessId = self.Api(self._get_process_id)
            self.GetAncestor = self.Api(lambda _hwnd, _flags: self._root_hwnd)

        def _get_process_id(self, _hwnd, process_id):
            process_id._obj.value = self.process_id
            return 1

    class Kernel32:
        def __init__(self):
            self.GetCurrentProcessId = User32.Api(lambda: 100)

    user32 = User32(100, 4321)
    monkeypatch.setattr(picker.ctypes, "windll", SimpleNamespace(user32=user32, kernel32=Kernel32()))
    native_picker = NativeProjectRootPicker(owner_hwnd_provider=lambda: 4321)
    assert native_picker._resolve_owner() == (4321, True, True)

    user32.process_id = 101
    assert native_picker._resolve_owner() == (0, True, False)

    user32.process_id = 100
    user32._root_hwnd = 9999
    assert native_picker._resolve_owner() == (0, True, True)


def test_picker_diagnostic_is_safe_and_contains_runtime_context(caplog) -> None:
    from embedded_copilot.native_shell.picker import _log_picker_failure, _PickerTrace

    trace = _PickerTrace.create()
    trace.owner_present = True
    trace.owner_pid_match = True
    trace.apartment_type = "STA"
    with caplog.at_level(logging.WARNING, logger="embedded_copilot.native_launcher"):
        _log_picker_failure("PICKER_SHOW_FAILED", error=RuntimeError(), hresult=0x80004005, stage="DIALOG_SHOW", trace=trace)
    record = caplog.records[-1]
    assert record.reason_code == "PICKER_SHOW_FAILED"
    assert record.stage == "DIALOG_SHOW"
    assert record.exception_type == "RuntimeError"
    assert record.hresult == "0x80004005"
    assert record.thread_id == str(trace.thread_id)
    assert record.thread_name == trace.thread_name
    assert record.apartment_state == "STA"
    assert record.apartment_type == "STA"
    assert record.requested_apartment == "STA"
    assert record.owner_present is True
    assert record.owner_pid_match is True
    assert record.picker_backend == "windows-iFileOpenDialog-ctypes"
    assert "C:\\" not in record.getMessage()


def test_picker_options_and_failure_codes_are_explicit() -> None:
    picker = (Path(__file__).resolve().parents[2] / "src" / "embedded_copilot" / "native_shell" / "picker.py").read_text(encoding="utf-8")
    assert "_FOS_PICKFOLDERS" in picker
    assert "_FOS_FORCEFILESYSTEM" in picker
    assert "_FOS_PATHMUSTEXIST" in picker
    assert "_I_FILE_DIALOG_GET_RESULT_SLOT = 20" in picker
    assert "ctypes.cast(display_name, ctypes.c_void_p)" in picker
    for code in ("PICKER_COM_INIT_FAILED", "PICKER_CREATE_FAILED", "PICKER_OWNER_UNAVAILABLE", "PICKER_SHOW_FAILED", "PICKER_RESULT_FAILED", "PICKER_CANCELLED"):
        assert code in picker
    assert "FindWindowW" not in picker


def test_cancel_hresult_is_normalized_from_signed_ctypes_hresult() -> None:
    from embedded_copilot.native_shell.picker import _ERROR_CANCELLED, _hresult_code

    signed_cancel = ctypes.c_long(_ERROR_CANCELLED).value
    assert signed_cancel < 0
    assert _hresult_code(signed_cancel) == _ERROR_CANCELLED
    assert _hresult_code(ctypes.c_long(0).value) == 0


def test_picker_stage_logging_does_not_contain_candidate_path(caplog) -> None:
    from embedded_copilot.native_shell.picker import _log_picker_stage, _PickerTrace

    trace = _PickerTrace.create()
    with caplog.at_level(logging.INFO, logger="embedded_copilot.native_launcher"):
        _log_picker_stage(trace, "RESULT_GET")
    record = caplog.records[-1]
    assert record.stage == "RESULT_GET"
    assert record.trace_id == trace.trace_id
    assert not hasattr(record, "path")
    assert "D:\\Projects" not in record.getMessage()


def test_com_abi_slots_are_stable() -> None:
    from embedded_copilot.native_shell import picker

    assert picker._I_FILE_DIALOG_SHOW_SLOT == 3
    assert picker._I_FILE_DIALOG_SET_OPTIONS_SLOT == 9
    assert picker._I_FILE_DIALOG_GET_OPTIONS_SLOT == 10
    assert picker._I_FILE_DIALOG_GET_RESULT_SLOT == 20
    assert picker._I_SHELL_ITEM_GET_DISPLAY_NAME_SLOT == 5


def test_probe_is_not_part_of_typed_bridge() -> None:
    from embedded_copilot.native_shell import picker

    assert "probe_picker_runtime" not in NativeProjectRootBridge.__dict__
    assert callable(picker.probe_picker_runtime)


class _FakeWinApi:
    def __init__(self, function):
        self._function = function
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._function(*args)


class _FakeOle32:
    def __init__(self, init_hr: int, apartment: int | None = None, probe_hr: int = 0, create_hr: int = 0x80004005):
        self.uninitialize_calls = 0
        self.CoInitializeEx = _FakeWinApi(lambda _reserved, _flags: init_hr)
        if apartment is not None:
            def probe(apt, qualifier):
                apt._obj.value = apartment
                qualifier._obj.value = 0
                return probe_hr

            self.CoGetApartmentType = _FakeWinApi(probe)
        self.CoCreateInstance = _FakeWinApi(lambda *_args: create_hr)
        self.CoUninitialize = _FakeWinApi(self._uninitialize)

    def _uninitialize(self):
        self.uninitialize_calls += 1


def test_com_init_s_ok_and_unknown_probe_continue_to_dialog_create(caplog) -> None:
    from embedded_copilot.native_shell.picker import _initialize_com_sta, _PickerTrace

    trace = _PickerTrace.create()
    with caplog.at_level(logging.INFO, logger="embedded_copilot.native_launcher"):
        success, initialized = _initialize_com_sta(_FakeOle32(0), trace)
    assert (success, initialized) == (True, True)
    assert trace.raw_hresult == "0"
    assert trace.normalized_hresult == "0x00000000"
    assert trace.com_init_status == "SUCCESS"
    assert trace.requested_apartment == "STA"
    assert trace.observed_apartment == "UNKNOWN"
    assert trace.apartment_probe_status == "UNAVAILABLE"
    assert not any(record.reason_code == "PICKER_COM_INIT_FAILED" for record in caplog.records)


def test_com_init_s_false_is_success_and_requires_cleanup() -> None:
    from embedded_copilot.native_shell.picker import _initialize_com_sta, _PickerTrace

    ole32 = _FakeOle32(1)
    success, initialized = _initialize_com_sta(ole32, _PickerTrace.create())
    assert (success, initialized) == (True, True)
    ole32.CoUninitialize()
    assert ole32.uninitialize_calls == 1


def test_com_init_failed_hresult_does_not_uninitialize() -> None:
    from embedded_copilot.native_shell.picker import _initialize_com_sta, _PickerTrace

    ole32 = _FakeOle32(0x80010106)
    success, initialized = _initialize_com_sta(ole32, _PickerTrace.create())
    assert (success, initialized) == (False, False)
    assert ole32.uninitialize_calls == 0


def test_com_init_explicit_non_sta_fails_closed_without_reclassifying_s_ok() -> None:
    from embedded_copilot.native_shell.picker import _initialize_com_sta, _PickerTrace

    trace = _PickerTrace.create()
    ole32 = _FakeOle32(0, apartment=2)
    success, initialized = _initialize_com_sta(ole32, trace)
    assert (success, initialized) == (False, True)
    assert trace.com_init_status == "SUCCESS"
    assert trace.observed_apartment == "MTA"
    assert trace.normalized_hresult == "0x00000000"
    ole32.CoUninitialize()
    assert ole32.uninitialize_calls == 1


def test_select_windows_folder_balances_uninitialize_for_s_ok_and_s_false(monkeypatch) -> None:
    from embedded_copilot.native_shell import picker

    for init_hr in (0, 1):
        ole32 = _FakeOle32(init_hr)
        monkeypatch.setattr(picker.ctypes, "windll", SimpleNamespace(ole32=ole32))
        selection = picker._select_windows_folder(owner_hwnd=123, trace=picker._PickerTrace.create())
        assert selection.reason_code == "PICKER_CREATE_FAILED"
        assert ole32.uninitialize_calls == 1
