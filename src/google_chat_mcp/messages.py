"""Reading messages of one space: the latest N, or everything after a point in time.

For record-keeping, read with `since` and keep the returned `next_since`: it is the
API's own `createTime` of the last message returned (microseconds, never rounded),
so passing it back as `since` continues with no duplicates and no gaps. Messages that
share one `createTime` are never split across two reads. Nothing is cut silently:
with `since`, `has_more` says newer messages remain (continue with `next_since`);
without `since`, `has_older` says older ones remain (page back with `older_until`
as `until`).
"""

from __future__ import annotations

from collections.abc import Iterable

from .api import build_filter, iter_messages
from .errors import ChatMcpError
from .spaces import SpaceInfo
from .timeutil import to_jst, to_rfc3339

MAX_LIMIT = 500
_CARD_ONLY = "（カード・本文なし）"
_API_PAGE_MAX = 1000


def _text(raw: dict) -> str:
    if raw.get("text"):
        return raw["text"]
    if raw.get("fallbackText"):
        return raw["fallbackText"]
    if raw.get("cardsV2") or raw.get("cards"):
        return _CARD_ONLY
    return ""


def _attachment(a: dict) -> dict:
    out = {
        "name": a.get("contentName", "（名前なし）"),
        "type": a.get("contentType", ""),
        "source": a.get("source", ""),
    }
    drive_id = a.get("driveDataRef", {}).get("driveFileId")
    if drive_id:
        out["drive_file_id"] = drive_id
    return out


def format_message(raw: dict) -> dict:
    """The fields a reader (or a record) needs; optional ones only when they apply."""
    sender = raw.get("sender", {})
    out = {
        "id": raw.get("name"),
        "time": to_jst(raw["createTime"]),
        "sender": sender.get("displayName") or sender.get("name", "（不明）"),
        "sender_type": sender.get("type", "HUMAN"),
        "text": _text(raw),
        "thread": raw.get("thread", {}).get("name"),
    }
    if raw.get("threadReply"):
        out["is_reply"] = True
    updated = raw.get("lastUpdateTime")
    if updated and updated != raw.get("createTime"):
        out["edited"] = True
    attachments = [_attachment(a) for a in raw.get("attachment", [])]
    if attachments:
        out["attachments"] = attachments
    return out


def _take(stream: Iterable[dict], limit: int) -> tuple[list[dict], bool]:
    """Up to `limit` messages plus any that share the last one's createTime, and whether
    more remain. Never splitting a timestamp keeps `>`/`<` continuation lossless."""
    picked: list[dict] = []
    for raw in stream:
        if len(picked) >= limit and raw["createTime"] != picked[-1]["createTime"]:
            return picked, True
        picked.append(raw)
    return picked, False


def read_messages(
    chat,
    space: SpaceInfo,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    thread: str | None = None,
    include_bots: bool = True,
) -> dict:
    """Without `since`: the newest `limit` messages (oldest first; `has_older` and
    `older_until` page backwards). With `since`: the first `limit` messages strictly after
    it (`has_more` and `next_since` continue forwards). Both stop before `until`."""
    if limit < 1:
        raise ChatMcpError("limit は 1 以上を指定してください。")
    notes = []
    if limit > MAX_LIMIT:
        how = (
            "続きは next_since を since に渡して読んでください。"
            if since
            else "さらに古い分は older_until を until に渡して読んでください。"
        )
        notes.append(f"limit を上限の {MAX_LIMIT} 件に丸めました。{how}")
        limit = MAX_LIMIT
    since_n = to_rfc3339(since) if since else None
    until_n = to_rfc3339(until) if until else None
    if thread and not thread.startswith("spaces/"):
        thread = f"{space.space}/threads/{thread}"
    stream = iter_messages(
        chat,
        space.space,
        filter=build_filter(since=since_n, until=until_n, thread=thread),
        order_by="createTime asc" if since_n else "createTime desc",
        page_size=min(limit + 1, _API_PAGE_MAX),
    )
    wanted = (raw for raw in stream if include_bots or raw.get("sender", {}).get("type") != "BOT")
    picked, more = _take(wanted, limit)
    result = {"space": space.to_dict()}
    if since_n:
        result |= {"has_more": more, "next_since": picked[-1]["createTime"] if picked else since_n}
    else:
        picked.reverse()
        result |= {
            "has_older": more,
            "older_until": picked[0]["createTime"] if picked else None,
            "next_since": picked[-1]["createTime"] if picked else None,
        }
    result |= {"messages": [format_message(raw) for raw in picked], "count": len(picked)}
    if notes:
        result["notes"] = notes
    return result
