from __future__ import annotations

import copy

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.engineering_desktop import (
    DesktopInteractionRequest,
    DesktopInteractionType,
    DesktopOpenProjectRequest,
    DesktopQuestionRequest,
    DesktopRejected,
    DesktopSessionCloseRequest,
)

from .exceptions import ProductSessionRejected, ProductUnavailable
from .models import (
    DesktopChatRequest,
    DesktopChatResponse,
    DesktopNotificationView,
    DesktopProductEvent,
    DesktopTheme,
    DesktopUserPreferences,
    NotificationLevel,
    ProductEventType,
    ProductSession,
    ProductSessionOpenRequest,
    ProductSessionStatus,
    TimeDisplayFormat,
    TimelineInteractionView,
    explorer_from_workspace,
)
from .ports import (
    DesktopFoundationPort,
    DesktopPreferenceStorePort,
    DesktopProductEventStorePort,
    ProductClockPort,
    ProductSessionStorePort,
)


def _copy(value: object, model: type, *, field: str):
    if type(value) is not model:
        raise ProductUnavailable(f"{field} returned an invalid type")
    try:
        return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
    except (TypeError, ValueError) as error:
        raise ProductUnavailable(f"{field} returned invalid content") from error


class DesktopProductRuntime:
    __slots__ = ("_clock", "_desktop", "_events", "_preferences", "_sessions")

    def __init__(
        self,
        *,
        desktop: DesktopFoundationPort,
        sessions: ProductSessionStorePort,
        preferences: DesktopPreferenceStorePort,
        events: DesktopProductEventStorePort,
        clock: ProductClockPort,
    ) -> None:
        self._desktop = desktop
        self._sessions = sessions
        self._preferences = preferences
        self._events = events
        self._clock = clock

    def _now(self):
        from .contracts import utc_datetime

        return utc_datetime(self._clock.now(), field="product clock")

    def _session(self, product_session_id: str, requester: str) -> ProductSession:
        stored = self._sessions.get(product_session_id)
        if stored is None:
            raise ProductSessionRejected("product session was not found")
        session = _copy(stored, ProductSession, field="product session")
        if session.requester != requester:
            raise ProductSessionRejected("product session requester mismatch")
        if (
            session.status is ProductSessionStatus.OPEN
            and session.desktop_session.expires_at <= self._now()
        ):
            expired = ProductSession(
                product_session_id=session.product_session_id,
                requester=session.requester,
                desktop_session=session.desktop_session,
                workspace_reference=session.workspace_reference,
                created_at=session.created_at,
                status=ProductSessionStatus.EXPIRED,
            )
            stored_expired = _copy(
                self._sessions.save(expired),
                ProductSession,
                field="expired product session",
            )
            if stored_expired != expired:
                raise ProductUnavailable("expired product session return was altered")
            self._event(
                stored_expired,
                ProductEventType.SESSION_EXPIRED,
                stored_expired.fingerprint,
            )
            raise ProductSessionRejected("product session is expired")
        if session.status is not ProductSessionStatus.OPEN:
            raise ProductSessionRejected("product session is not open")
        return session

    def _event(
        self,
        session: ProductSession,
        event_type: ProductEventType,
        reference_fingerprint: str | None = None,
        status=None,
    ) -> DesktopProductEvent:
        prior = self._events.read_after(session.product_session_id, None) or ()
        sequence = prior[-1].sequence + 1 if prior else 1
        event = DesktopProductEvent(
            event_id=f"desktop-product-{session.product_session_id}-{sequence}",
            sequence=sequence,
            product_session_id=session.product_session_id,
            event_type=event_type,
            reference_fingerprint=reference_fingerprint,
            status=status,
            timestamp=self._now(),
        )
        stored = self._events.append(event)
        checked = _copy(stored, DesktopProductEvent, field="product event")
        if checked != event:
            raise ProductUnavailable("product event return was altered")
        return checked

    def open_session(
        self, request: ProductSessionOpenRequest, *, requester: str
    ) -> ProductSession:
        checked = _copy(
            request, ProductSessionOpenRequest, field="product session request"
        )
        now = self._now()
        if checked.expires_at <= now:
            raise ProductSessionRejected("product session expiry is invalid")
        project = self._desktop.open_project(
            DesktopOpenProjectRequest(
                session_id=f"desktop-{checked.product_session_id}",
                project_id=checked.project_id,
                requester=requester,
                expires_at=checked.expires_at,
            )
        )
        session = ProductSession(
            product_session_id=checked.product_session_id,
            requester=requester,
            desktop_session=project.session,
            workspace_reference=project.workspace.workspace_snapshot_reference,
            created_at=now,
            status=ProductSessionStatus.OPEN,
        )
        stored = _copy(
            self._sessions.save(session), ProductSession, field="product session"
        )
        if stored != session:
            raise ProductUnavailable("product session return was altered")
        self._event(stored, ProductEventType.SESSION_OPENED, stored.fingerprint)
        return stored

    def close_session(
        self, product_session_id: str, *, requester: str
    ) -> ProductSession:
        session = self._session(product_session_id, requester)
        self._desktop.close_session(
            DesktopSessionCloseRequest(
                session_id=session.desktop_session.session_id,
                project_id=session.desktop_session.project_id,
                timestamp=self._now(),
            )
        )
        closed = ProductSession(
            product_session_id=session.product_session_id,
            requester=session.requester,
            desktop_session=session.desktop_session,
            workspace_reference=session.workspace_reference,
            created_at=session.created_at,
            status=ProductSessionStatus.CLOSED,
        )
        stored = _copy(
            self._sessions.save(closed), ProductSession, field="product session"
        )
        if stored != closed:
            raise ProductUnavailable("product session return was altered")
        self._event(stored, ProductEventType.SESSION_CLOSED, stored.fingerprint)
        return stored

    def _workspace(self, session: ProductSession):
        view = self._desktop.get_workspace(
            DesktopInteractionRequest(
                session_id=session.desktop_session.session_id,
                project_id=session.desktop_session.project_id,
                request_type=DesktopInteractionType.VIEW_WORKSPACE,
                reference=session.workspace_reference,
                timestamp=self._now(),
            )
        )
        self._event(session, ProductEventType.WORKSPACE_REFRESHED, view.fingerprint)
        return view

    def explorer(self, product_session_id: str, *, requester: str):
        return explorer_from_workspace(
            self._workspace(self._session(product_session_id, requester))
        )

    def timeline(
        self, product_session_id: str, *, requester: str
    ) -> TimelineInteractionView:
        session = self._session(product_session_id, requester)
        view = self._workspace(session)
        return TimelineInteractionView(
            workspace_fingerprint=view.fingerprint,
            entries=view.timeline,
            generated_at=view.generated_at,
        )

    def chat(
        self, request: DesktopChatRequest, *, requester: str
    ) -> DesktopChatResponse:
        checked = _copy(request, DesktopChatRequest, field="desktop chat request")
        session = self._session(checked.product_session_id, requester)
        question = DesktopQuestionRequest(
            session_id=session.desktop_session.session_id,
            project_id=session.desktop_session.project_id,
            application_request=ApplicationQuestionRequest(
                request_id=checked.request_id,
                trace_id=checked.trace_id,
                project_id=session.desktop_session.project_id,
                requester=requester,
                question=checked.question,
                constraints=checked.constraints,
                requested_at=checked.requested_at,
            ),
            timestamp=checked.requested_at,
        )
        try:
            display = self._desktop.ask_for_display(question)
        except DesktopRejected as error:
            self._event(session, ProductEventType.CHAT_REJECTED, question.fingerprint)
            raise ProductSessionRejected("desktop chat was rejected") from error
        except Exception as error:
            self._event(session, ProductEventType.CHAT_FAILED, question.fingerprint)
            raise ProductUnavailable("desktop chat is unavailable") from error
        response = DesktopChatResponse(
            product_session_id=session.product_session_id,
            display=display,
            timestamp=self._now(),
        )
        self._event(
            session, ProductEventType.CHAT_COMPLETED, display.answer_view.fingerprint
        )
        return response

    def preferences_for(self, *, requester: str) -> DesktopUserPreferences:
        stored = self._preferences.get(requester)
        if stored is None:
            return DesktopUserPreferences(
                requester=requester,
                theme=DesktopTheme.SYSTEM,
                sidebar_collapsed=False,
                time_format=TimeDisplayFormat.LOCAL,
                notification_level=NotificationLevel.IMPORTANT,
                updated_at=self._now(),
            )
        return _copy(stored, DesktopUserPreferences, field="desktop preferences")

    def update_preferences(
        self, preferences: DesktopUserPreferences, *, requester: str
    ) -> DesktopUserPreferences:
        checked = _copy(
            preferences, DesktopUserPreferences, field="desktop preferences"
        )
        if checked.requester != requester:
            raise ProductSessionRejected("desktop preference requester mismatch")
        stored = _copy(
            self._preferences.save(checked),
            DesktopUserPreferences,
            field="desktop preferences",
        )
        if stored != checked:
            raise ProductUnavailable("desktop preference return was altered")
        return stored

    def notifications(
        self, product_session_id: str, *, requester: str
    ) -> tuple[DesktopNotificationView, ...]:
        session = self._session(product_session_id, requester)
        events = self._events.read_after(session.product_session_id, None) or ()
        return tuple(
            DesktopNotificationView(
                notification_id=f"notification-{event.event_id}",
                event_type=event.event_type,
                reference_fingerprint=event.reference_fingerprint,
                status=event.status,
                occurred_at=event.timestamp,
            )
            for event in events
        )

    def events_after(
        self, product_session_id: str, *, requester: str, last_event_id: str | None
    ):
        self._session(product_session_id, requester)
        events = self._events.read_after(product_session_id, last_event_id)
        if events is None:
            return None
        return tuple(
            _copy(event, DesktopProductEvent, field="product event") for event in events
        )


__all__ = ("DesktopProductRuntime",)
