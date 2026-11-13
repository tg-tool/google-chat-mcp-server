"""Spaces: list them with readable names, resolve what the caller typed, list members.

DMs and unnamed group chats have no name in the API, so they are named after the
other people in them ("DM: 山田 太郎", "グループ: 佐藤、鈴木"). The caller's own user
ID comes from the read-state API, whose response name resolves `users/me`.

Derived names and the caller's ID cost extra calls, so they live in a NameCache that
the server shares across tool calls for a few minutes. An ID or URL is resolved with
one `spaces.get` instead of listing every space.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import islice

from googleapiclient.errors import HttpError

from . import api
from .errors import ChatMcpError
from .textutil import norm
from .timeutil import to_epoch_us, to_jst

_REF_RE = re.compile(r"^spaces/[A-Za-z0-9_-]+$")
_URL_RE = re.compile(r"(?:/room/|/dm/|#chat/space/|#chat/dm/)([A-Za-z0-9_-]+)")
_PREFIXES = ("DM: ", "グループ: ")
_ROLES = {"ROLE_MANAGER": "管理者", "ROLE_MEMBER": "メンバー"}
_MAX_CANDIDATES = 10
_SENDER_LOOKUP_LIMIT = 20
_ME_ATTEMPTS = 3
CACHE_TTL_SECONDS = 600


class NameCache:
    """Derived space names and the caller's ID, kept for `ttl_seconds`."""

    def __init__(
        self, ttl_seconds: float = CACHE_TTL_SECONDS, clock: Callable[[], float] = time.monotonic
    ):
        self._ttl = ttl_seconds
        self._clock = clock
        self._data: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        value, expires = hit
        if self._clock() >= expires:
            self._data.pop(key, None)
            return None
        return value

    def put(self, key: str, value: str) -> None:
        self._data[key] = (value, self._clock() + self._ttl)


@dataclass(frozen=True)
class SpaceInfo:
    space: str
    display_name: str
    type: str
    threaded: bool
    last_active_ts: str | None
    url: str | None

    def to_dict(self) -> dict:
        return {
            "space": self.space,
            "display_name": self.display_name,
            "type": self.type,
            "threaded": self.threaded,
            "last_active": to_jst(self.last_active_ts) if self.last_active_ts else None,
            "url": self.url,
        }


def parse_space_ref(ref: str) -> str | None:
    """`spaces/ID` or a Chat URL → `spaces/ID`; anything else (names, bare IDs) → None."""
    ref = ref.strip()
    if _REF_RE.match(ref):
        return ref
    if ref.startswith(("http://", "https://")) and (m := _URL_RE.search(ref)):
        return f"spaces/{m.group(1)}"
    return None


def _bare(display_name: str) -> str:
    for prefix in _PREFIXES:
        if display_name.startswith(prefix):
            return display_name[len(prefix) :]
    return display_name


def _one(ref: str, matches: list[SpaceInfo]) -> SpaceInfo | None:
    """The single match, None for no match; several matches are an error listing them."""
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        shown = "、".join(f"{i.display_name}（{i.space}）" for i in matches[:_MAX_CANDIDATES])
        more = (
            f" ほか {len(matches) - _MAX_CANDIDATES} 件" if len(matches) > _MAX_CANDIDATES else ""
        )
        raise ChatMcpError(
            f"「{ref}」に当たるスペースが {len(matches)} 件あります。"
            f"名前を詳しくするか、ID・URL で指定してください: {shown}{more}"
        )
    return None


class SpaceDirectory:
    """The caller's spaces for the duration of one tool call (names may come from `cache`)."""

    def __init__(self, chat, cache: NameCache | None = None):
        self._chat = chat
        self._cache = cache or NameCache()
        self._raw: list[dict] | None = None
        self._all: list[SpaceInfo] | None = None
        self._me_tried = False

    def _spaces_raw(self) -> list[dict]:
        if self._raw is None:
            self._raw = list(api.iter_spaces(self._chat))
        return self._raw

    def _me_candidates(self, hint: str | None) -> Iterator[str]:
        if hint:
            yield hint
        for raw in self._spaces_raw():
            if raw["name"] != hint:
                yield raw["name"]

    def me(self, space_hint: str | None = None) -> str | None:
        """The caller's `users/ID`, or None when it cannot be determined.

        Asks the read state of `space_hint` first (no listing needed), then of a few
        listed spaces, so one inaccessible space does not break every tool."""
        cached = self._cache.get("me")
        if cached or self._me_tried:
            return cached
        self._me_tried = True
        for space in islice(self._me_candidates(space_hint), _ME_ATTEMPTS):
            try:
                name = api.get_read_state(self._chat, space).get("name", "")
            except HttpError:
                continue
            if name and not name.startswith("users/me/"):
                me = name.split("/spaces/")[0]
                self._cache.put("me", me)
                return me
        return None

    def all(self) -> list[SpaceInfo]:
        """Every space, most recently active first (never-active ones last)."""
        if self._all is None:
            infos = [self._info(raw) for raw in self._spaces_raw()]
            infos.sort(
                key=lambda i: to_epoch_us(i.last_active_ts) if i.last_active_ts else -1,
                reverse=True,
            )
            self._all = infos
        return self._all

    def _info(self, raw: dict) -> SpaceInfo:
        kind = raw.get("spaceType", "SPACE")
        return SpaceInfo(
            space=raw["name"],
            display_name=raw.get("displayName") or self._derived_name(raw["name"], kind),
            type=kind,
            threaded=raw.get("spaceThreadingState") == "THREADED_MESSAGES",
            last_active_ts=raw.get("lastActiveTime"),
            url=raw.get("spaceUri"),
        )

    def _derived_name(self, space: str, kind: str) -> str:
        if kind not in ("DIRECT_MESSAGE", "GROUP_CHAT"):
            return "（名前なし）"
        key = f"name:{space}"
        cached = self._cache.get(key)
        if cached:
            return cached
        name = self._name_from_people(space, kind)
        self._cache.put(key, name)
        return name

    def _name_from_people(self, space: str, kind: str) -> str:
        me = self.me(space_hint=space)
        others = [
            m.get("member", {})
            for m in api.list_members(self._chat, space)
            if m.get("member", {}).get("name") != me
        ]
        others.sort(key=lambda who: who.get("type") != "HUMAN")
        names = [who["displayName"] for who in others if who.get("displayName")]
        if not names and kind == "DIRECT_MESSAGE":
            names = self._latest_sender_name(space, me)
        label = "、".join(names) if names else "（名前不明）"
        return f"DM: {label}" if kind == "DIRECT_MESSAGE" else f"グループ: {label}"

    def _latest_sender_name(self, space: str, me: str | None) -> list[str]:
        """For a DM with an app whose membership carries no name: the newest other sender."""
        recent = api.iter_messages(self._chat, space, order_by="createTime desc", page_size=10)
        for msg in islice(recent, _SENDER_LOOKUP_LIMIT):
            sender = msg.get("sender", {})
            if sender.get("name") != me and sender.get("displayName"):
                return [sender["displayName"]]
        return []

    def resolve(self, ref: str) -> SpaceInfo:
        """A space from an ID, a URL or a (part of a) name; ambiguity is an error with candidates."""
        sid = parse_space_ref(ref)
        if sid is not None:
            for info in self._all or []:
                if info.space == sid:
                    return info
            try:
                return self._info(api.get_space(self._chat, sid))
            except HttpError as exc:
                if int(exc.resp.status) != 404:
                    raise
                raise ChatMcpError(
                    f"参加しているスペースに {sid} が見つかりません（ID・URL の誤りか、参加していないスペース）。"
                    "list_spaces で一覧を確認してください。"
                ) from exc
        infos = self.all()
        key = norm(ref.strip())
        if not key:
            raise ChatMcpError(
                "スペースが指定されていません。名前・URL・ID のどれかを指定してください（一覧は list_spaces）。"
            )
        exact = [i for i in infos if key in (norm(i.display_name), norm(_bare(i.display_name)))]
        partial = [i for i in infos if key in norm(i.display_name)]
        found = _one(ref, exact) or _one(ref, partial)
        if found:
            return found
        bare_id = f"spaces/{ref.strip()}"
        for info in infos:
            if info.space == bare_id:
                return info
        raise ChatMcpError(
            f"「{ref}」に当たるスペースがありません。list_spaces で一覧を確認してください。"
        )

    def members(self, space: str) -> list[dict]:
        """Members with name, type, role (Japanese) and email when present; people first."""
        out = []
        for m in api.list_members(self._chat, space):
            who = m.get("member", {})
            role = m.get("role")
            entry = {
                "name": who.get("displayName") or "（名前なし）",
                "type": who.get("type", "HUMAN"),
                "role": _ROLES.get(role, role or "メンバー"),
            }
            if who.get("email"):
                entry["email"] = who["email"]
            out.append(entry)
        out.sort(key=lambda e: e["type"] != "HUMAN")
        return out
