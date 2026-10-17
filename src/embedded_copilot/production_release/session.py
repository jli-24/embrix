"""Short-lived local bootstrap and HttpOnly-session state for packaged Desktop."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class SessionRejected(PermissionError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LocalIdentity:
    requester: str
    tenant_id: str


@dataclass(slots=True)
class _Bootstrap:
    identity: LocalIdentity
    expires_at: datetime


@dataclass(slots=True)
class _Session:
    identity: LocalIdentity
    expires_at: datetime


class LocalSessionManager:
    """In-memory, restart-invalidated session manager.

    A nonce is only transported in a URL fragment.  It is consumed once by the
    frontend exchange endpoint and never written to a log, database or audit.
    """

    def __init__(self, *, now: callable | None = None, nonce_ttl_seconds: int = 30, session_ttl_seconds: int = 1800) -> None:
        if nonce_ttl_seconds < 1 or session_ttl_seconds < 1:
            raise ValueError("session duration is invalid")
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce_ttl = timedelta(seconds=nonce_ttl_seconds)
        self._session_ttl = timedelta(seconds=session_ttl_seconds)
        self._bootstraps: dict[str, _Bootstrap] = {}
        self._sessions: dict[str, _Session] = {}

    @property
    def session_ttl_seconds(self) -> int:
        return int(self._session_ttl.total_seconds())

    def issue_bootstrap(self, *, requester: str, tenant_id: str) -> str:
        identity = LocalIdentity(requester=requester, tenant_id=tenant_id)
        nonce = secrets.token_urlsafe(32)
        self._bootstraps[nonce] = _Bootstrap(identity=identity, expires_at=self._now() + self._nonce_ttl)
        return nonce

    def exchange(self, nonce: str) -> str:
        pending = self._bootstraps.pop(nonce, None)
        if pending is None or pending.expires_at <= self._now():
            raise SessionRejected("desktop bootstrap is invalid or expired", code="DESKTOP_BOOTSTRAP_REJECTED")
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(identity=pending.identity, expires_at=self._now() + self._session_ttl)
        return token

    def authenticate(self, token: str | None) -> LocalIdentity:
        if not token:
            raise SessionRejected("desktop session is required", code="DESKTOP_IDENTITY_REQUIRED")
        session = self._sessions.get(token)
        if session is None:
            self._sessions.pop(token, None)
            raise SessionRejected("desktop session is invalid", code="DESKTOP_IDENTITY_INVALID")
        if session.expires_at <= self._now():
            self._sessions.pop(token, None)
            raise SessionRejected("desktop session is invalid or expired", code="DESKTOP_SESSION_EXPIRED")
        session.expires_at = self._now() + self._session_ttl
        return session.identity

    def invalidate(self, token: str) -> None:
        self._sessions.pop(token, None)


__all__ = ("LocalIdentity", "LocalSessionManager", "SessionRejected")
