"""Time inputs/outputs (JST) and text normalization."""

from datetime import datetime

import pytest

from google_chat_mcp.errors import ChatMcpError
from google_chat_mcp.textutil import norm
from google_chat_mcp.timeutil import JST, ago, to_epoch_us, to_jst, to_rfc3339


def test_date_only_becomes_jst_midnight():
    assert to_rfc3339("2026-11-01") == "2026-11-01T00:00:00+09:00"


def test_naive_datetime_gets_jst_offset():
    assert to_rfc3339("2026-11-01T09:00") == "2026-11-01T09:00:00+09:00"
    assert to_rfc3339("2026-11-01T09:00:05.123") == "2026-11-01T09:00:05.123+09:00"


def test_aware_input_is_returned_verbatim():
    for value in ("2026-11-13T12:05:12.345678Z", "2026-11-13T21:05:12+09:00"):
        assert to_rfc3339(value) == value


def test_invalid_inputs_raise():
    for value in (
        "",
        "11/1",
        "2026-13-01",
        "2026-11-01 09:00",
        "yesterday",
        "2026-02-30T00:00:00Z",
    ):
        with pytest.raises(ChatMcpError, match="時刻の形式"):
            to_rfc3339(value)


def test_to_jst_converts_and_drops_fraction():
    assert to_jst("2026-11-13T12:05:12.345678Z") == "2026-11-13T21:05:12+09:00"


def test_epoch_us_compares_across_offsets_and_precision():
    assert to_epoch_us("2026-11-13T09:00:00+09:00") == to_epoch_us("2026-11-13T00:00:00Z")
    assert to_epoch_us("2026-11-13T00:00:00.000001Z") - to_epoch_us("2026-11-13T00:00:00Z") == 1
    assert to_epoch_us("2026-11-13T00:00:00.123456789Z") == to_epoch_us(
        "2026-11-13T00:00:00.123456Z"
    )


def test_ago():
    assert ago(30, now=datetime(2026, 11, 13, 21, 0, tzinfo=JST)) == "2026-10-14T21:00:00+09:00"


def test_norm_width_and_case():
    assert norm("ＡＢＣ ｶﾀｶﾅ") == norm("abc カタカナ")
