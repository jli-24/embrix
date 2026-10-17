"""Credential Manager boundary for provider credentials and release material."""

from __future__ import annotations

import ctypes
import os
import secrets
from ctypes import wintypes
from typing import Protocol

from .credential_recovery_diagnostic import emit as recovery_emit
from .credential_save_diagnostic import emit


class CredentialUnavailable(RuntimeError):
    pass


CHAT_PROVIDER_CREDENTIAL_PURPOSE = "CHAT_PROVIDER_API_KEY:deepseek"
PROVIDER_BINDING_INTEGRITY_PURPOSE = "PROVIDER_BINDING_INTEGRITY:deepseek"


def provider_credential_purpose(provider: str) -> str:
    """Return the stable, secret-store-only target for one provider."""

    if not isinstance(provider, str) or not provider or any(value in provider for value in ("/", "\\", "..", "\x00")):
        raise ValueError("provider identifier is invalid")
    return CHAT_PROVIDER_CREDENTIAL_PURPOSE if provider == "deepseek" else f"CHAT_PROVIDER_API_KEY:{provider}"


def provider_connection_credential_purpose(connection_id: str) -> str:
    """Return the connection-scoped secret-store purpose.

    The legacy provider purpose remains readable for compatibility, but all
    connection-aware writes use this purpose so rotating one connection cannot
    replace another connection's credential.
    """

    if (
        not isinstance(connection_id, str)
        or not connection_id
        or any(value in connection_id for value in ("/", "\\", "..", "\x00"))
    ):
        raise ValueError("connection identifier is invalid")
    return f"CHAT_PROVIDER_API_KEY:{connection_id}"


def provider_binding_integrity_purpose(provider: str) -> str:
    if not isinstance(provider, str) or not provider or any(value in provider for value in ("/", "\\", "..", "\x00")):
        raise ValueError("provider identifier is invalid")
    return PROVIDER_BINDING_INTEGRITY_PURPOSE if provider == "deepseek" else f"PROVIDER_BINDING_INTEGRITY:{provider}"


def provider_connection_binding_integrity_purpose(connection_id: str) -> str:
    if (
        not isinstance(connection_id, str)
        or not connection_id
        or any(value in connection_id for value in ("/", "\\", "..", "\x00"))
    ):
        raise ValueError("connection identifier is invalid")
    return f"PROVIDER_BINDING_INTEGRITY:{connection_id}"


class CredentialProviderPort(Protocol):
    def get(self, purpose: str) -> bytes | None: ...
    def set(self, purpose: str, value: bytes) -> None: ...
    def delete(self, purpose: str) -> None: ...
    def get_or_create_provider_binding_key(self) -> bytes: ...


def _get_or_create_provider_binding_key(provider: CredentialProviderPort) -> bytes:
    """Resolve the integrity key while emitting only fixed safe stages."""

    try:
        existing = provider.get(PROVIDER_BINDING_INTEGRITY_PURPOSE)
    except Exception as error:
        emit("INTEGRITY_TARGET_EXISTS", result="READ_FAILURE", error=error)
        recovery_emit("CREDENTIAL_RECOVERY_INTEGRITY_ERROR", result="ERROR", error=error)
        raise
    if existing is not None:
        emit("INTEGRITY_TARGET_EXISTS", result="YES")
        recovery_emit("CREDENTIAL_RECOVERY_INTEGRITY_PRESENT", result="PRESENT")
        return existing
    emit("INTEGRITY_TARGET_EXISTS", result="NO")
    recovery_emit("CREDENTIAL_RECOVERY_INTEGRITY_ABSENT", result="ABSENT")
    emit("INTEGRITY_TARGET_CREATE_BEGIN", result="BEGIN")
    key = secrets.token_bytes(32)
    try:
        provider.set(PROVIDER_BINDING_INTEGRITY_PURPOSE, key)
    except Exception as error:
        emit("INTEGRITY_TARGET_CREATE_FAILURE", result="FAILURE", error=error)
        raise
    emit("INTEGRITY_TARGET_CREATE_SUCCESS", result="SUCCESS")
    return key


def _target(purpose: str) -> str:
    if not purpose or any(value in purpose for value in ("/", "\\", "..", "\x00")):
        raise ValueError("credential purpose is invalid")
    return f"EmbeddedCopilot/v7/{purpose}"


def _diagnostic_target_kind(purpose: str) -> str | None:
    if purpose.startswith("CHAT_PROVIDER_API_KEY:"):
        return "PRIMARY"
    if purpose.startswith("PROVIDER_BINDING_INTEGRITY:"):
        return "INTEGRITY"
    return None


def _lookup_stage(target_kind: str, suffix: str) -> str:
    if target_kind == "PRIMARY":
        return f"PRIMARY_CREDENTIAL_LOOKUP_{suffix}"
    return f"INTEGRITY_TARGET_LOOKUP_{suffix}"


class InMemoryCredentialProvider:
    """Test-only injection seam; it is never selected by production composition."""

    def __init__(self) -> None:
        self._values: dict[str, bytes] = {}

    def get(self, purpose: str) -> bytes | None:
        value = self._values.get(_target(purpose))
        return None if value is None else bytes(value)

    def set(self, purpose: str, value: bytes) -> None:
        if not isinstance(value, bytes) or not value:
            raise ValueError("credential value is invalid")
        self._values[_target(purpose)] = bytes(value)

    def delete(self, purpose: str) -> None:
        self._values.pop(_target(purpose), None)

    def get_or_create_backup_integrity_key(self) -> bytes:
        """Test-only equivalent of the purpose-scoped secure material lookup."""

        existing = self.get("backup-integrity")
        if existing is not None:
            return existing
        value = secrets.token_bytes(32)
        self.set("backup-integrity", value)
        return value

    def get_or_create_provider_binding_key(self) -> bytes:
        """Test-only equivalent of the provider-binding integrity lookup."""

        return _get_or_create_provider_binding_key(self)


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", ctypes.c_byte * 8),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialManager:
    """Current-token Credential Manager adapter using only Win32 APIs.

    ``CredWriteW`` stores the value in the credential set associated with the
    current Windows token. ``CRED_PERSIST_LOCAL_MACHINE`` keeps it across
    later logon sessions for that same user on that same computer; it does not
    make the credential visible to other Windows users or other computers.
    """

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        if os.name != "nt":
            raise CredentialUnavailable("Windows Credential Manager is unavailable")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_Credential)),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    @staticmethod
    def _raise(operation: str, *, error_code: int | None = None) -> None:
        error = ctypes.get_last_error() if error_code is None else error_code
        failure = CredentialUnavailable(f"credential manager {operation} failed ({error})")
        failure.win32_error_code = error  # type: ignore[attr-defined]
        raise failure

    def get(self, purpose: str) -> bytes | None:
        target_kind = _diagnostic_target_kind(purpose)
        if target_kind is not None:
            recovery_emit(
                _lookup_stage(target_kind, "BEGIN"),
                result="BEGIN",
                target_kind=target_kind,
            )
        pointer = ctypes.POINTER(_Credential)()
        ok = self._advapi.CredReadW(_target(purpose), self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer))
        if not ok:
            error_code = ctypes.get_last_error()
            if error_code == self._ERROR_NOT_FOUND:
                if target_kind is not None:
                    recovery_emit(
                        _lookup_stage(target_kind, "NOT_FOUND"),
                        result="NOT_FOUND",
                        target_kind=target_kind,
                        safe_code=error_code,
                    )
                return None
            if target_kind is not None:
                suffix = "ACCESS_DENIED" if error_code == 5 else "ERROR"
                recovery_emit(
                    _lookup_stage(target_kind, suffix),
                    result=suffix,
                    target_kind=target_kind,
                    safe_code=error_code,
                )
            self._raise("read", error_code=error_code)
        if target_kind is not None:
            recovery_emit(
                _lookup_stage(target_kind, "PRESENT"),
                result="PRESENT",
                target_kind=target_kind,
            )
        try:
            size = int(pointer.contents.CredentialBlobSize)
            return ctypes.string_at(pointer.contents.CredentialBlob, size)
        finally:
            self._advapi.CredFree(pointer)

    def set(self, purpose: str, value: bytes) -> None:
        if not isinstance(value, bytes) or not value:
            raise ValueError("credential value is invalid")
        blob = (ctypes.c_byte * len(value)).from_buffer_copy(value)
        credential = _Credential()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = _target(purpose)
        credential.CredentialBlobSize = len(value)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte))
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "Embedded Copilot"
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            self._raise("write")

    def delete(self, purpose: str) -> None:
        if not self._advapi.CredDeleteW(_target(purpose), self._CRED_TYPE_GENERIC, 0):
            if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                return
            self._raise("delete")

    def get_or_create_backup_integrity_key(self) -> bytes:
        """Use a purpose-specific key; it is never shared with a provider key."""

        existing = self.get("backup-integrity")
        if existing is not None:
            return existing
        key = secrets.token_bytes(32)
        self.set("backup-integrity", key)
        return key

    def get_or_create_provider_binding_key(self) -> bytes:
        """Use a separate purpose-scoped key for provider binding identity."""

        return _get_or_create_provider_binding_key(self)


__all__ = (
    "CHAT_PROVIDER_CREDENTIAL_PURPOSE",
    "PROVIDER_BINDING_INTEGRITY_PURPOSE",
    "CredentialProviderPort",
    "CredentialUnavailable",
    "InMemoryCredentialProvider",
    "WindowsCredentialManager",
    "provider_binding_integrity_purpose",
    "provider_connection_binding_integrity_purpose",
    "provider_connection_credential_purpose",
    "provider_credential_purpose",
)
