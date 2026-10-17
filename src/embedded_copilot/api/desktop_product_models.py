from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, field_validator

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.desktop_product_runtime import (
    DesktopTheme,
    DesktopUserPreferences,
    NotificationLevel,
    ProductSessionOpenRequest,
    TimeDisplayFormat,
)
from embedded_copilot.engineering_desktop.contracts import (
    identifier,
    tuple_only,
    utc_datetime,
)
from embedded_copilot.model_gateway.contracts import ModelGatewayContract, safe_text


class DesktopProductApiModel(ModelGatewayContract):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class DesktopSessionOpenInput(DesktopProductApiModel):
    product_session_id: str
    project_id: str
    expires_at: datetime

    @field_validator("product_session_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="expires_at")

    def to_request(self) -> ProductSessionOpenRequest:
        return ProductSessionOpenRequest(
            product_session_id=self.product_session_id,
            project_id=self.project_id,
            expires_at=self.expires_at,
        )


class DesktopChatInput(DesktopProductApiModel):
    request_id: str
    trace_id: str
    question: str
    constraints: tuple[str, ...] = ()
    requested_at: datetime

    @field_validator("request_id", "trace_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("question", mode="before")
    @classmethod
    def _question(cls, value: object) -> str:
        return safe_text(value, field="question", maximum=4096, allow_newlines=True)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value: object) -> tuple[str, ...]:
        return tuple(
            safe_text(item, field="constraint", maximum=256)
            for item in tuple_only(value, field="constraints")
        )

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    def to_application(
        self, *, project_id: str, requester: str
    ) -> ApplicationQuestionRequest:
        return ApplicationQuestionRequest(
            request_id=self.request_id,
            trace_id=self.trace_id,
            project_id=project_id,
            requester=requester,
            question=self.question,
            constraints=self.constraints,
            requested_at=self.requested_at,
        )


class DesktopPreferenceInput(DesktopProductApiModel):
    theme: DesktopTheme
    sidebar_collapsed: bool
    time_format: TimeDisplayFormat
    notification_level: NotificationLevel
    updated_at: datetime

    @field_validator("sidebar_collapsed", mode="before")
    @classmethod
    def _collapsed(cls, value: object) -> bool:
        if type(value) is not bool:
            raise ValueError("sidebar_collapsed is invalid")
        return value

    @field_validator("updated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="updated_at")

    def to_preferences(self, *, requester: str) -> DesktopUserPreferences:
        return DesktopUserPreferences(
            requester=requester,
            theme=self.theme,
            sidebar_collapsed=self.sidebar_collapsed,
            time_format=self.time_format,
            notification_level=self.notification_level,
            updated_at=self.updated_at,
        )
