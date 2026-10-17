from __future__ import annotations


class DesktopCopilotChatError(Exception):
    """Base error for the isolated desktop chat context."""


class ChatRejected(DesktopCopilotChatError):
    pass


class ChatConflict(DesktopCopilotChatError):
    pass


class ChatUnavailable(DesktopCopilotChatError):
    pass


class ChatApplicationFailed(DesktopCopilotChatError):
    def __init__(self, code: str = "COPILOT_CHAT_APPLICATION_FAILED") -> None:
        super().__init__(code)
        self.code = code


__all__ = (
    "ChatApplicationFailed",
    "ChatConflict",
    "ChatRejected",
    "ChatUnavailable",
    "DesktopCopilotChatError",
)
