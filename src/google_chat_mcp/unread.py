"""Unread messages: those after the caller's read state, never counting their own.

Read state is only read, never changed — reading here leaves the Chat app's unread
badges as they were. Unread disappears as soon as the person opens a space in the
app (on any device), so it is for catching up, not for record-keeping: records
continue from `next_since` (see messages.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from . import api
from .errors import ChatMcpError
from .messages import format_message
from .spaces import SpaceDirectory, SpaceInfo
from .timeutil import ago, to_epoch_us, to_jst

LOOKBACK_DAYS = 90


def _candidates(
    chat, directory: SpaceDirectory, now: datetime | None
) -> tuple[list[tuple[SpaceInfo, str | None]], list[str]]:
    """Spaces whose last activity is newer than the read state (recent spaces only)."""
    cutoff = to_epoch_us(ago(LOOKBACK_DAYS, now=now))
    out: list[tuple[SpaceInfo, str | None]] = []
    skipped = 0
    for info in directory.all():
        if not info.last_active_ts or to_epoch_us(info.last_active_ts) <= cutoff:
            skipped += 1
            continue
        last_read = api.get_read_state(chat, info.space).get("lastReadTime")
        if last_read and to_epoch_us(info.last_active_ts) <= to_epoch_us(last_read):
            continue
        out.append((info, last_read))
    notes = []
    if skipped:
        notes.append(
            f"最後の動きが {LOOKBACK_DAYS}日より前（または記録なし）のスペース {skipped} 件は、"
            "未読を調べていません。"
        )
    return out, notes


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


def _me_note(me: str | None) -> list[str]:
    if me:
        return []
    return ["自分の ID を判定できなかったため、自分の発言も未読に含まれている可能性があります。"]


def find_unread_spaces(
    chat, directory: SpaceDirectory, *, now: datetime | None = None
) -> tuple[list[tuple[SpaceInfo, str | None]], list[str]]:
    """Spaces that hold at least one unread message from someone else, with their lastReadTime."""
    me = directory.me()
    candidates, notes = _candidates(chat, directory, now)
    found = [
        (info, last_read)
        for info, last_read in candidates
        if next(
            _unread_stream(chat, info, last_read, me, include_bots=True, now=now, page_size=10),
            None,
        )
        is not None
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
) -> dict:
    """Unread messages per space, oldest first within a space, newest spaces first."""
    if limit < 1:
        raise ChatMcpError("limit は 1 以上を指定してください。")
    me = directory.me()
    if space:
        info = directory.resolve(space)
        targets = [(info, api.get_read_state(chat, info.space).get("lastReadTime"))]
        notes: list[str] = []
    else:
        targets, notes = _candidates(chat, directory, now)

    entries = []
    total = 0
    truncated = False
    for index, (info, last_read) in enumerate(targets):
        if total >= limit:
            truncated = True
            notes.append(
                f"件数上限 {limit} に達したため、残り {len(targets) - index} スペースは調べていません。"
                "space を指定するか limit を増やしてください。"
            )
            break
        if not last_read:
            notes.append(
                f"「{info.display_name}」は既読の記録が無いため、直近 {LOOKBACK_DAYS}日分だけを未読として扱いました。"
            )
        picked: list[dict] = []
        has_more = False
        for raw in _unread_stream(chat, info, last_read, me, include_bots=include_bots, now=now):
            if total + len(picked) >= limit:
                has_more = truncated = True
                break
            picked.append(raw)
        if not picked:
            continue
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

    result = {"spaces": entries, "total": total, "truncated": truncated}
    notes += _me_note(me)
    if notes:
        result["notes"] = notes
    return result
