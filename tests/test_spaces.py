"""Spaces: listing, readable names for DMs, resolving what the caller typed, members."""

import pytest
from fakes import FakeChat, member, message, space

from google_chat_mcp.errors import ChatMcpError
from google_chat_mcp.spaces import NameCache, SpaceDirectory, parse_space_ref

ME = "users/111"


def make_chat() -> FakeChat:
    return FakeChat(
        me=ME,
        spaces=[
            space("AAAAbbb123", name="営業定例", last_active="2026-11-13T03:00:00.000000Z"),
            space("BBBBccc234", name="エイギョウ定例", last_active="2026-11-12T03:00:00.000000Z"),
            space("DDDDeee456", type="DIRECT_MESSAGE", last_active="2026-11-13T05:00:00.000000Z"),
            space("EEEEfff567", type="DIRECT_MESSAGE", last_active="2026-11-10T00:00:00.000000Z"),
            space("GGGGhhh678", type="GROUP_CHAT", last_active="2026-11-11T00:00:00.000000Z"),
            space("HHHHiii789", name="雑談", last_active=None, threaded=True),
        ],
        members={
            "spaces/DDDDeee456": [member(ME, "利用者"), member("users/222", "山田 太郎")],
            "spaces/EEEEfff567": [member(ME, "利用者"), member("users/999", type="BOT")],
            "spaces/GGGGhhh678": [
                member(ME, "利用者"),
                member("users/333", "佐藤"),
                member("users/444", "鈴木"),
            ],
        },
        messages={
            "spaces/EEEEfff567": [
                message(
                    "EEEEfff567", 1, "2026-11-09T00:00:00.000000Z", sender=ME, sender_name="利用者"
                ),
                message(
                    "EEEEfff567",
                    2,
                    "2026-11-10T00:00:00.000000Z",
                    sender="users/999",
                    sender_name="Drive",
                    sender_type="BOT",
                ),
            ]
        },
    )


def names(infos) -> list[str]:
    return [i.display_name for i in infos]


def test_parse_space_ref_forms():
    assert parse_space_ref("spaces/AAAAbbb123") == "spaces/AAAAbbb123"
    assert parse_space_ref("https://chat.google.com/room/AAAAbbb123?cls=7") == "spaces/AAAAbbb123"
    assert (
        parse_space_ref("https://chat.google.com/room/AAAAbbb123/ThReAd99?cls=7")
        == "spaces/AAAAbbb123"
    )
    assert parse_space_ref("https://chat.google.com/dm/DDDDeee456") == "spaces/DDDDeee456"
    assert (
        parse_space_ref("https://mail.google.com/chat/u/0/#chat/space/AAAAbbb123")
        == "spaces/AAAAbbb123"
    )
    assert (
        parse_space_ref("https://mail.google.com/chat/u/0/#chat/dm/DDDDeee456")
        == "spaces/DDDDeee456"
    )
    assert parse_space_ref("営業定例") is None
    assert parse_space_ref("AAAAbbb123") is None


def test_me_from_read_state():
    assert SpaceDirectory(make_chat()).me() == ME
    assert SpaceDirectory(FakeChat()).me() is None


def test_all_sorted_and_dm_named_after_counterpart():
    infos = SpaceDirectory(make_chat()).all()
    assert names(infos) == [
        "DM: 山田 太郎",
        "営業定例",
        "エイギョウ定例",
        "グループ: 佐藤、鈴木",
        "DM: Drive",
        "雑談",
    ]
    dm = infos[0].to_dict()
    assert dm == {
        "space": "spaces/DDDDeee456",
        "display_name": "DM: 山田 太郎",
        "type": "DIRECT_MESSAGE",
        "threaded": False,
        "last_active": "2026-11-13T14:00:00+09:00",
        "url": "https://chat.google.com/dm/DDDDeee456?cls=7",
    }
    assert infos[-1].to_dict()["last_active"] is None
    assert infos[-1].threaded is True


def test_bot_only_dm_named_from_latest_sender():
    info = SpaceDirectory(make_chat()).resolve("spaces/EEEEfff567")
    assert info.display_name == "DM: Drive"


def test_group_chat_without_name_lists_members():
    info = SpaceDirectory(make_chat()).resolve("spaces/GGGGhhh678")
    assert info.display_name == "グループ: 佐藤、鈴木"


def test_resolve_by_id_url_and_exact_name():
    d = SpaceDirectory(make_chat())
    assert d.resolve("spaces/AAAAbbb123").display_name == "営業定例"
    assert d.resolve("https://chat.google.com/room/AAAAbbb123?cls=7").display_name == "営業定例"
    assert d.resolve("営業定例").space == "spaces/AAAAbbb123"
    assert d.resolve(" 営業定例 ").space == "spaces/AAAAbbb123"


def test_resolve_dm_by_person_name_and_width_insensitive():
    d = SpaceDirectory(make_chat())
    assert d.resolve("山田").space == "spaces/DDDDeee456"
    assert d.resolve("山田 太郎").space == "spaces/DDDDeee456"
    assert d.resolve("ｴｲｷﾞｮｳ定例").space == "spaces/BBBBccc234"


def test_resolve_ambiguous_lists_candidates():
    with pytest.raises(ChatMcpError) as excinfo:
        SpaceDirectory(make_chat()).resolve("定例")
    msg = str(excinfo.value)
    assert "営業定例（spaces/AAAAbbb123）" in msg
    assert "エイギョウ定例（spaces/BBBBccc234）" in msg


def test_resolve_unknown_points_to_list_spaces():
    with pytest.raises(ChatMcpError, match="list_spaces"):
        SpaceDirectory(make_chat()).resolve("存在しない部屋")
    with pytest.raises(ChatMcpError, match="list_spaces"):
        SpaceDirectory(make_chat()).resolve("spaces/ZZZZzzz000")


def test_resolve_bare_id_of_listed_space():
    assert SpaceDirectory(make_chat()).resolve("AAAAbbb123").display_name == "営業定例"


def test_members_formatting():
    chat = FakeChat(
        me=ME,
        spaces=[space("AAAAbbb123", name="営業定例")],
        members={
            "spaces/AAAAbbb123": [
                member("users/999", type="BOT"),
                member("users/222", "山田 太郎", role="ROLE_MANAGER", email="yamada@example.com"),
                member("users/333", "佐藤", email="sato@example.com"),
            ]
        },
    )
    assert SpaceDirectory(chat).members("spaces/AAAAbbb123") == [
        {"name": "山田 太郎", "type": "HUMAN", "role": "管理者", "email": "yamada@example.com"},
        {"name": "佐藤", "type": "HUMAN", "role": "メンバー", "email": "sato@example.com"},
        {"name": "（名前なし）", "type": "BOT", "role": "メンバー"},
    ]


def test_resolve_by_id_or_url_skips_the_full_listing():
    chat = make_chat()
    d = SpaceDirectory(chat)
    assert d.resolve("spaces/DDDDeee456").display_name == "DM: 山田 太郎"
    assert d.resolve("https://chat.google.com/room/AAAAbbb123?cls=7").display_name == "営業定例"
    keys = [key for key, _ in chat.calls]
    assert "spaces.list" not in keys
    assert keys.count("spaces.get") == 2


def test_shared_cache_skips_repeat_lookups():
    chat = make_chat()
    cache = NameCache()
    first = names(SpaceDirectory(chat, cache=cache).all())
    before = len(chat.calls)
    second = names(SpaceDirectory(chat, cache=cache).all())
    assert second == first
    assert [key for key, _ in chat.calls[before:]] == ["spaces.list"]


def test_cache_entries_expire():
    now = [1000.0]
    cache = NameCache(ttl_seconds=600, clock=lambda: now[0])
    cache.put("name:spaces/X", "DM: 山田 太郎")
    now[0] += 599
    assert cache.get("name:spaces/X") == "DM: 山田 太郎"
    now[0] += 2
    assert cache.get("name:spaces/X") is None


def test_me_falls_back_when_a_read_state_fails():
    chat = make_chat()
    chat.read_state_errors = {"spaces/AAAAbbb123"}
    assert SpaceDirectory(chat).me() == ME
