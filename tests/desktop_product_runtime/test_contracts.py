from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.desktop_product_runtime import (
    DesktopTheme,
    DesktopUserPreferences,
    NotificationLevel,
    TimeDisplayFormat,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def test_preferences_are_immutable_and_deterministic() -> None:
    first = DesktopUserPreferences(
        requester="engineer1",
        theme=DesktopTheme.SYSTEM,
        sidebar_collapsed=False,
        time_format=TimeDisplayFormat.UTC,
        notification_level=NotificationLevel.IMPORTANT,
        updated_at=NOW,
    )
    second = DesktopUserPreferences.model_validate(first.model_dump(mode="python"))
    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValidationError):
        DesktopUserPreferences(
            requester="api_key",
            theme=DesktopTheme.SYSTEM,
            sidebar_collapsed=False,
            time_format=TimeDisplayFormat.UTC,
            notification_level=NotificationLevel.IMPORTANT,
            updated_at=NOW,
        )
