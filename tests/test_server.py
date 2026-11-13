"""The MCP surface: five tools, readable errors for Claude, quiet logs.

Timestamps here are relative to the real clock so the default windows
(30-day search, 90-day unread) never age out of the fixture.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeChat, member, message, space
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from google_chat_mcp import server
from google_chat_mcp.errors import AuthRequiredError
from google_chat_mcp.spaces import NameCache
from google_chat_mcp.timeutil import to_jst


def hours_ago(h: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


A_MSGS = [hours_ago(3), hours_ago(2), hours_ago(1)]
A_READ = hours_ago(2.5)


def make_chat(**kwargs) -> FakeChat:
    return FakeChat(
        spaces=[
            space("A", name="営業定例", last_active=A_MSGS[-1]),
            space("B", name="雑談", last_active=hours_ago(24 * 12)),
            space("D", type="DIRECT_MESSAGE", last_active=hours_ago(24)),
        ],
        members={"spaces/D": [member("users/111", "利用者"), member("users/222", "山田 太郎")]},
        messages={"spaces/A": [message("A", i + 1, t) for i, t in enumerate(A_MSGS)]},
        read_states={"spaces/A": A_READ},
        **kwargs,
    )


@pytest.fixture
def use(monkeypatch):
    def _use(chat):
        monkeypatch.setattr(server, "_chat", lambda: chat)
        monkeypatch.setattr(server, "_NAMES", NameCache())
        return chat

    return _use


def call(name: str, args: dict) -> dict:
    result = asyncio.run(server.server.call_tool(name, args))
    return json.loads(result.content[0].text)


def tool_error(name: str, args: dict) -> str:
    with pytest.raises(ToolError) as excinfo:
        asyncio.run(server.server.call_tool(name, args))
    assert not isinstance(excinfo.value, UnexpectedToolError), "message would not reach Claude"
    return str(excinfo.value)


def test_tools_registered():
    names = {t.name for t in asyncio.run(server.server.list_tools())}
    assert names == {"list_spaces", "get_messages", "search_messages", "get_members", "get_unread"}


def test_log_level_is_warning():
    assert server.server.settings.log_level == "WARNING"


def test_get_messages_end_to_end_with_fake(use):
    use(make_chat())
    data = call("get_messages", {"space": "営業定例", "limit": 2})
    assert [m["id"] for m in data["messages"]] == ["spaces/A/messages/m2", "spaces/A/messages/m3"]
    assert data["next_since"] == A_MSGS[-1]
    assert data["next_since"].endswith("Z")
    assert data["has_older"] is True


def test_domain_error_reaches_claude_as_tool_error(use):
    use(make_chat())
    assert "list_spaces" in tool_error("get_messages", {"space": "存在しない部屋"})


def test_http_403_is_explained(use):
    use(make_chat(errors={"messages.list": 403}))
    assert "権限" in tool_error("get_messages", {"space": "営業定例"})


def test_http_404_is_explained(use):
    use(make_chat(errors={"spaces.list": 404}))
    assert "見つかりません" in tool_error("list_spaces", {})


def test_auth_required_is_explained(monkeypatch):
    def needs_login():
        raise AuthRequiredError(
            "ログインが必要です。`python -m google_chat_mcp.auth` を実行してください。"
        )

    monkeypatch.setattr(server, "_chat", needs_login)
    assert "python -m google_chat_mcp.auth" in tool_error("list_spaces", {})


def test_list_spaces_filters(use):
    use(make_chat())
    names = lambda data: [s["display_name"] for s in data["spaces"]]  # noqa: E731
    assert names(call("list_spaces", {})) == ["営業定例", "DM: 山田 太郎", "雑談"]
    assert names(call("list_spaces", {"space_type": "DIRECT_MESSAGE"})) == ["DM: 山田 太郎"]
    assert names(call("list_spaces", {"name_contains": "営業"})) == ["営業定例"]
    since = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    assert names(call("list_spaces", {"active_since": since})) == ["営業定例", "DM: 山田 太郎"]
    assert call("list_spaces", {})["count"] == 3
    assert "space_type" in tool_error("list_spaces", {"space_type": "ROOM"})


def test_list_spaces_unread_only(use):
    use(make_chat())
    data = call("list_spaces", {"unread_only": True})
    assert [s["space"] for s in data["spaces"]] == ["spaces/A"]
    assert data["spaces"][0]["unread"] is True
    assert data["spaces"][0]["last_read"] == to_jst(A_READ)


def test_get_members_and_unread_and_search_are_wired(use):
    use(make_chat())
    members = call("get_members", {"space": "山田"})
    assert members["space"]["space"] == "spaces/D"
    assert [m["name"] for m in members["members"]] == ["利用者", "山田 太郎"]
    unread = call("get_unread", {"space": "営業定例"})
    assert [m["id"] for m in unread["spaces"][0]["messages"]] == [
        "spaces/A/messages/m2",
        "spaces/A/messages/m3",
    ]
    found = call("search_messages", {"keywords": "message 2"})
    assert [h["id"] for h in found["hits"]] == ["spaces/A/messages/m2"]


def test_names_are_cached_across_tool_calls(use):
    chat = use(make_chat())
    first = call("list_spaces", {})
    before = len(chat.calls)
    assert call("list_spaces", {}) == first
    assert [key for key, _ in chat.calls[before:]] == ["spaces.list"]


def test_network_and_refresh_failures_are_explained(monkeypatch):
    from google.auth.exceptions import RefreshError, TransportError

    def broken(exc):
        def _raise():
            raise exc

        return _raise

    monkeypatch.setattr(server, "_chat", broken(TransportError("connection reset")))
    assert "通信" in tool_error("list_spaces", {})
    monkeypatch.setattr(server, "_chat", broken(TimeoutError("timed out")))
    assert "通信" in tool_error("list_spaces", {})
    monkeypatch.setattr(server, "_chat", broken(RefreshError("invalid_grant")))
    assert "python -m google_chat_mcp.auth" in tool_error("list_spaces", {})
