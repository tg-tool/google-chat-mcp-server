"""A fake Google Chat API client for tests.

It mirrors the discovery client's call chain (`chat.spaces().messages().list(...).execute()`),
the resource shapes the real API returns, paging, ordering and the filters our code sends.
It deliberately has no write methods.
"""

from __future__ import annotations

import re

import httplib2
from googleapiclient.errors import HttpError

from google_chat_mcp.timeutil import to_epoch_us

MAX_PAGE = 1000


def make_http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


def space(
    id: str,
    *,
    name: str | None = None,
    type: str = "SPACE",
    last_active: str | None = "2026-11-13T00:00:00.000000Z",
    threaded: bool = False,
) -> dict:
    d = {
        "name": f"spaces/{id}",
        "spaceType": type,
        "spaceThreadingState": "THREADED_MESSAGES" if threaded else "UNTHREADED_MESSAGES",
        "spaceHistoryState": "HISTORY_ON",
        "createTime": "2026-01-01T00:00:00.000000Z",
        "spaceUri": f"https://chat.google.com/{'dm' if type == 'DIRECT_MESSAGE' else 'room'}/{id}?cls=7",
        "membershipCount": {"joinedDirectHumanUserCount": 2},
    }
    if last_active is not None:
        d["lastActiveTime"] = last_active
    if name is not None:
        d["displayName"] = name
    return d


def message(
    space_id: str,
    n: int | str,
    ts: str,
    *,
    sender: str = "users/222",
    sender_name: str | None = "山田 太郎",
    sender_type: str = "HUMAN",
    text: str | None = "",
    **extra,
) -> dict:
    """A message resource. `text=None` omits the text fields entirely."""
    msg = {
        "name": f"spaces/{space_id}/messages/m{n}",
        "sender": {"name": sender, "type": sender_type},
        "createTime": ts,
        "thread": {"name": f"spaces/{space_id}/threads/t{n}"},
        "space": {"name": f"spaces/{space_id}"},
    }
    if sender_name is not None:
        msg["sender"]["displayName"] = sender_name
    if text is not None:
        body = text or f"message {n}"
        msg.update(text=body, formattedText=body, argumentText=body)
    msg.update(extra)
    return msg


def member(
    user: str,
    name: str | None = None,
    *,
    type: str = "HUMAN",
    role: str = "ROLE_MEMBER",
    email: str | None = None,
) -> dict:
    m = {"name": user, "type": type}
    if name is not None:
        m["displayName"] = name
    if email is not None:
        m["email"] = email
    return {
        "name": f"spaces/-/members/{user.split('/')[-1]}",
        "state": "JOINED",
        "role": role,
        "member": m,
        "createTime": "2026-01-01T00:00:00.000000Z",
    }


class _Request:
    def __init__(self, chat: FakeChat, key: str, kwargs: dict, produce):
        self._chat, self._key, self._kwargs, self._produce = chat, key, kwargs, produce

    def execute(self, num_retries: int = 0) -> dict:
        self._chat.calls.append((self._key, {**self._kwargs, "num_retries": num_retries}))
        status = self._chat.errors.get(self._key)
        if status:
            raise make_http_error(status)
        return self._produce()


def _page(items: list, key: str, page_size: int, token: str | None) -> dict:
    start = int(token or 0)
    size = min(page_size, MAX_PAGE)
    chunk = items[start : start + size]
    resp = {key: chunk} if chunk else {}
    if start + size < len(items):
        resp["nextPageToken"] = str(start + size)
    return resp


_GT = re.compile(r'create_?[tT]ime\s*>\s*"([^"]+)"')
_LT = re.compile(r'create_?[tT]ime\s*<\s*"([^"]+)"')
_THREAD = re.compile(r"thread\.name\s*=\s*(\S+)")


def _apply(messages: list[dict], filter: str | None, order_by: str | None) -> list[dict]:
    out = list(messages)
    if filter:
        if m := _GT.search(filter):
            low = to_epoch_us(m.group(1))
            out = [x for x in out if to_epoch_us(x["createTime"]) > low]
        if m := _LT.search(filter):
            high = to_epoch_us(m.group(1))
            out = [x for x in out if to_epoch_us(x["createTime"]) < high]
        if m := _THREAD.search(filter):
            out = [x for x in out if x.get("thread", {}).get("name") == m.group(1)]
    out.sort(key=lambda x: to_epoch_us(x["createTime"]), reverse=(order_by or "").endswith("desc"))
    return out


class _Messages:
    def __init__(self, chat: FakeChat):
        self._c = chat

    def list(
        self, parent, pageSize=25, pageToken=None, filter=None, orderBy=None, showDeleted=None
    ):
        kwargs = {"parent": parent, "pageSize": pageSize, "pageToken": pageToken}
        kwargs.update(
            {k: v for k, v in (("filter", filter), ("orderBy", orderBy)) if v is not None}
        )
        return _Request(
            self._c,
            "messages.list",
            kwargs,
            lambda: _page(
                _apply(self._c.messages.get(parent, []), filter, orderBy),
                "messages",
                pageSize,
                pageToken,
            ),
        )


class _Members:
    def __init__(self, chat: FakeChat):
        self._c = chat

    def list(
        self, parent, pageSize=100, pageToken=None, filter=None, showGroups=None, showInvited=None
    ):
        return _Request(
            self._c,
            "members.list",
            {"parent": parent, "pageSize": pageSize, "pageToken": pageToken},
            lambda: _page(self._c.members.get(parent, []), "memberships", pageSize, pageToken),
        )


class _Spaces:
    def __init__(self, chat: FakeChat):
        self._c = chat

    def list(self, pageSize=100, pageToken=None, filter=None):
        return _Request(
            self._c,
            "spaces.list",
            {"pageSize": pageSize, "pageToken": pageToken},
            lambda: _page(self._c.spaces_, "spaces", pageSize, pageToken),
        )

    def get(self, name):
        def produce():
            for s in self._c.spaces_:
                if s["name"] == name:
                    return s
            raise make_http_error(404)

        return _Request(self._c, "spaces.get", {"name": name}, produce)

    def messages(self):
        return _Messages(self._c)

    def members(self):
        return _Members(self._c)


class _UserSpaces:
    def __init__(self, chat: FakeChat):
        self._c = chat

    def getSpaceReadState(self, name):  # noqa: N802 - mirrors the API's method name
        def produce():
            space_name = "spaces/" + name.split("/spaces/")[1].split("/")[0]
            if space_name in self._c.read_state_errors:
                raise make_http_error(403)
            resp = {"name": name.replace("users/me/", f"{self._c.me}/", 1)}
            last = self._c.read_states.get(space_name)
            if last is not None:
                resp["lastReadTime"] = last
            return resp

        return _Request(self._c, "users.spaces.getSpaceReadState", {"name": name}, produce)


class _Users:
    def __init__(self, chat: FakeChat):
        self._c = chat

    def spaces(self):
        return _UserSpaces(self._c)


class FakeChat:
    def __init__(
        self,
        spaces=(),
        messages: dict[str, list[dict]] | None = None,
        members: dict[str, list[dict]] | None = None,
        read_states: dict[str, str | None] | None = None,
        me: str = "users/111",
        errors: dict[str, int] | None = None,
        read_state_errors: set[str] | None = None,
    ):
        self.spaces_ = list(spaces)
        self.messages = messages or {}
        self.members = members or {}
        self.read_states = read_states or {}
        self.me = me
        self.errors = errors or {}
        self.read_state_errors = read_state_errors or set()
        self.calls: list[tuple[str, dict]] = []

    def spaces(self):
        return _Spaces(self)

    def users(self):
        return _Users(self)
