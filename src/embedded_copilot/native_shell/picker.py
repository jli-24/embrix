"""Presentation-only Windows folder picker boundary.

The picker returns an untrusted candidate path.  Registration, canonicalization,
allow-list checks, permissions, identity and fingerprint validation remain in
the Project Catalog API; this module never grants filesystem capability.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

logger = logging.getLogger("embedded_copilot.native_launcher")
_BACKEND_ID = "windows-iFileOpenDialog-ctypes"
_ERROR_CANCELLED = 0x800704C7
_COINIT_APARTMENTTHREADED = 0x2
_FOS_PICKFOLDERS = 0x20
_FOS_FORCEFILESYSTEM = 0x40
_FOS_PATHMUSTEXIST = 0x800
_CLSCTX_INPROC_SERVER = 0x1
_SIGDN_FILESYSPATH = 0x80058000
_I_FILE_DIALOG_SHOW_SLOT = 3
_I_FILE_DIALOG_SET_OPTIONS_SLOT = 9
_I_FILE_DIALOG_GET_OPTIONS_SLOT = 10
_I_FILE_DIALOG_GET_RESULT_SLOT = 20
_I_SHELL_ITEM_GET_DISPLAY_NAME_SLOT = 5
_I_UNKNOWN_RELEASE_SLOT = 2
_RPC_E_CHANGED_MODE = 0x80010106


class NativeProjectRootSelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    CANCELLED = "CANCELLED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class NativeProjectRootSelection:
    status: NativeProjectRootSelectionStatus
    path: str | None = None
    reason_code: str | None = None

    def as_js_payload(self) -> dict[str, str | None]:
        return {
            "status": self.status.value,
            "path": self.path if self.status is NativeProjectRootSelectionStatus.SELECTED else None,
            "reason_code": self.reason_code,
        }


@dataclass(slots=True)
class _PickerTrace:
    trace_id: str
    started_at: float
    callback_thread_id: int
    callback_thread_name: str
    worker_thread_id: int | None = None
    worker_thread_name: str = "-"
    stage: str = "-"
    apartment_type: str = "UNKNOWN"
    owner_present: bool = False
    owner_pid_match: bool = False
    requested_apartment: str = "STA"
    com_init_status: str = "-"
    raw_hresult: str = "-"
    normalized_hresult: str = "-"
    observed_apartment: str = "UNKNOWN"
    apartment_probe_status: str = "UNAVAILABLE"

    @classmethod
    def create(cls) -> _PickerTrace:
        current = threading.current_thread()
        return cls(
            trace_id=uuid4().hex,
            started_at=time.monotonic(),
            callback_thread_id=threading.get_ident(),
            callback_thread_name=current.name,
        )

    @property
    def duration_ms(self) -> int:
        return max(0, int((time.monotonic() - self.started_at) * 1000))

    @property
    def thread_id(self) -> int:
        return self.worker_thread_id or self.callback_thread_id

    @property
    def thread_name(self) -> str:
        return self.worker_thread_name if self.worker_thread_id else self.callback_thread_name


class NativeProjectRootPickerPort(Protocol):
    def select_project_root(self) -> NativeProjectRootSelection:
        """Return one untrusted candidate path or a fixed safe status."""


class NativeProjectRootPicker:
    """IFileOpenDialog adapter; the COM object never escapes this module."""

    def __init__(self, *, owner_hwnd_provider: Callable[[], int] | None = None) -> None:
        self._owner_hwnd_provider = owner_hwnd_provider

    def set_owner_hwnd_provider(self, provider: Callable[[], int]) -> None:
        """Bind the already-created pywebview owner without exposing it to JS."""

        self._owner_hwnd_provider = provider

    def _owner_hwnd(self) -> int:
        return self._resolve_owner()[0]

    def _resolve_owner(self) -> tuple[int, bool, bool]:
        if self._owner_hwnd_provider is None:
            return 0, False, False
        try:
            value = int(self._owner_hwnd_provider() or 0)
            if not value:
                return 0, False, False
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            user32.IsWindow.argtypes = (ctypes.c_void_p,)
            user32.IsWindow.restype = ctypes.c_int
            user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
            user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            user32.GetAncestor.restype = ctypes.c_void_p
            kernel32.GetCurrentProcessId.restype = ctypes.c_ulong
            process_id = ctypes.c_ulong()
            if not user32.IsWindow(ctypes.c_void_p(value)):
                return 0, False, False
            if not user32.GetWindowThreadProcessId(ctypes.c_void_p(value), ctypes.byref(process_id)):
                return 0, True, False
            if process_id.value != kernel32.GetCurrentProcessId():
                return 0, True, False
            root_hwnd = int(user32.GetAncestor(ctypes.c_void_p(value), 2) or 0)
            if root_hwnd != value:
                return 0, True, True
            return value, True, True
        except Exception:  # noqa: BLE001 - owner is optional diagnostic context
            return 0, False, False

    def select_project_root(self) -> NativeProjectRootSelection:
        trace = _PickerTrace.create()
        _log_picker_stage(trace, "BRIDGE_RECEIVED")
        if os.name != "nt":
            selection = NativeProjectRootSelection(NativeProjectRootSelectionStatus.UNAVAILABLE, reason_code="PICKER_COM_INIT_FAILED")
            _log_picker_stage(trace, "RETURN_RESULT", status=selection.status.value)
            return selection
        result: list[NativeProjectRootSelection] = []

        def run_sta() -> None:
            trace.worker_thread_id = threading.get_ident()
            trace.worker_thread_name = threading.current_thread().name
            _log_picker_stage(trace, "STA_THREAD_START")
            owner_hwnd, owner_present, owner_pid_match = self._resolve_owner()
            trace.owner_present = owner_present
            trace.owner_pid_match = owner_pid_match
            _log_picker_stage(trace, "OWNER_RESOLVE")
            if not owner_hwnd:
                _log_picker_failure(
                    "PICKER_OWNER_UNAVAILABLE",
                    stage="OWNER_RESOLVE",
                    trace=trace,
                )
                result.append(
                    NativeProjectRootSelection(
                        NativeProjectRootSelectionStatus.UNAVAILABLE,
                        reason_code="PICKER_OWNER_UNAVAILABLE",
                    )
                )
                return
            result.append(_select_windows_folder(owner_hwnd=owner_hwnd, trace=trace))

        # pywebview executes JS API methods on an HTTP worker thread.  COM
        # common dialogs must be created on a dedicated initialized STA thread.
        worker = threading.Thread(target=run_sta, name="embedded-copilot-folder-picker-sta", daemon=False)
        worker.start()
        worker.join(timeout=120.0)
        if worker.is_alive():
            _log_picker_failure("PICKER_SHOW_FAILED", error=TimeoutError(), stage="RETURN_RESULT", trace=trace)
            selection = NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_SHOW_FAILED")
            _log_picker_stage(trace, "RETURN_RESULT", status=selection.status.value)
            return selection
        selected = result[0] if result else NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")
        if selected.status is not NativeProjectRootSelectionStatus.SELECTED:
            _log_picker_stage(trace, "RETURN_RESULT", status=selected.status.value)
            return selected
        try:
            if not selected.path:
                selection = NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")
                _log_picker_stage(trace, "RETURN_RESULT", status=selection.status.value)
                return selection
            candidate = Path(selected.path)
            if candidate.is_symlink() or not candidate.is_dir():
                selection = NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")
                _log_picker_stage(trace, "RETURN_RESULT", status=selection.status.value)
                return selection
            canonical = candidate.resolve(strict=True)
            if not canonical.is_dir() or not os.access(canonical, os.R_OK | os.X_OK | os.W_OK):
                selection = NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")
                _log_picker_stage(trace, "RETURN_RESULT", status=selection.status.value)
                return selection
            selection = NativeProjectRootSelection(NativeProjectRootSelectionStatus.SELECTED, path=str(canonical))
            _log_picker_stage(trace, "RETURN_RESULT", status=selection.status.value)
            return selection
        except Exception as error:  # noqa: BLE001 - map provider details to a fixed status
            _log_picker_failure("PICKER_RESULT_FAILED", error=error, stage="PATH_EXTRACT", trace=trace)
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")


class NativeProjectRootBridge:
    """The complete JS bridge surface: exactly one typed capability."""

    def __init__(self, picker: NativeProjectRootPickerPort) -> None:
        self._picker = picker

    def select_project_root(self) -> dict[str, str | None]:
        return self._picker.select_project_root().as_js_payload()


def _select_windows_folder(*, owner_hwnd: int = 0, trace: _PickerTrace | None = None) -> NativeProjectRootSelection:
    """Use the Windows common dialog without exposing shell/filesystem APIs."""

    trace = trace or _PickerTrace.create()
    trace.worker_thread_id = threading.get_ident()
    trace.worker_thread_name = threading.current_thread().name
    _log_picker_stage(trace, "COM_INITIALIZE")
    try:
        ole32 = ctypes.windll.ole32
        com_success, initialized = _initialize_com_sta(ole32, trace)
    except Exception as error:  # noqa: BLE001 - fixed capability failure
        _log_picker_failure("PICKER_COM_INIT_FAILED", error=error, stage="COM_INITIALIZE", trace=trace)
        return NativeProjectRootSelection(NativeProjectRootSelectionStatus.UNAVAILABLE, reason_code="PICKER_COM_INIT_FAILED")
    if not com_success:
        return NativeProjectRootSelection(NativeProjectRootSelectionStatus.UNAVAILABLE, reason_code="PICKER_COM_INIT_FAILED")

    dialog = ctypes.c_void_p()
    result = None
    try:
        _log_picker_stage(trace, "DIALOG_CREATE")
        clsid = ctypes.byref(_guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7"))
        iid = ctypes.byref(_guid("D57C7288-D4AD-4768-BE02-9D969532D960"))
        ole32.CoCreateInstance.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        ole32.CoCreateInstance.restype = ctypes.c_long
        hr = ole32.CoCreateInstance(clsid, None, _CLSCTX_INPROC_SERVER, iid, ctypes.byref(dialog))
        if _hresult_code(hr) != 0 or not dialog.value:
            _log_picker_failure("PICKER_CREATE_FAILED", hresult=hr, stage="DIALOG_CREATE", trace=trace)
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.UNAVAILABLE, reason_code="PICKER_CREATE_FAILED")

        set_options = _com_method(dialog, _I_FILE_DIALOG_SET_OPTIONS_SLOT, ctypes.c_long, ctypes.c_uint)
        get_options = _com_method(dialog, _I_FILE_DIALOG_GET_OPTIONS_SLOT, ctypes.c_long, ctypes.POINTER(ctypes.c_uint))
        show = _com_method(dialog, _I_FILE_DIALOG_SHOW_SLOT, ctypes.c_long, ctypes.c_void_p)
        options = ctypes.c_uint()
        _log_picker_stage(trace, "OPTIONS_READ")
        options_hr = get_options(dialog, ctypes.byref(options))
        if _hresult_code(options_hr) != 0:
            _log_picker_failure("PICKER_CREATE_FAILED", hresult=options_hr, stage="OPTIONS_READ", trace=trace)
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_CREATE_FAILED")
        _log_picker_stage(trace, "OPTIONS_SET")
        option_hr = set_options(dialog, options.value | _FOS_PICKFOLDERS | _FOS_FORCEFILESYSTEM | _FOS_PATHMUSTEXIST)
        if _hresult_code(option_hr) != 0:
            _log_picker_failure("PICKER_CREATE_FAILED", hresult=option_hr, stage="OPTIONS_SET", trace=trace)
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_CREATE_FAILED")
        _log_picker_stage(trace, "DIALOG_SHOW")
        hr = show(dialog, ctypes.c_void_p(owner_hwnd))
        normalized_hr = _hresult_code(hr)
        if normalized_hr == _ERROR_CANCELLED:  # HRESULT_FROM_WIN32(ERROR_CANCELLED), signed or unsigned
            _log_picker_failure("PICKER_CANCELLED", hresult=hr, stage="DIALOG_SHOW", trace=trace)
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.CANCELLED, reason_code="PICKER_CANCELLED")
        if normalized_hr != 0:
            _log_picker_failure("PICKER_SHOW_FAILED", hresult=hr, stage="DIALOG_SHOW", trace=trace)
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_SHOW_FAILED")

        _log_picker_stage(trace, "RESULT_GET")
        get_result = _com_method(dialog, _I_FILE_DIALOG_GET_RESULT_SLOT, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
        shell_item = ctypes.c_void_p()
        result_hr = get_result(dialog, ctypes.byref(shell_item))
        if _hresult_code(result_hr) != 0 or not shell_item.value:
            _log_picker_failure("PICKER_RESULT_FAILED", hresult=result_hr, stage="RESULT_GET", trace=trace)
            return NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")
        try:
            _log_picker_stage(trace, "PATH_EXTRACT")
            get_display_name = _com_method(shell_item, _I_SHELL_ITEM_GET_DISPLAY_NAME_SLOT, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_wchar_p))
            display_name = ctypes.c_wchar_p()
            display_hr = get_display_name(shell_item, _SIGDN_FILESYSPATH, ctypes.byref(display_name))
            if _hresult_code(display_hr) != 0 or not display_name.value:
                _log_picker_failure("PICKER_RESULT_FAILED", hresult=display_hr, stage="PATH_EXTRACT", trace=trace)
                return NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")
            result = str(display_name.value)
        finally:
            if display_name.value:
                ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
                ole32.CoTaskMemFree.restype = None
                ole32.CoTaskMemFree(ctypes.cast(display_name, ctypes.c_void_p))
            _release(shell_item)
        return NativeProjectRootSelection(NativeProjectRootSelectionStatus.SELECTED, path=result)
    except Exception as error:  # noqa: BLE001 - safe fixed failure with diagnostics
        _log_picker_failure("PICKER_RESULT_FAILED", error=error, stage=trace.stage or "RETURN_RESULT", trace=trace)
        return NativeProjectRootSelection(NativeProjectRootSelectionStatus.FAILED, reason_code="PICKER_RESULT_FAILED")
    finally:
        if dialog.value:
            _release(dialog)
        if initialized:
            _log_picker_stage(trace, "COM_UNINITIALIZE")
            ole32.CoUninitialize()


class _GUID(ctypes.Structure):
    _fields_ = (("data", ctypes.c_ubyte * 16),)


def _guid(value: str) -> _GUID:
    import uuid

    return _GUID((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


def _com_method(pointer: ctypes.c_void_p, slot: int, restype: object, *argtypes: object):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[slot])


def _release(pointer: ctypes.c_void_p) -> None:
    try:
        _com_method(pointer, _I_UNKNOWN_RELEASE_SLOT, ctypes.c_ulong)(pointer)
    except Exception as error:  # noqa: BLE001 - COM cleanup must not mask picker result
        logger.debug(
            "native_project_root_picker_release_failed",
            extra={"event_name": "native_project_root_picker_release_failed", "error_type": type(error).__name__},
        )


def _format_hresult(value: object) -> str:
    try:
        return f"0x{int(value) & 0xFFFFFFFF:08X}"
    except (TypeError, ValueError):
        return "-"


def _hresult_code(value: object) -> int:
    try:
        return int(value) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return -1


def _hresult_succeeded(value: object) -> bool:
    """Mirror the Windows SUCCEEDED macro without Python truthiness."""

    normalized = _hresult_code(value)
    return normalized >= 0 and normalized < 0x80000000


def _raw_hresult(value: object) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "-"


def _initialize_com_sta(ole32: object, trace: _PickerTrace) -> tuple[bool, bool]:
    """Initialize COM on the dedicated worker and classify probe diagnostics separately."""

    trace.requested_apartment = "STA"
    trace.com_init_status = "FAILED"
    trace.apartment_probe_status = "UNAVAILABLE"
    try:
        ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        ole32.CoInitializeEx.restype = ctypes.c_long
        raw_hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    except Exception as error:  # noqa: BLE001 - fixed capability failure
        _log_picker_failure("PICKER_COM_INIT_FAILED", error=error, stage="COM_INITIALIZE", trace=trace)
        return False, False

    trace.raw_hresult = _raw_hresult(raw_hr)
    trace.normalized_hresult = _format_hresult(raw_hr)
    if not _hresult_succeeded(raw_hr) or _hresult_code(raw_hr) not in (0, 1):
        _log_picker_failure("PICKER_COM_INIT_FAILED", hresult=raw_hr, stage="COM_INITIALIZE", trace=trace)
        return False, False

    # S_OK and S_FALSE both initialize/attach the COM apartment and both need
    # one matching CoUninitialize call on this thread.
    trace.com_init_status = "SUCCESS"
    trace.apartment_type = "STA"
    observed, probe_status, probe_hr = _probe_apartment(ole32)
    trace.observed_apartment = observed
    trace.apartment_probe_status = probe_status
    if observed in {"MTA", "NA"}:
        _log_picker_failure(
            "PICKER_COM_APARTMENT_INVALID",
            hresult=probe_hr,
            stage="COM_INITIALIZE",
            trace=trace,
        )
        return False, True
    _log_picker_stage(trace, "COM_INITIALIZE", status="SUCCESS")
    return True, True


def _probe_apartment(ole32: object) -> tuple[str, str, object]:
    try:
        probe = ole32.CoGetApartmentType
    except AttributeError:
        return "UNKNOWN", "UNAVAILABLE", "-"
    try:
        probe.argtypes = (ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint))
        probe.restype = ctypes.c_long
        apartment = ctypes.c_uint()
        qualifier = ctypes.c_uint()
        hr = probe(ctypes.byref(apartment), ctypes.byref(qualifier))
        if not _hresult_succeeded(hr):
            return "UNKNOWN", "FAILED", hr
        return {1: "STA", 2: "MTA", 3: "NA", 4: "MAINSTA"}.get(apartment.value, "UNKNOWN"), "SUCCESS", hr
    except Exception:  # noqa: BLE001 - diagnostics cannot override successful COM init
        return "UNKNOWN", "FAILED", "-"


def _apartment_state(ole32: object) -> str:
    try:
        apt = ctypes.c_uint()
        qualifier = ctypes.c_uint()
        hr = ole32.CoGetApartmentType(ctypes.byref(apt), ctypes.byref(qualifier))
        if hr != 0:
            return "UNKNOWN"
        return {1: "STA", 2: "MTA", 3: "NA", 4: "MAINSTA"}.get(apt.value, "UNKNOWN")
    except Exception:  # noqa: BLE001 - diagnostics must never mask picker flow
        return "UNKNOWN"


def _log_picker_failure(
    reason_code: str,
    error: BaseException | None = None,
    *,
    stage: str = "-",
    hresult: object = "-",
    thread_id: int | None = None,
    apartment_state: str = "UNKNOWN",
    owner_hwnd: int = 0,
    trace: _PickerTrace | None = None,
) -> None:
    context = trace or _PickerTrace.create()
    if apartment_state != "UNKNOWN":
        context.apartment_type = apartment_state
    logger.warning(
        "native_project_root_picker_failure",
        extra={
            "event_name": "native_project_root_picker_failure",
            "trace_id": context.trace_id,
            "error_type": type(error).__name__ if error is not None else "-",
            "exception_type": type(error).__name__ if error is not None else "-",
            "reason_code": reason_code,
            "stage": stage,
            "hresult": _format_hresult(hresult),
            "thread_id": str(thread_id or context.thread_id),
            "thread_name": context.thread_name,
            "apartment_state": context.apartment_type,
            "apartment_type": context.apartment_type,
            "requested_apartment": context.requested_apartment,
            "com_init_status": context.com_init_status,
            "raw_hresult": context.raw_hresult,
            "normalized_hresult": context.normalized_hresult,
            "observed_apartment": context.observed_apartment,
            "apartment_probe_status": context.apartment_probe_status,
            "picker_backend": _BACKEND_ID,
            "owner_hwnd": "valid" if owner_hwnd or context.owner_present else "none",
            "owner_present": context.owner_present,
            "owner_pid_match": context.owner_pid_match,
            "duration_ms": context.duration_ms,
        },
    )


def _log_picker_stage(trace: _PickerTrace, stage: str, *, status: str = "-") -> None:
    trace.stage = stage
    logger.info(
        "native_project_root_picker_stage",
        extra={
            "event_name": "native_project_root_picker_stage",
            "trace_id": trace.trace_id,
            "error_type": "-",
            "exception_type": "-",
            "reason_code": "-",
            "stage": stage,
            "status": status,
            "hresult": "-",
            "thread_id": str(trace.thread_id),
            "thread_name": trace.thread_name,
            "apartment_state": trace.apartment_type,
            "apartment_type": trace.apartment_type,
            "requested_apartment": trace.requested_apartment,
            "com_init_status": trace.com_init_status,
            "raw_hresult": trace.raw_hresult,
            "normalized_hresult": trace.normalized_hresult,
            "observed_apartment": trace.observed_apartment,
            "apartment_probe_status": trace.apartment_probe_status,
            "picker_backend": _BACKEND_ID,
            "owner_hwnd": "valid" if trace.owner_present else "none",
            "owner_present": trace.owner_present,
            "owner_pid_match": trace.owner_pid_match,
            "duration_ms": trace.duration_ms,
        },
    )


def probe_picker_runtime() -> bool:
    """Probe packaged ctypes/COM loading without opening a user dialog."""

    if os.name != "nt":
        return False
    ole32 = ctypes.windll.ole32
    initialized = False
    dialog = ctypes.c_void_p()
    try:
        ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        ole32.CoInitializeEx.restype = ctypes.c_long
        coinit = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        if _hresult_code(coinit) not in (0, 1):
            return False
        initialized = True
        ole32.CoCreateInstance.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        ole32.CoCreateInstance.restype = ctypes.c_long
        hr = ole32.CoCreateInstance(
            ctypes.byref(_guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")),
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(_guid("D57C7288-D4AD-4768-BE02-9D969532D960")),
            ctypes.byref(dialog),
        )
        return _hresult_code(hr) == 0 and bool(dialog.value)
    finally:
        if dialog.value:
            _release(dialog)
        if initialized:
            ole32.CoUninitialize()


__all__ = (
    "NativeProjectRootBridge",
    "NativeProjectRootPicker",
    "NativeProjectRootPickerPort",
    "NativeProjectRootSelection",
    "NativeProjectRootSelectionStatus",
    "probe_picker_runtime",
)
