"""Keyword search over a bounded period, across spaces."""

from datetime import datetime

import pytest
from fakes import FakeChat, message, space

from google_chat_mcp import search
from google_chat_mcp.errors import ChatMcpError
from google_chat_mcp.search import search_messages
from google_chat_mcp.spaces import SpaceDirectory
from google_chat_mcp.timeutil import JST

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=JST)


def make_chat() -> FakeChat:
    attachment = [
        {
            "name": "spaces/A/messages/m3/attachments/a1",
            "contentName": "見積書_v2.pdf",
            "contentType": "application/pdf",
            "source": "DRIVE_FILE",
            "driveDataRef": {"driveFileId": "drive-file-3"},
        }
    ]
    return FakeChat(
        spaces=[
            space("A", name="営業定例", last_active="2026-11-13T02:00:00.000000Z"),
            space("B", name="雑談", last_active="2026-11-12T00:00:00.000000Z"),
            space("C", name="古い部屋", last_active="2026-10-04T00:00:00.000000Z"),
        ],
        messages={
            "spaces/A": [
                message("A", 4, "2026-10-13T00:00:00.000000Z", text="ABC社の見積 再送"),
                message("A", 1, "2026-11-13T01:00:00.000000Z", text="abc社の見積"),
                message("A", 2, "2026-11-13T01:30:00.000000Z", text="abc社の請求"),
                message(
                    "A", 3, "2026-11-13T01:45:00.000000Z", text="資料です", attachment=attachment
                ),
            ],
            "spaces/B": [message("B", 5, "2026-11-12T00:00:00.000000Z", text="ＡＢＣ社 見積の件")],
            "spaces/C": [message("C", 6, "2026-10-04T00:00:00.000000Z", text="abc 見積")],
        },
    )


def run(chat=None, **kwargs):
    chat = chat or make_chat()
    return search_messages(chat, SpaceDirectory(chat), now=NOW, **kwargs)


def hit_ids(result) -> list[str]:
    return [h["id"].rsplit("/", 1)[1] for h in result["hits"]]


def test_and_match_case_and_width_insensitive():
    result = run(keywords="見積　ＡＢＣ")
    assert hit_ids(result) == ["m1", "m5"]
    assert result["hits"][0]["space"] == "spaces/A"
    assert result["hits"][0]["space_name"] == "営業定例"
    assert result["hits"][0]["text"] == "abc社の見積"
    assert result["count"] == 2
    assert result["truncated"] is False
    assert "truncated_reasons" not in result


def test_matches_attachment_names():
    assert hit_ids(run(keywords="見積書")) == ["m3"]


def test_default_window_is_30_days_and_skips_inactive_spaces():
    chat = make_chat()
    result = run(chat, keywords="見積")
    assert hit_ids(result) == ["m3", "m1", "m5"]
    assert result["scanned_spaces"] == 2
    assert result["since"] == "2026-10-14T12:00:00+09:00"
    scanned = {kw["parent"] for key, kw in chat.calls if key == "messages.list"}
    assert scanned == {"spaces/A", "spaces/B"}
    assert all(
        'createTime > "2026-10-14T12:00:00+09:00"' in kw["filter"]
        for key, kw in chat.calls
        if key == "messages.list"
    )


def test_explicit_spaces_are_resolved():
    result = run(keywords="見積", spaces=["雑談"])
    assert hit_ids(result) == ["m5"]
    assert result["scanned_spaces"] == 1


def test_hits_newest_first_and_limit_truncates():
    result = run(keywords="見積", limit=2)
    assert hit_ids(result) == ["m3", "m1"]
    assert result["truncated"] is True
    assert any("件数" in reason for reason in result["truncated_reasons"])


def test_scan_cap_is_reported(monkeypatch):
    monkeypatch.setattr(search, "SCAN_CAP_PER_SPACE", 2)
    result = run(keywords="見積")
    assert result["truncated"] is True
    assert any(
        "走査上限" in reason and "営業定例" in reason for reason in result["truncated_reasons"]
    )
    assert result["scanned_messages"] == 3


def test_empty_keywords_raise():
    with pytest.raises(ChatMcpError, match="keywords"):
        run(keywords="　 ")


def test_spaces_skipped_by_last_activity_are_noted():
    result = run(keywords="見積")
    assert any("1 件" in note and "spaces" in note for note in result["notes"])


def test_card_placeholder_text_is_not_searched():
    card = {"cardId": "c1", "card": {"header": {"title": "承認"}}}
    chat = FakeChat(
        spaces=[space("A", name="営業定例", last_active="2026-11-13T02:00:00.000000Z")],
        messages={
            "spaces/A": [
                message(
                    "A",
                    1,
                    "2026-11-13T01:00:00.000000Z",
                    text=None,
                    cardsV2=[card],
                    sender_type="BOT",
                ),
                message("A", 2, "2026-11-13T01:10:00.000000Z", text="ポイントカードの件"),
            ]
        },
    )
    assert hit_ids(run(chat, keywords="カード")) == ["m2"]
