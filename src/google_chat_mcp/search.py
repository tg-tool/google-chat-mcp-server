"""Keyword search. Google offers no message search to regular users, so this reads
messages within a bounded period and matches them locally.

Every keyword must appear (AND), in the text or an attachment name, ignoring case
and full-/half-width differences. Anything not examined — beyond the hit limit or a
per-space scan cap — is reported in `truncated_reasons`, never dropped silently.
"""

from __future__ import annotations

from datetime import datetime

from .api import build_filter, iter_messages
from .errors import ChatMcpError
from .messages import format_message
from .spaces import SpaceDirectory
from .textutil import norm
from .timeutil import ago, to_epoch_us, to_rfc3339

DEFAULT_DAYS = 30
SCAN_CAP_PER_SPACE = 5000


def _haystack(formatted: dict) -> str:
    names = " ".join(a["name"] for a in formatted.get("attachments", []))
    return norm(f"{formatted['text']} {names}")


def search_messages(
    chat,
    directory: SpaceDirectory,
    *,
    keywords: str,
    spaces: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    now: datetime | None = None,
) -> dict:
    words = keywords.split()
    if not words:
        raise ChatMcpError("keywords が空です。探したい語を空白区切りで指定してください。")
    if limit < 1:
        raise ChatMcpError("limit は 1 以上を指定してください。")
    terms = [norm(w) for w in words]
    since_n = to_rfc3339(since) if since else ago(DEFAULT_DAYS, now=now)
    until_n = to_rfc3339(until) if until else None

    notes: list[str] = []
    if spaces:
        targets = list({info.space: info for info in map(directory.resolve, spaces)}.values())
    else:
        low = to_epoch_us(since_n)
        everything = directory.all()
        targets = [
            info
            for info in everything
            if info.last_active_ts and to_epoch_us(info.last_active_ts) > low
        ]
        if len(everything) > len(targets):
            notes.append(
                f"最後の（人の）発言が期間より前のスペース {len(everything) - len(targets)} 件は"
                "調べていません。ボットの投稿は「最後の発言」に数えられないため、"
                "ボットだけが投稿するスペースを探すときは spaces で指定してください。"
            )

    flt = build_filter(since=since_n, until=until_n)
    found: list[tuple[int, dict]] = []
    reasons: list[str] = []
    scanned = 0
    for info in targets:
        seen = 0
        for raw in iter_messages(chat, info.space, filter=flt, order_by="createTime desc"):
            if seen >= SCAN_CAP_PER_SPACE:
                reasons.append(
                    f"走査上限: 「{info.display_name}」は新しい順に {SCAN_CAP_PER_SPACE} 件までしか"
                    "調べていません。期間（since・until）を狭めて再検索してください。"
                )
                break
            seen += 1
            formatted = format_message(raw)
            if all(t in _haystack(formatted) for t in terms):
                hit = {"space": info.space, "space_name": info.display_name, **formatted}
                found.append((to_epoch_us(raw["createTime"]), hit))
        scanned += seen

    found.sort(key=lambda pair: pair[0], reverse=True)
    if len(found) > limit:
        reasons.insert(
            0,
            f"件数上限: {len(found)} 件当たりましたが、新しい順に {limit} 件だけ返しています。"
            "limit を増やすか、語や期間で絞ってください。",
        )
        found = found[:limit]
    result = {
        "keywords": words,
        "since": since_n,
        "until": until_n,
        "hits": [hit for _, hit in found],
        "count": len(found),
        "scanned_spaces": len(targets),
        "scanned_messages": scanned,
        "truncated": bool(reasons),
    }
    if reasons:
        result["truncated_reasons"] = reasons
    if notes:
        result["notes"] = notes
    return result
