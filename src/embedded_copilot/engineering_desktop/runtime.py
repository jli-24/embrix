from __future__ import annotations

import copy

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRunResult,
    ApplicationRunStatus,
)
from embedded_copilot.engineering_workspace import EngineeringWorkspaceSnapshot

from .exceptions import (
    DesktopApplicationFailed,
    DesktopApplicationRejected,
    DesktopAuditUnavailable,
    DesktopRejected,
    DesktopUnavailable,
)
from .models import (
    DesktopAnswerDisplay,
    DesktopAnswerView,
    DesktopAuditEvent,
    DesktopAuditEventType,
    DesktopInteractionRequest,
    DesktopInteractionType,
    DesktopModelSettingsView,
    DesktopOpenProjectRequest,
    DesktopProjectionStatus,
    DesktopProjectView,
    DesktopQuestionRequest,
    DesktopReferenceKind,
    DesktopReferenceView,
    DesktopRuntimeComponentView,
    DesktopSession,
    DesktopSessionCloseRequest,
    DesktopSessionStatus,
    DesktopWorkspaceView,
    RuntimeStatusView,
)
from .ports import (
    DesktopApplicationPort,
    DesktopAuditPort,
    DesktopCapabilityPort,
    DesktopClockPort,
    DesktopModelSettingsPort,
    DesktopSessionPort,
    DesktopWorkspacePort,
)


def _copy(value: object, model: type, *, field: str):
    if type(value) is not model:
        raise DesktopUnavailable(f"{field} returned an invalid type")
    try:
        return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
    except (TypeError, ValueError) as error:
        raise DesktopUnavailable(f"{field} returned invalid content") from error


class EngineeringDesktopRuntime:
    """Read-only Desktop projection runtime with mandatory append-only audit."""

    __slots__ = (
        "_applications",
        "_audit",
        "_capabilities",
        "_clock",
        "_models",
        "_sessions",
        "_workspaces",
    )

    def __init__(
        self,
        *,
        sessions: DesktopSessionPort,
        workspaces: DesktopWorkspacePort,
        applications: DesktopApplicationPort,
        capabilities: DesktopCapabilityPort,
        model_settings: DesktopModelSettingsPort,
        audit: DesktopAuditPort,
        clock: DesktopClockPort,
    ) -> None:
        self._sessions = sessions
        self._workspaces = workspaces
        self._applications = applications
        self._capabilities = capabilities
        self._models = model_settings
        self._audit = audit
        self._clock = clock

    def _now(self):
        value = self._clock.now()
        from .contracts import utc_datetime

        return utc_datetime(value, field="desktop clock")

    def _record(
        self,
        session: DesktopSession,
        event_type: DesktopAuditEventType,
        reference_fingerprint: str | None = None,
    ) -> DesktopAuditEvent:
        event_id = DesktopAuditEvent.event_id_for(
            session.fingerprint, event_type, reference_fingerprint
        )
        try:
            existing = self._audit.get(event_id)
        except Exception as error:
            raise DesktopAuditUnavailable("desktop audit read failed") from error
        if existing is not None:
            checked_existing = _copy(existing, DesktopAuditEvent, field="desktop audit")
            if (
                checked_existing.event_type is event_type
                and checked_existing.session_id == session.session_id
                and checked_existing.session_fingerprint == session.fingerprint
                and checked_existing.project_id == session.project_id
                and checked_existing.reference_fingerprint == reference_fingerprint
            ):
                return checked_existing
            raise DesktopAuditUnavailable("desktop audit identity conflict")
        event = DesktopAuditEvent(
            event_id=event_id,
            event_type=event_type,
            session_id=session.session_id,
            session_fingerprint=session.fingerprint,
            project_id=session.project_id,
            reference_fingerprint=reference_fingerprint,
            timestamp=self._now(),
        )
        try:
            stored = self._audit.append(event)
        except Exception as error:
            raise DesktopAuditUnavailable("desktop audit write failed") from error
        checked = _copy(stored, DesktopAuditEvent, field="desktop audit")
        if checked != event:
            raise DesktopAuditUnavailable("desktop audit return was altered")
        return checked

    def _save_session(self, session: DesktopSession) -> DesktopSession:
        try:
            stored = self._sessions.save(session)
        except Exception as error:
            raise DesktopUnavailable("desktop session store is unavailable") from error
        checked = _copy(stored, DesktopSession, field="desktop session")
        if checked != session:
            raise DesktopUnavailable("desktop session return was altered")
        return checked

    def _session(self, session_id: str, project_id: str) -> DesktopSession:
        try:
            stored = self._sessions.get(session_id)
        except Exception as error:
            raise DesktopUnavailable("desktop session read failed") from error
        if stored is None:
            raise DesktopRejected("desktop session was not found")
        session = _copy(stored, DesktopSession, field="desktop session")
        if session.project_id != project_id:
            raise DesktopRejected("desktop session project mismatch")
        if session.status is DesktopSessionStatus.CLOSED:
            raise DesktopRejected("desktop session is closed")
        if session.status is DesktopSessionStatus.EXPIRED:
            raise DesktopRejected("desktop session is expired")
        if self._now() >= session.expires_at:
            expired = DesktopSession(
                session_id=session.session_id,
                project_id=session.project_id,
                requester=session.requester,
                opened_at=session.opened_at,
                last_activity=session.expires_at,
                expires_at=session.expires_at,
                status=DesktopSessionStatus.EXPIRED,
            )
            saved = self._save_session(expired)
            self._record(saved, DesktopAuditEventType.DESKTOP_SESSION_EXPIRED)
            raise DesktopRejected("desktop session is expired")
        return session

    def _workspace(self, project_id: str) -> EngineeringWorkspaceSnapshot:
        try:
            snapshot = self._workspaces.get_workspace(project_id)
        except Exception as error:
            raise DesktopUnavailable("desktop workspace read failed") from error
        if snapshot is None:
            raise DesktopRejected("desktop workspace was not found")
        checked = _copy(
            snapshot, EngineeringWorkspaceSnapshot, field="desktop workspace"
        )
        if checked.project_id != project_id:
            raise DesktopRejected("desktop workspace project mismatch")
        return checked

    @staticmethod
    def _reference(
        *,
        reference_kind: DesktopReferenceKind,
        domain: str,
        kind: str,
        object_id: str,
        source_fingerprint: str,
        status: DesktopProjectionStatus | None,
        occurred_at,
        relation_type: str | None = None,
    ) -> DesktopReferenceView:
        return DesktopReferenceView(
            reference_kind=reference_kind,
            source_domain=domain,
            source_kind=kind,
            reference_id=object_id,
            source_fingerprint=source_fingerprint,
            status=status,
            relation_type=relation_type,
            occurred_at=occurred_at,
        )

    def _workspace_view(
        self, snapshot: EngineeringWorkspaceSnapshot
    ) -> DesktopWorkspaceView:
        project = self._reference(
            reference_kind=DesktopReferenceKind.PROJECT,
            domain="WORKSPACE",
            kind="PROJECT",
            object_id=snapshot.project_id,
            source_fingerprint=snapshot.fingerprint,
            status=None,
            occurred_at=snapshot.generated_at,
        )
        workspace = self._reference(
            reference_kind=DesktopReferenceKind.WORKSPACE_SNAPSHOT,
            domain="WORKSPACE",
            kind="SNAPSHOT",
            object_id=snapshot.workspace_id,
            source_fingerprint=snapshot.fingerprint,
            status=None,
            occurred_at=snapshot.generated_at,
        )
        timeline = tuple(
            self._reference(
                reference_kind=DesktopReferenceKind.TIMELINE_ENTRY,
                domain=item.reference.source_domain.value,
                kind=item.reference.source_kind.value,
                object_id=item.reference.source_object_id,
                source_fingerprint=item.reference.source_fingerprint,
                status=DesktopProjectionStatus(item.reference.status.value),
                occurred_at=item.reference.occurred_at,
            )
            for item in snapshot.timeline
        )
        artifacts = tuple(
            self._reference(
                reference_kind=DesktopReferenceKind.ARTIFACT,
                domain=item.source_domain.value,
                kind=item.source_kind.value,
                object_id=item.source_object_id,
                source_fingerprint=item.source_fingerprint,
                status=DesktopProjectionStatus(item.status.value),
                occurred_at=item.occurred_at,
            )
            for item in snapshot.source_references
        )
        evidence_by_fingerprint: dict[str, DesktopReferenceView] = {}
        for entry in snapshot.timeline:
            for link in entry.lineage:
                evidence_by_fingerprint[link.fingerprint] = self._reference(
                    reference_kind=DesktopReferenceKind.EVIDENCE,
                    domain=link.source_domain.value,
                    kind=link.source_kind.value,
                    object_id=link.source_object_id,
                    source_fingerprint=link.source_fingerprint,
                    status=None,
                    occurred_at=None,
                    relation_type=link.relation_type.value,
                )
        evidence = tuple(
            sorted(evidence_by_fingerprint.values(), key=lambda item: item.fingerprint)
        )
        active_proposals = tuple(
            item
            for item in timeline
            if item.source_kind == "PROPOSAL"
            and item.status
            in {
                DesktopProjectionStatus.PENDING,
                DesktopProjectionStatus.ACTIVE,
                DesktopProjectionStatus.REVIEW_REQUIRED,
            }
        )
        pending_reviews = tuple(
            item
            for item in timeline
            if item.source_kind == "REVIEW"
            and item.status
            in {
                DesktopProjectionStatus.PENDING,
                DesktopProjectionStatus.ACTIVE,
                DesktopProjectionStatus.REVIEW_REQUIRED,
            }
        )
        return DesktopWorkspaceView(
            project_reference=project,
            workspace_snapshot_reference=workspace,
            timeline=timeline,
            artifacts=artifacts,
            evidence=evidence,
            active_proposals=active_proposals,
            pending_reviews=pending_reviews,
            generated_at=snapshot.generated_at,
        )

    def open_project(self, request: DesktopOpenProjectRequest) -> DesktopProjectView:
        checked = _copy(
            request, DesktopOpenProjectRequest, field="desktop open request"
        )
        now = self._now()
        if checked.expires_at <= now:
            raise DesktopRejected("desktop session expiry is invalid")
        snapshot = self._workspace(checked.project_id)
        session = self._save_session(
            DesktopSession(
                session_id=checked.session_id,
                project_id=checked.project_id,
                requester=checked.requester,
                opened_at=now,
                last_activity=now,
                expires_at=checked.expires_at,
                status=DesktopSessionStatus.OPEN,
            )
        )
        self._record(session, DesktopAuditEventType.DESKTOP_SESSION_OPENED)
        self._record(
            session, DesktopAuditEventType.PROJECT_VIEWED, snapshot.fingerprint
        )
        return DesktopProjectView(
            session=session, workspace=self._workspace_view(snapshot)
        )

    def close_session(self, request: DesktopSessionCloseRequest) -> DesktopSession:
        checked = _copy(
            request, DesktopSessionCloseRequest, field="desktop close request"
        )
        session = self._session(checked.session_id, checked.project_id)
        if (
            checked.timestamp < session.last_activity
            or checked.timestamp > session.expires_at
        ):
            raise DesktopRejected("desktop close timestamp is invalid")
        closed = self._save_session(
            DesktopSession(
                session_id=session.session_id,
                project_id=session.project_id,
                requester=session.requester,
                opened_at=session.opened_at,
                last_activity=checked.timestamp,
                expires_at=session.expires_at,
                status=DesktopSessionStatus.CLOSED,
            )
        )
        self._record(closed, DesktopAuditEventType.DESKTOP_SESSION_CLOSED)
        return closed

    def ask(self, request: DesktopQuestionRequest) -> DesktopAnswerView:
        return self.ask_for_display(request).answer_view

    def ask_for_display(self, request: DesktopQuestionRequest) -> DesktopAnswerDisplay:
        checked = _copy(
            request, DesktopQuestionRequest, field="desktop question request"
        )
        session = self._session(checked.session_id, checked.project_id)
        application_request = _copy(
            checked.application_request,
            ApplicationQuestionRequest,
            field="application question",
        )
        if application_request.requester != session.requester:
            raise DesktopRejected("desktop requester does not own the session")
        self._record(
            session,
            DesktopAuditEventType.QUESTION_SUBMITTED,
            application_request.fingerprint,
        )
        try:
            result = self._applications.answer(application_request)
        except Exception as error:
            raise DesktopUnavailable(
                "desktop application runtime is unavailable"
            ) from error
        result = _copy(result, ApplicationRunResult, field="application result")
        if (
            result.project_id != session.project_id
            or result.question_fingerprint != application_request.fingerprint
        ):
            raise DesktopRejected("desktop application result binding mismatch")
        if result.status is ApplicationRunStatus.REJECTED:
            raise DesktopApplicationRejected("application result is REJECTED")
        if result.status is ApplicationRunStatus.FAILED:
            raise DesktopApplicationFailed("application result is FAILED")
        if result.status is not ApplicationRunStatus.COMPLETED or result.answer is None:
            raise DesktopUnavailable("application result is malformed")
        answer = result.answer
        view = DesktopAnswerView(
            answer_reference=self._reference(
                reference_kind=DesktopReferenceKind.ANSWER,
                domain="APPLICATION",
                kind="ANSWER",
                object_id=answer.answer_id,
                source_fingerprint=answer.fingerprint,
                status=DesktopProjectionStatus.COMPLETED,
                occurred_at=result.completed_at,
            ),
            evidence_reference=self._reference(
                reference_kind=DesktopReferenceKind.EVIDENCE,
                domain="APPLICATION",
                kind="EVIDENCE",
                object_id=f"evidence-{answer.answer_id}",
                source_fingerprint=answer.fingerprint,
                status=None,
                occurred_at=result.completed_at,
            ),
            model_reference=self._reference(
                reference_kind=DesktopReferenceKind.MODEL,
                domain="APPLICATION",
                kind="MODEL",
                object_id=f"{answer.model_info.provider}-{answer.model_info.model_name}",
                source_fingerprint=answer.model_info.fingerprint,
                status=None,
                occurred_at=result.completed_at,
            ),
            timestamp=result.completed_at,
        )
        self._record(session, DesktopAuditEventType.ANSWER_DISPLAYED, view.fingerprint)
        return DesktopAnswerDisplay(
            answer_view=view,
            content=answer.content,
            timestamp=result.completed_at,
        )

    def _view(
        self, request: DesktopInteractionRequest, *, expected: DesktopInteractionType
    ):
        checked = _copy(request, DesktopInteractionRequest, field="desktop interaction")
        if checked.request_type is not expected:
            raise DesktopRejected("desktop interaction type mismatch")
        session = self._session(checked.session_id, checked.project_id)
        snapshot = self._workspace(session.project_id)
        view = self._workspace_view(snapshot)
        assert checked.reference is not None
        return session, view, checked.reference

    def get_workspace(self, request: DesktopInteractionRequest) -> DesktopWorkspaceView:
        session, view, reference = self._view(
            request, expected=DesktopInteractionType.VIEW_WORKSPACE
        )
        if reference != view.workspace_snapshot_reference:
            raise DesktopRejected("desktop workspace reference mismatch")
        self._record(
            session, DesktopAuditEventType.PROJECT_VIEWED, reference.fingerprint
        )
        return view

    def get_timeline(
        self, request: DesktopInteractionRequest
    ) -> tuple[DesktopReferenceView, ...]:
        session, view, reference = self._view(
            request, expected=DesktopInteractionType.VIEW_TIMELINE
        )
        if reference != view.workspace_snapshot_reference:
            raise DesktopRejected("desktop timeline reference mismatch")
        self._record(
            session, DesktopAuditEventType.PROJECT_VIEWED, reference.fingerprint
        )
        return view.timeline

    def _single_reference(
        self,
        request: DesktopInteractionRequest,
        *,
        expected: DesktopInteractionType,
        candidates: str,
        event_type: DesktopAuditEventType,
    ) -> DesktopReferenceView:
        session, view, reference = self._view(request, expected=expected)
        if reference not in getattr(view, candidates):
            raise DesktopRejected("desktop reference is not present in workspace")
        self._record(session, event_type, reference.fingerprint)
        return reference

    def get_evidence(self, request: DesktopInteractionRequest) -> DesktopReferenceView:
        return self._single_reference(
            request,
            expected=DesktopInteractionType.VIEW_EVIDENCE,
            candidates="evidence",
            event_type=DesktopAuditEventType.EVIDENCE_VIEWED,
        )

    def get_proposal(self, request: DesktopInteractionRequest) -> DesktopReferenceView:
        return self._single_reference(
            request,
            expected=DesktopInteractionType.VIEW_PROPOSAL,
            candidates="active_proposals",
            event_type=DesktopAuditEventType.PROPOSAL_VIEWED,
        )

    def get_review(self, request: DesktopInteractionRequest) -> DesktopReferenceView:
        return self._single_reference(
            request,
            expected=DesktopInteractionType.VIEW_REVIEW,
            candidates="pending_reviews",
            event_type=DesktopAuditEventType.REVIEW_VIEWED,
        )

    def get_model_settings(
        self, request: DesktopInteractionRequest
    ) -> DesktopModelSettingsView:
        session, view, reference = self._view(
            request, expected=DesktopInteractionType.VIEW_WORKSPACE
        )
        if reference != view.workspace_snapshot_reference:
            raise DesktopRejected("desktop workspace reference mismatch")
        try:
            settings = self._models.get_settings(session.project_id)
        except Exception as error:
            raise DesktopUnavailable(
                "desktop model settings are unavailable"
            ) from error
        checked = _copy(
            settings, DesktopModelSettingsView, field="desktop model settings"
        )
        self._record(session, DesktopAuditEventType.PROJECT_VIEWED, checked.fingerprint)
        return checked

    def get_runtime_status(
        self, request: DesktopInteractionRequest
    ) -> RuntimeStatusView:
        session, view, reference = self._view(
            request, expected=DesktopInteractionType.VIEW_WORKSPACE
        )
        if reference != view.workspace_snapshot_reference:
            raise DesktopRejected("desktop workspace reference mismatch")
        try:
            components = self._capabilities.runtime_status(session.project_id)
        except Exception as error:
            raise DesktopUnavailable("desktop runtime status is unavailable") from error
        checked = tuple(
            _copy(item, DesktopRuntimeComponentView, field="desktop runtime component")
            for item in components
        )
        status = RuntimeStatusView(components=checked, generated_at=self._now())
        self._record(session, DesktopAuditEventType.PROJECT_VIEWED, status.fingerprint)
        return status


__all__ = ("EngineeringDesktopRuntime",)
