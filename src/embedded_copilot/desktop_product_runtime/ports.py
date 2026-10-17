from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_desktop import (
    DesktopAnswerDisplay,
    DesktopInteractionRequest,
    DesktopProjectView,
    DesktopQuestionRequest,
    DesktopSession,
    DesktopSessionCloseRequest,
    DesktopWorkspaceView,
)

from .models import DesktopProductEvent, DesktopUserPreferences, ProductSession


@runtime_checkable
class DesktopFoundationPort(Protocol):
    def open_project(self, request) -> DesktopProjectView: ...
    def close_session(self, request: DesktopSessionCloseRequest) -> DesktopSession: ...
    def get_workspace(
        self, request: DesktopInteractionRequest
    ) -> DesktopWorkspaceView: ...
    def get_timeline(self, request: DesktopInteractionRequest): ...
    def ask_for_display(
        self, request: DesktopQuestionRequest
    ) -> DesktopAnswerDisplay: ...


@runtime_checkable
class ProductSessionStorePort(Protocol):
    def save(self, session: ProductSession) -> ProductSession: ...
    def get(self, product_session_id: str) -> ProductSession | None: ...


@runtime_checkable
class DesktopPreferenceStorePort(Protocol):
    def save(self, preferences: DesktopUserPreferences) -> DesktopUserPreferences: ...
    def get(self, requester: str) -> DesktopUserPreferences | None: ...


@runtime_checkable
class DesktopProductEventStorePort(Protocol):
    def append(self, event: DesktopProductEvent) -> DesktopProductEvent: ...
    def read_after(
        self, product_session_id: str, last_event_id: str | None
    ) -> tuple[DesktopProductEvent, ...] | None: ...


@runtime_checkable
class ProductClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
