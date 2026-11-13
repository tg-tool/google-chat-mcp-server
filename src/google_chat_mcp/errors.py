"""Anticipated failures. Their messages are shown to the person (and Claude) as-is."""


class ChatMcpError(Exception):
    """A failure we saw coming; the message says what happened and how to fix it."""


class AuthRequiredError(ChatMcpError):
    """A Google login is needed, or could not be completed."""
