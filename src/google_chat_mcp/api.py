"""The only module that talks to Google.

It calls read methods only — `list`, `get` and `getSpaceReadState` — and every
request retries transient failures (429 / 5xx) a few times. Other modules go
through these functions; tests/test_readonly_guard.py enforces both rules.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from googleapiclient.discovery import build

RETRIES = 3
_PAGE = 1000


def build_chat(credentials) -> Any:
    """A Chat API v1 client authorized with read-only user credentials."""
    return build("chat", "v1", credentials=credentials, cache_discovery=False)


def build_filter(
    *, since: str | None = None, until: str | None = None, thread: str | None = None
) -> str | None:
    """The messages.list filter: strictly after `since`, strictly before `until`, one thread."""
    parts = []
    if since:
        parts.append(f'createTime > "{since}"')
    if until:
        parts.append(f'createTime < "{until}"')
    if thread:
        parts.append(f"thread.name = {thread}")
    return " AND ".join(parts) or None


def iter_spaces(chat) -> Iterator[dict]:
    """Every space the user is a member of (all pages)."""
    token = None
    while True:
        resp = chat.spaces().list(pageSize=_PAGE, pageToken=token).execute(num_retries=RETRIES)
        yield from resp.get("spaces", [])
        token = resp.get("nextPageToken")
        if not token:
            return


def get_space(chat, space: str) -> dict:
    return chat.spaces().get(name=space).execute(num_retries=RETRIES)


def iter_messages(
    chat,
    space: str,
    *,
    filter: str | None = None,
    order_by: str = "createTime asc",
    page_size: int = _PAGE,
) -> Iterator[dict]:
    """Messages of one space in `order_by` order, following pages lazily."""
    token = None
    while True:
        kwargs: dict[str, Any] = {"parent": space, "pageSize": page_size, "orderBy": order_by}
        if filter:
            kwargs["filter"] = filter
        if token:
            kwargs["pageToken"] = token
        resp = chat.spaces().messages().list(**kwargs).execute(num_retries=RETRIES)
        yield from resp.get("messages", [])
        token = resp.get("nextPageToken")
        if not token:
            return


def list_members(chat, space: str) -> list[dict]:
    """All memberships of one space (humans and apps)."""
    members: list[dict] = []
    token = None
    while True:
        resp = (
            chat.spaces()
            .members()
            .list(parent=space, pageSize=_PAGE, pageToken=token)
            .execute(num_retries=RETRIES)
        )
        members.extend(resp.get("memberships", []))
        token = resp.get("nextPageToken")
        if not token:
            return members


def get_read_state(chat, space: str) -> dict:
    """The caller's read state of one space; the returned name carries the caller's user ID."""
    name = f"users/me/{space}/spaceReadState"
    return chat.users().spaces().getSpaceReadState(name=name).execute(num_retries=RETRIES)
