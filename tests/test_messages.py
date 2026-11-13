"""Reading messages: formatting, latest-N, continuing exactly where the last read stopped."""

import pytest
from fakes import FakeChat, message, space

from google_chat_mcp.errors import ChatMcpError
from google_chat_mcp.messages import format_message, read_messages
from google_chat_mcp.spaces import SpaceInfo

SP = SpaceInfo(
    space="spaces/A",
    display_name="営業定例",
    type="SPACE",
    threaded=False,
    last_active_ts="2026-11-13T00:10:00.000000Z",
    url="https://chat.google.com/room/A?cls=7",
)


def ts(i: int) -> str:
    return f"2026-11-13T00:{i // 60:02d}:{i % 60:02d}.000000Z"


def chat_with(msgs) -> FakeChat:
    return FakeChat(spaces=[space("A", name="営業定例")], messages={"spaces/A": msgs})


def ids(result) -> list[str]:
    return [m["id"].rsplit("/", 1)[1] for m in result["messages"]]


DRIVE_ATTACHMENT = {
    "name": "spaces/A/messages/m1/attachments/a1",
    "contentName": "見積書.pdf",
    "contentType": "application/pdf",
    "source": "DRIVE_FILE",
    "driveDataRef": {"driveFileId": "drive-file-1"},
    "thumbnailUri": "https://example.com/thumb",
    "downloadUri": "https://example.com/download",
}
UPLOADED_ATTACHMENT = {
    "name": "spaces/A/messages/m1/attachments/a2",
    "contentName": "photo.jpg",
    "contentType": "image/jpeg",
    "source": "UPLOADED_CONTENT",
    "attachmentDataRef": {"resourceName": "res-2"},
    "thumbnailUri": "https://example.com/thumb2",
    "downloadUri": "https://example.com/download2",
}


def test_format_message_fields():
    raw = message(
        "A",
        1,
        "2026-11-13T12:05:12.345678Z",
        text="見積を送ります",
        threadReply=True,
        lastUpdateTime="2026-11-13T12:10:00.000000Z",
        attachment=[DRIVE_ATTACHMENT, UPLOADED_ATTACHMENT],
    )
    assert format_message(raw) == {
        "id": "spaces/A/messages/m1",
        "time": "2026-11-13T21:05:12+09:00",
        "sender": "山田 太郎",
        "sender_type": "HUMAN",
        "text": "見積を送ります",
        "thread": "spaces/A/threads/t1",
        "is_reply": True,
        "edited": True,
        "attachments": [
            {
                "name": "見積書.pdf",
                "type": "application/pdf",
                "source": "DRIVE_FILE",
                "drive_file_id": "drive-file-1",
            },
            {"name": "photo.jpg", "type": "image/jpeg", "source": "UPLOADED_CONTENT"},
        ],
    }
    plain = format_message(message("A", 2, "2026-11-13T12:06:00.000000Z", text="こんにちは"))
    assert set(plain) == {"id", "time", "sender", "sender_type", "text", "thread"}


def test_textless_messages_are_kept():
    only_file = format_message(message("A", 3, ts(3), text=None, attachment=[DRIVE_ATTACHMENT]))
    assert only_file["text"] == ""
    assert only_file["attachments"][0]["name"] == "見積書.pdf"
    card = {"cardId": "c1", "card": {"header": {"title": "承認"}}}
    with_fallback = message(
        "A",
        4,
        ts(4),
        text=None,
        sender_type="BOT",
        cardsV2=[card],
        fallbackText="承認依頼が届きました",
    )
    assert format_message(with_fallback)["text"] == "承認依頼が届きました"
    card_only = message("A", 5, ts(5), text=None, sender_type="BOT", cardsV2=[card])
    assert format_message(card_only)["text"] == "（カード・本文なし）"


def test_latest_mode_returns_newest_in_ascending_order():
    chat = chat_with([message("A", i, ts(i)) for i in range(1, 6)])
    result = read_messages(chat, SP, limit=3)
    assert ids(result) == ["m3", "m4", "m5"]
    assert result["count"] == 3
    assert result["has_more"] is True
    assert result["next_since"] == ts(5)
    assert result["space"]["display_name"] == "営業定例"
    assert "notes" not in result


def test_since_mode_is_exclusive_and_ascending():
    chat = chat_with([message("A", i, ts(i)) for i in range(1, 6)])
    result = read_messages(chat, SP, since=ts(2))
    assert ids(result) == ["m3", "m4", "m5"]
    assert result["has_more"] is False
    assert result["next_since"] == ts(5)


def test_continuation_within_same_second():
    stamps = [
        "2026-11-13T12:00:00.000001Z",
        "2026-11-13T12:00:00.000002Z",
        "2026-11-13T12:00:00.000003Z",
    ]
    chat = chat_with([message("A", i + 1, t) for i, t in enumerate(stamps)])
    first = read_messages(chat, SP, since="2026-11-13T00:00:00Z", limit=2)
    assert ids(first) == ["m1", "m2"]
    assert first["next_since"] == "2026-11-13T12:00:00.000002Z"
    assert first["has_more"] is True
    second = read_messages(chat, SP, since=first["next_since"], limit=2)
    assert ids(second) == ["m3"]
    assert second["has_more"] is False


def test_limit_is_capped_with_note():
    chat = chat_with([message("A", i, ts(i)) for i in range(600)])
    result = read_messages(chat, SP, limit=1000)
    assert result["count"] == 500
    assert ids(result)[0] == "m100"
    assert result["has_more"] is True
    assert any("500" in note for note in result["notes"])


def test_invalid_limit_raises():
    for bad in (0, -1):
        with pytest.raises(ChatMcpError, match="limit"):
            read_messages(chat_with([]), SP, limit=bad)


def test_include_bots_false_skips_bots():
    msgs = [
        message("A", 1, ts(1)),
        message("A", 2, ts(2), sender="users/999", sender_name="通知", sender_type="BOT"),
        message("A", 3, ts(3)),
    ]
    with_bots = read_messages(chat_with(msgs), SP, limit=2)
    assert ids(with_bots) == ["m2", "m3"]
    assert with_bots["has_more"] is True
    people_only = read_messages(chat_with(msgs), SP, limit=2, include_bots=False)
    assert ids(people_only) == ["m1", "m3"]
    assert people_only["has_more"] is False


def test_thread_filter_reads_one_thread():
    msgs = [
        message("A", 1, ts(1)),
        message("A", 2, ts(2)),
        message("A", 3, ts(3), thread={"name": "spaces/A/threads/t1"}, threadReply=True),
    ]
    chat = chat_with(msgs)
    assert ids(read_messages(chat, SP, thread="spaces/A/threads/t1")) == ["m1", "m3"]
    assert "thread.name = spaces/A/threads/t1" in chat.calls[-1][1]["filter"]
    assert ids(read_messages(chat, SP, thread="t1")) == ["m1", "m3"]


def test_until_is_exclusive():
    chat = chat_with([message("A", i, ts(i)) for i in range(1, 6)])
    assert ids(read_messages(chat, SP, until=ts(3))) == ["m1", "m2"]


def test_nothing_new():
    chat = chat_with([message("A", i, ts(i)) for i in range(1, 6)])
    caught_up = read_messages(chat, SP, since=ts(5))
    assert caught_up["messages"] == []
    assert caught_up["count"] == 0
    assert caught_up["has_more"] is False
    assert caught_up["next_since"] == ts(5)
    assert read_messages(chat, SP, since="2026-11-14")["next_since"] == "2026-11-14T00:00:00+09:00"
    assert read_messages(chat_with([]), SP)["next_since"] is None
