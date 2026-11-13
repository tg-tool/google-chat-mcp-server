"""Unread messages: after the read state, never counting the caller's own messages."""

from datetime import datetime

import pytest
from fakes import FakeChat, message, space

from google_chat_mcp.errors import ChatMcpError
from google_chat_mcp.spaces import SpaceDirectory
from google_chat_mcp.timeutil import JST
from google_chat_mcp.unread import find_unread_spaces, read_unread

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=JST)  # 90 days before: 2026-08-15T12:00:00+09:00
ME = "users/111"


def mine(space_id, n, ts):
    return message(space_id, n, ts, sender=ME, sender_name="利用者")


def make_chat() -> FakeChat:
    return FakeChat(
        me=ME,
        spaces=[
            space("A", name="営業定例", last_active="2026-11-13T02:00:00.000000Z"),
            space("B", name="雑談", last_active="2026-11-12T00:00:00.000000Z"),
            space("C", name="新しい部屋", last_active="2026-11-13T00:00:00.000000Z"),
            space("D", name="放置", last_active="2026-08-05T00:00:00.000000Z"),
            space("E", name="自分だけ", last_active="2026-11-13T01:30:00.000000Z"),
        ],
        read_states={
            "spaces/A": "2026-11-13T01:00:00.000000Z",
            "spaces/B": "2026-11-12T00:00:00.000000Z",
            "spaces/C": None,
            "spaces/D": "2026-08-01T00:00:00.000000Z",
            "spaces/E": "2026-11-13T01:00:00.000000Z",
        },
        messages={
            "spaces/A": [
                message("A", 1, "2026-11-13T00:30:00.000000Z"),
                mine("A", 3, "2026-11-13T01:20:00.000000Z"),
                message("A", 4, "2026-11-13T02:00:00.000000Z", text="確認お願いします"),
            ],
            "spaces/B": [message("B", 2, "2026-11-12T00:00:00.000000Z")],
            "spaces/C": [
                message("C", 5, "2026-07-01T00:00:00.000000Z"),
                message("C", 6, "2026-11-10T00:00:00.000000Z"),
                mine("C", 7, "2026-11-11T00:00:00.000000Z"),
            ],
            "spaces/D": [message("D", 9, "2026-08-05T00:00:00.000000Z")],
            "spaces/E": [mine("E", 8, "2026-11-13T01:30:00.000000Z")],
        },
    )


def run(chat=None, **kwargs):
    chat = chat or make_chat()
    return read_unread(chat, SpaceDirectory(chat), now=NOW, **kwargs)


def by_space(result) -> dict:
    return {s["space"]: s for s in result["spaces"]}


def msg_ids(entry) -> list[str]:
    return [m["id"].rsplit("/", 1)[1] for m in entry["messages"]]


def test_unread_after_last_read_excluding_own():
    result = run()
    a = by_space(result)["spaces/A"]
    assert msg_ids(a) == ["m4"]
    assert a["unread_count"] == 1
    assert a["has_more"] is False
    assert a["last_read"] == "2026-11-13T10:00:00+09:00"
    assert a["display_name"] == "営業定例"
    assert "spaces/E" not in by_space(result)
    assert list(by_space(result)) == ["spaces/A", "spaces/C"]
    assert result["total"] == 2
    assert result["truncated"] is False


def test_space_without_new_messages_is_not_listed():
    assert "spaces/B" not in by_space(run())


def test_never_opened_space_is_bounded_and_noted():
    chat = make_chat()
    result = run(chat)
    c = by_space(result)["spaces/C"]
    assert msg_ids(c) == ["m6"]
    assert c["last_read"] is None
    assert any("90日" in note and "新しい部屋" in note for note in result["notes"])
    c_filters = [
        kw["filter"]
        for key, kw in chat.calls
        if key == "messages.list" and kw["parent"] == "spaces/C"
    ]
    assert c_filters == ['createTime > "2026-08-15T12:00:00+09:00"']


def test_old_spaces_not_checked_and_noted():
    chat = make_chat()
    result = run(chat)
    read_state_names = [
        kw["name"] for key, kw in chat.calls if key == "users.spaces.getSpaceReadState"
    ]
    assert not any("/spaces/D/" in name for name in read_state_names)
    assert any("1 件" in note for note in result["notes"])


def test_global_limit_truncates():
    chat = FakeChat(
        me=ME,
        spaces=[
            space("X", name="X", last_active="2026-11-13T02:00:00.000000Z"),
            space("Y", name="Y", last_active="2026-11-13T01:00:00.000000Z"),
        ],
        read_states={
            "spaces/X": "2026-11-13T00:00:00.000000Z",
            "spaces/Y": "2026-11-13T00:00:00.000000Z",
        },
        messages={
            "spaces/X": [message("X", i, f"2026-11-13T01:0{i}:00.000000Z") for i in range(1, 4)],
            "spaces/Y": [message("Y", i, f"2026-11-13T00:5{i}:00.000000Z") for i in range(1, 3)],
        },
    )
    result = run(chat, limit=2)
    assert result["total"] == 2
    assert result["truncated"] is True
    x = by_space(result)["spaces/X"]
    assert msg_ids(x) == ["m1", "m2"]
    assert x["has_more"] is True
    assert "spaces/Y" not in by_space(result)
    assert any("1 スペース" in note for note in result["notes"])


def test_single_space_mode():
    chat = make_chat()
    result = run(chat, space="営業定例")
    assert list(by_space(result)) == ["spaces/A"]
    parents = {kw["parent"] for key, kw in chat.calls if key == "messages.list"}
    assert parents == {"spaces/A"}


def test_invalid_limit_raises():
    with pytest.raises(ChatMcpError, match="limit"):
        run(limit=0)


def test_find_unread_spaces_for_listing():
    chat = make_chat()
    found, notes = find_unread_spaces(chat, SpaceDirectory(chat), now=NOW)
    assert [(info.space, last_read) for info, last_read in found] == [
        ("spaces/A", "2026-11-13T01:00:00.000000Z"),
        ("spaces/C", None),
    ]
    assert any("1 件" in note for note in notes)


def test_bot_posts_after_last_read_count_even_when_last_active_is_older():
    # Chat's lastActiveTime is not moved by app (BOT) posts — measured on the real API
    chat = FakeChat(
        me=ME,
        spaces=[space("R", name="日報", last_active="2026-11-13T01:00:00.000000Z")],
        read_states={"spaces/R": "2026-11-13T01:00:00.000000Z"},
        messages={
            "spaces/R": [
                message("R", 1, "2026-11-13T01:00:00.000000Z"),
                message(
                    "R",
                    2,
                    "2026-11-13T02:00:00.000000Z",
                    sender="users/999",
                    sender_name="日報ボット",
                    sender_type="BOT",
                ),
            ]
        },
    )
    result = run(chat)
    assert [msg_ids(s) for s in result["spaces"]] == [["m2"]]
    found, _ = find_unread_spaces(chat, SpaceDirectory(chat), now=NOW)
    assert [info.space for info, _ in found] == ["spaces/R"]


def test_parallel_scan_gives_the_same_result():
    assert run(factory=make_chat) == run()
