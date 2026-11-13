"""MCP server: five read-only tools over the Google Chat API.

Each tool builds its own client from the saved token (`_chat`), so a fresh login
takes effect on the next call. Anticipated failures become `ToolError`, the only
kind of error whose message the MCP SDK passes on to Claude. Logs stay at WARNING
so message text and space names never reach the server log.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from googleapiclient.errors import HttpError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import api, auth
from .errors import ChatMcpError
from .messages import read_messages
from .search import search_messages as _search_messages
from .spaces import NameCache, SpaceDirectory
from .textutil import norm
from .timeutil import to_epoch_us, to_jst, to_rfc3339
from .unread import find_unread_spaces, read_unread

_SPACE_TYPES = ("SPACE", "GROUP_CHAT", "DIRECT_MESSAGE")

INSTRUCTIONS = (
    "Google Chat を読み取り専用で読む MCP サーバー。送信・編集・削除・既読の変更はできない。"
    "まず list_spaces でスペースを確かめ、get_messages で読む。"
    "記録をとるときは get_messages の next_since を記録に残し、次回はそれを since に渡して続きから読む。"
    "get_unread の未読は利用者が画面で開くと消えるので、記録の起点には使わない。"
)

server = MCPServer("google-chat-readonly", instructions=INSTRUCTIONS, log_level="WARNING")

# Derived DM/group names and the caller's ID, shared across tool calls for a few minutes.
_NAMES = NameCache()


def _chat() -> Any:
    """A Chat client from the saved token (opens the browser login if none is usable)."""
    return api.build_chat(auth.load_credentials(interactive=True))


def explain_http_error(err: HttpError) -> str:
    status = int(err.resp.status)
    if status == 401:
        return "認証が無効です（401）。ターミナルで `python -m google_chat_mcp.auth` を実行してログインし直してください。"
    if status == 403:
        return (
            "Google Chat API に拒否されました（403: 権限がありません）。"
            "そのスペースに参加していないか、組織の管理設定で止められている可能性があります。"
        )
    if status == 404:
        return (
            "見つかりません（404）。スペースやスレッドの ID の誤りか、参加していないスペースです。"
            "list_spaces で確認してください。"
        )
    if status == 429:
        return "Google Chat API が混み合っています（429）。少し待ってから再実行してください。"
    reason = getattr(err, "reason", "") or ""
    return f"Google Chat API がエラーを返しました（{status}{': ' + reason if reason else ''}）。"


def _guard(run: Callable[[], dict]) -> dict:
    try:
        return run()
    except ChatMcpError as exc:
        raise ToolError(str(exc)) from exc
    except HttpError as exc:
        raise ToolError(explain_http_error(exc)) from exc


@server.tool()
def list_spaces(
    space_type: str | None = None,
    active_since: str | None = None,
    name_contains: str | None = None,
    unread_only: bool = False,
) -> dict:
    """参加している Google Chat のスペース（スペース・グループチャット・DM）を、最近動いた順に一覧する。読み取り専用。

    DM とグループチャットは相手の名前で表示する（例「DM: 山田 太郎」「グループ: 佐藤、鈴木」）。
    last_active は人の最後の発言の時刻で、ボット・アプリの投稿では更新されない。
    返り値の space（spaces/…）か名前を、ほかのツールの space 引数に渡す。

    Args:
        space_type: "SPACE"・"GROUP_CHAT"・"DIRECT_MESSAGE" のどれかで絞る。
        active_since: この時刻以降に動いたものだけ（例 "2026-11-01"、"2026-11-01T09:00"。時差なしは日本時間）。
        name_contains: 名前の一部で絞る（全角半角・大文字小文字を区別しない）。
        unread_only: True なら、利用者がチャットの画面でまだ開いていない発言があるスペースだけを、
            last_read（既読の時刻）付きで返す。スペースごとに問い合わせるので少し時間がかかる。
    """

    def run() -> dict:
        if space_type and space_type not in _SPACE_TYPES:
            raise ChatMcpError(
                f"space_type は {' / '.join(_SPACE_TYPES)} のどれかを指定してください（指定値: {space_type}）。"
            )
        chat = _chat()
        directory = SpaceDirectory(chat, cache=_NAMES)
        infos = directory.all()
        if space_type:
            infos = [i for i in infos if i.type == space_type]
        if active_since:
            low = to_epoch_us(to_rfc3339(active_since))
            infos = [i for i in infos if i.last_active_ts and to_epoch_us(i.last_active_ts) >= low]
        if name_contains:
            key = norm(name_contains)
            infos = [i for i in infos if key in norm(i.display_name)]
        notes: list[str] = []
        if unread_only:
            found, notes = find_unread_spaces(chat, directory)
            last_read = {info.space: lr for info, lr in found}
            spaces = [
                {
                    **i.to_dict(),
                    "unread": True,
                    "last_read": to_jst(last_read[i.space]) if last_read[i.space] else None,
                }
                for i in infos
                if i.space in last_read
            ]
        else:
            spaces = [i.to_dict() for i in infos]
        result = {"count": len(spaces), "spaces": spaces}
        if notes:
            result["notes"] = notes
        return result

    return _guard(run)


@server.tool()
def get_messages(
    space: str,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    thread: str | None = None,
    include_bots: bool = True,
) -> dict:
    """1つのスペースのメッセージを読む。読み取り専用で、既読にもしない。

    since を渡すかどうかで、返り値と続きの読み方が変わる。
    - since あり（記録の続き）: since より後のメッセージを、古い順に最大 limit 件返す。
      has_more が true なら、まだ新しい分がある → 返り値の next_since をそのまま since に渡して続きを読む。
    - since なし（最新を見る）: 最新の limit 件を、古い順に並べて返す。
      has_older が true なら、それより古い分がある → 返り値の older_until をそのまま until に渡して読む
      （next_since を since に渡しても古い分は読めない）。
    - 記録をとるときは、最後の呼び出しの next_since を記録に残し、次回はそれをそのまま since に渡す。
      next_since は小数秒まで正確な値なので、加工せずに渡せば重複も取りこぼしも無く続きから読める。
    - 同じ時刻のメッセージは途中で分けないため、limit を少し超えて返ることがある。
    - 各メッセージ: id・time（日本時間）・sender（名前）・sender_type（HUMAN / BOT）・text・thread。
      該当するときだけ is_reply（返信）・edited（編集済み）・attachments（Drive のファイルは drive_file_id）。

    Args:
        space: スペースの名前（一部でよい）・URL・ID（spaces/…）のどれか。DM は相手の名前で指定できる。
        since: この時刻より後だけ（例 "2026-11-01"、"2026-11-01T09:00"、または前回の next_since）。
        until: この時刻より前だけ（古い分を読むときは older_until を渡す）。
        limit: 最大件数（既定 100、上限 500）。
        thread: このスレッドだけを読む（メッセージの thread の値）。
        include_bots: 既定 true（ボット・アプリの発言も含める）。false にすると除く。記録では既定のままにする。
    """

    def run() -> dict:
        chat = _chat()
        info = SpaceDirectory(chat, cache=_NAMES).resolve(space)
        return read_messages(
            chat,
            info,
            since=since,
            until=until,
            limit=limit,
            thread=thread,
            include_bots=include_bots,
        )

    return _guard(run)


@server.tool()
def search_messages(
    keywords: str,
    spaces: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
) -> dict:
    """期間を区切って、キーワードでメッセージを探す。Google 側に検索の仕組みが無いため、取得して手元で照合する。

    - keywords は空白区切りで、すべての語を含むもの（AND）を探す。全角半角・大文字小文字は区別しない。
      本文と添付ファイルの名前が対象。
    - spaces を省略すると、人の最後の発言が期間内にあるスペースを調べる（since の既定は 30日前）。
      ボットだけが投稿するスペースは対象から外れるので、探すときは spaces で指定する。
      調べなかったスペースの数は notes に書く。
    - 結果は新しい順。truncated が true なら件数上限や走査上限で打ち切っている（理由は truncated_reasons）。
      その場合は期間や語で絞って探し直す。

    Args:
        keywords: 探す語（空白区切り）。
        spaces: 調べるスペースの名前・URL・ID のリスト。省略時は期間内に動いたスペースすべて。
        since: この時刻より後だけ（既定は 30日前）。
        until: この時刻より前だけ。
        limit: 返すヒットの最大件数（既定 50）。
    """

    def run() -> dict:
        chat = _chat()
        return _search_messages(
            chat,
            SpaceDirectory(chat, cache=_NAMES),
            keywords=keywords,
            spaces=spaces,
            since=since,
            until=until,
            limit=limit,
        )

    return _guard(run)


@server.tool()
def get_members(space: str) -> dict:
    """スペースの参加者（名前・メール・種類・役割）を返す。記録の「参加者」欄に使う。

    Args:
        space: スペースの名前（一部でよい）・URL・ID のどれか。
    """

    def run() -> dict:
        chat = _chat()
        directory = SpaceDirectory(chat, cache=_NAMES)
        info = directory.resolve(space)
        members = directory.members(info.space)
        return {"space": info.to_dict(), "count": len(members), "members": members}

    return _guard(run)


@server.tool()
def get_unread(space: str | None = None, limit: int = 200, include_bots: bool = True) -> dict:
    """利用者がチャットの画面でまだ開いていない（未読の）メッセージを、スペースごとにまとめて返す。
    読むだけで既読にはしない。利用者自身の発言は数えない。

    - space を指定するとそのスペースだけ。省略すると未読のあるスペースをすべて（最近動いた順）。
    - 注意: 未読は利用者が画面で開いた時点で消える（スマホで開いた分も）。記録の続きの起点には使わず、
      記録は get_messages の next_since で続きから読む。未読は「まだ見ていない分の把握」に使う。
    - 人の最後の発言が 90日より前のスペースは調べない（ボットだけが投稿するスペースは space で指定する）。既読の記録が無いスペースは直近 90日分を未読とみなす
      （どちらも notes に書く）。
    - truncated が true なら limit で打ち切っている。

    Args:
        space: スペースの名前・URL・ID。省略時は全スペース。
        limit: 返すメッセージの合計の最大件数（既定 200）。
        include_bots: 既定 true（ボット・アプリの発言も含める）。false にすると除く。
    """

    def run() -> dict:
        chat = _chat()
        return read_unread(
            chat,
            SpaceDirectory(chat, cache=_NAMES),
            space=space,
            limit=limit,
            include_bots=include_bots,
        )

    return _guard(run)


def main() -> None:
    """Console entry point: serve over stdio."""
    server.run("stdio")


if __name__ == "__main__":
    main()
