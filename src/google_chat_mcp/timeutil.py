"""Time handling: inputs in JST-friendly forms, outputs in JST, exact comparisons.

The Chat API returns timestamps such as `2026-11-13T12:05:12.345678Z` (UTC,
microseconds). Values that are already timezone-aware are passed to the API
verbatim, so a `next_since` taken from a message is never rounded.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .errors import ChatMcpError

JST = ZoneInfo("Asia/Tokyo")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?(Z|[+-]\d{2}:\d{2})?$"
)
_EXAMPLES = "（例: 2026-11-01 / 2026-11-01T09:00 / 2026-11-01T09:00:00+09:00）"


def _bad(value: str) -> ChatMcpError:
    return ChatMcpError(f"時刻の形式が読めません: '{value}'{_EXAMPLES}")


def _tz(offset: str | None) -> timezone | ZoneInfo:
    if offset is None:
        return JST
    if offset == "Z":
        return UTC
    sign = 1 if offset[0] == "+" else -1
    hours, minutes = int(offset[1:3]), int(offset[4:6])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse(value: str) -> tuple[datetime, bool]:
    """(aware datetime, whether the input carried its own offset). Raises ChatMcpError."""
    try:
        if _DATE_RE.match(value):
            d = date.fromisoformat(value)
            return datetime(d.year, d.month, d.day, tzinfo=JST), False
        m = _DATETIME_RE.match(value)
        if not m:
            raise _bad(value)
        day, hh, mm, ss, frac, offset = m.groups()
        d = date.fromisoformat(day)
        micro = int((frac or "")[:6].ljust(6, "0"))
        dt = datetime(
            d.year, d.month, d.day, int(hh), int(mm), int(ss or 0), micro, tzinfo=_tz(offset)
        )
        return dt, offset is not None
    except ValueError as exc:
        raise _bad(value) from exc


def to_rfc3339(value: str) -> str:
    """Normalize an input time for the API: date → 0:00 JST, naive → JST, aware → unchanged."""
    _, aware = _parse(value)
    if aware:
        return value
    if _DATE_RE.match(value):
        return f"{value}T00:00:00+09:00"
    day, hh, mm, ss, frac, _ = _DATETIME_RE.match(value).groups()
    return f"{day}T{hh}:{mm}:{ss or '00'}{'.' + frac if frac else ''}+09:00"


def to_epoch_us(ts: str) -> int:
    """Exact integer microseconds since the epoch (for comparing timestamps)."""
    dt, _ = _parse(ts)
    return (dt - _EPOCH) // timedelta(microseconds=1)


def to_jst(ts: str) -> str:
    """Display form in JST, to the second: '2026-11-13T21:05:12+09:00'."""
    dt, _ = _parse(ts)
    return dt.astimezone(JST).replace(microsecond=0).isoformat()


def ago(days: int, *, now: datetime | None = None) -> str:
    """The JST time `days` days before now, to the second."""
    now = (now or datetime.now(JST)).astimezone(JST)
    return (now - timedelta(days=days)).replace(microsecond=0).isoformat()
