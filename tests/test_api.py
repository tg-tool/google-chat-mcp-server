"""The single gateway to the Chat API: paging, the arguments we send, retries."""

from fakes import FakeChat, member, message, space

from google_chat_mcp import api
from google_chat_mcp.api import build_filter


def _ts(i: int) -> str:
    return f"2026-11-13T00:{i // 60:02d}:{i % 60:02d}.000000Z"


def test_iter_spaces_follows_all_pages():
    chat = FakeChat(spaces=[space(f"S{i:05d}") for i in range(2500)])
    got = list(api.iter_spaces(chat))
    assert [s["name"] for s in got] == [f"spaces/S{i:05d}" for i in range(2500)]
    lists = [kw for key, kw in chat.calls if key == "spaces.list"]
    assert len(lists) == 3
    assert all(kw["pageSize"] == 1000 for kw in lists)


def test_build_filter_forms():
    assert build_filter() is None
    assert build_filter(since="S") == 'createTime > "S"'
    assert build_filter(until="U") == 'createTime < "U"'
    assert (
        build_filter(since="S", until="U", thread="spaces/A/threads/T")
        == 'createTime > "S" AND createTime < "U" AND thread.name = spaces/A/threads/T'
    )


def test_iter_messages_passes_filter_and_order():
    msgs = [message("A", i, _ts(i)) for i in range(3)]
    chat = FakeChat(spaces=[space("A")], messages={"spaces/A": msgs})
    flt = f'createTime > "{_ts(0)}"'
    got = list(api.iter_messages(chat, "spaces/A", filter=flt, order_by="createTime desc"))
    assert [m["name"] for m in got] == ["spaces/A/messages/m2", "spaces/A/messages/m1"]
    key, kw = chat.calls[-1]
    assert key == "messages.list"
    assert kw["parent"] == "spaces/A"
    assert kw["filter"] == flt
    assert kw["orderBy"] == "createTime desc"


def test_iter_messages_without_filter_sends_none_and_follows_pages():
    msgs = [message("A", i, _ts(i)) for i in range(2500)]
    chat = FakeChat(spaces=[space("A")], messages={"spaces/A": msgs})
    got = list(api.iter_messages(chat, "spaces/A"))
    assert len(got) == 2500
    assert got[0]["name"] == "spaces/A/messages/m0"
    lists = [kw for key, kw in chat.calls if key == "messages.list"]
    assert len(lists) == 3
    assert all("filter" not in kw and kw["orderBy"] == "createTime asc" for kw in lists)


def test_get_space_returns_the_resource():
    chat = FakeChat(spaces=[space("A", name="営業定例"), space("B")])
    assert api.get_space(chat, "spaces/A")["displayName"] == "営業定例"
    assert chat.calls[-1][0] == "spaces.get"


def test_get_read_state_uses_me_alias():
    chat = FakeChat(
        spaces=[space("A")],
        read_states={"spaces/A": "2026-11-13T01:00:00.000000Z"},
        me="users/111",
    )
    rs = api.get_read_state(chat, "spaces/A")
    assert chat.calls[-1] == (
        "users.spaces.getSpaceReadState",
        {"name": "users/me/spaces/A/spaceReadState", "num_retries": 3},
    )
    assert rs == {
        "name": "users/111/spaces/A/spaceReadState",
        "lastReadTime": "2026-11-13T01:00:00.000000Z",
    }


def test_list_members_follows_all_pages():
    members = [member(f"users/{i}", f"Person {i}") for i in range(1500)]
    chat = FakeChat(spaces=[space("A")], members={"spaces/A": members})
    got = api.list_members(chat, "spaces/A")
    assert [m["member"]["name"] for m in got] == [f"users/{i}" for i in range(1500)]
    lists = [kw for key, kw in chat.calls if key == "members.list"]
    assert len(lists) == 2
    assert all(kw["parent"] == "spaces/A" and kw["pageSize"] == 1000 for kw in lists)


def test_execute_uses_retries():
    chat = FakeChat(
        spaces=[space("A")],
        messages={"spaces/A": [message("A", 1, _ts(1))]},
        members={"spaces/A": [member("users/1", "A")]},
    )
    list(api.iter_spaces(chat))
    api.get_space(chat, "spaces/A")
    list(api.iter_messages(chat, "spaces/A"))
    api.list_members(chat, "spaces/A")
    api.get_read_state(chat, "spaces/A")
    assert len(chat.calls) == 5
    assert all(kw["num_retries"] == 3 for _, kw in chat.calls)
