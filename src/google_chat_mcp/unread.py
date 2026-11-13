"""Unread messages: those after the caller's read state, never counting their own.

Read state is only read, never changed — reading here leaves the Chat app's unread
badges as they were. Unread disappears as soon as the person opens a space in the
app (on any device), so it is for catching up, not for record-keeping: records
continue from `next_since` (see messages.py).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime
from itertools import islice
from typing import Any

from . import api
from .concurrency import parallel_map
from .errors import ChatMcpError
from .messages import format_message
from .spaces import SpaceDirectory, SpaceInfo
from .timeutil import ago, to_epoch_us, to_jst

LOOKBACK_DAYS = 90
_NEVER_OPENED = "「{name}」は既読の記録が無いため、直近 {days}日分だけを未読として扱いました。"


def _recent(
    directory: SpaceDirectory, now: datetime | None, only: list[SpaceInfo] | None
) -> tuple[list[SpaceInfo], list[str]]:
    """Spaces whose last (human) post is within LOOKBACK_DAYS.

    Chat's `lastActiveTime` is not moved by app (BOT) posts, so it cannot prove that a
    space has nothing unread; it only bounds which spaces count as recent."""
    cutoff = to_epoch_us(ago(LOOKBACK_DAYS, now=now))
    pool = directory.all() if only is None else only
    recent = [i for i in pool if i.last_active_ts and to_epoch_us(i.last_active_ts) > cutoff]
    notes = []
    if skipped := len(pool) - len(recent):
        notes.append(
            f"最後の（人の）発言が {LOOKBACK_DAYS}日より前（または記録なし）のスペース {skipped} 件は、"
            "未読を調べていません。ボットの投稿は「最後の発言」に数えられないため、"
            "ボットだけが投稿するスペースは space を指定して確かめてください。"
        )
    return recent, notes


def _unread_stream(
    chat,
    info: SpaceInfo,
    last_read: str | None,
    me: str | None,
    *,
    include_bots: bool,
    now: datetime | None,
    page_size: int = 1000,
) -> Iterator[dict]:
    since = last_read or ago(LOOKBACK_DAYS, now=now)
    stream = api.iter_messages(
        chat,
        info.space,
        filter=api.build_filter(since=since),
        order_by="createTime asc",
        page_size=page_size,
    )
    for raw in stream:
        sender = raw.get("sender", {})
        if me and sender.get("name") == me:
            continue
        if not include_bots and sender.get("type") == "BOT":
            continue
        yield raw


def _scan(
    chat,
    info: SpaceInfo,
    me: str | None,
    *,
    include_bots: bool,
    now: datetime | None,
    cap: int,
) -> tuple[str | None, list[dict]]:
    """(lastReadTime, up to `cap` unread messages) for one space."""
    last_read = api.get_read_state(chat, info.space).get("lastReadTime")
    stream = _unread_stream(
        chat,
        info,
        last_read,
        me,
        include_bots=include_bots,
        now=now,
        page_size=min(cap, 1000),
    )
    return last_read, list(islice(stream, cap))


def _me_note(me: str | None) -> list[str]:
    if me:
        return []
    return ["自分の ID を判定できなかったため、自分の発言も未読に含まれている可能性があります。"]


def find_unread_spaces(
    chat,
    directory: SpaceDirectory,
    *,
    now: datetime | None = None,
    only: list[SpaceInfo] | None = None,
    factory: Callable[[], Any] | None = None,
) -> tuple[list[tuple[SpaceInfo, str | None]], list[str]]:
    """Spaces (among `only`, default all) holding an unread message from someone else."""
    me = directory.me()
    recent, notes = _recent(directory, now, only)
    scans = parallel_map(
        lambda client, info: _scan(client, info, me, include_bots=True, now=now, cap=1),
        recent,
        chat=chat,
        factory=factory,
    )
    found = [
        (info, last_read) for info, (last_read, msgs) in zip(recent, scans, strict=True) if msgs
    ]
    return found, notes + _me_note(me)


def read_unread(
    chat,
    directory: SpaceDirectory,
    *,
    space: str | None = None,
    limit: int = 200,
    include_bots: bool = True,
    now: datetime | None = None,
    factory: Callable[[], Any] | None = None,
) -> dict:
    """Unread messages per space, oldest first within a space, newest spaces first.

    Spaces are scanned in parallel when `factory` is given; the `limit` is then applied
    in space order, and spaces left out by it are counted in `notes`."""
    if limit < 1:
        raise ChatMcpError("limit は 1 以上を指定してください。")
    me = directory.me()
    if space:
        targets, notes = [directory.resolve(space)], []
    else:
        targets, notes = _recent(directory, now, None)
    scans = parallel_map(
        lambda client, info: _scan(
            client, info, me, include_bots=include_bots, now=now, cap=limit + 1
        ),
        targets,
        chat=chat,
        factory=factory,
    )

    entries = []
    total = 0
    truncated = False
    left_out = 0
    for info, (last_read, msgs) in zip(targets, scans, strict=True):
        if not msgs:
            continue
        if total >= limit:
            left_out += 1
            continue
        if not last_read:
            notes.append(_NEVER_OPENED.format(name=info.display_name, days=LOOKBACK_DAYS))
        picked = msgs[: limit - total]
        has_more = len(msgs) > len(picked)
        truncated = truncated or has_more
        total += len(picked)
        entries.append(
            {
                "space": info.space,
                "display_name": info.display_name,
                "type": info.type,
                "url": info.url,
                "last_read": to_jst(last_read) if last_read else None,
                "messages": [format_message(raw) for raw in picked],
                "unread_count": len(picked),
                "has_more": has_more,
            }
        )
    if left_out:
        truncated = True
        notes.append(
            f"件数上限 {limit} に達したため、未読のある残り {left_out} スペースは返していません。"
            "space を指定するか limit を増やしてください。"
        )

    result = {"spaces": entries, "total": total, "truncated": truncated}
    notes += _me_note(me)
    if notes:
        result["notes"] = notes
    return result
