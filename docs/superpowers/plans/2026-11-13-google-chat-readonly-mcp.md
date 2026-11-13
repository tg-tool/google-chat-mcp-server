# 読み取り専用 Google Chat MCP 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Code から Google Chat を読み取り専用で読み、記録できる MCP サーバー（5ツール）を作り、登録して実物で確認する。

**Architecture:** Google API に触れるのは `api.py` の1か所だけにし、その上に「時刻」「スペース」「メッセージ」「検索」「未読」の各モジュールを重ね、`server.py` が MCP のツールとして束ねる。各モジュールは独自の例外 `ChatMcpError` を投げ、`server.py` が MCP SDK の `ToolError` に変換して Claude に文面を届ける。テストは偽の Chat API（`tests/fakes.py`）で、ネットにつながずに行う。

**Tech Stack:** Python 3.14（要件は 3.12 以上）・`mcp` 2.x（`MCPServer`）・google-api-python-client・google-auth・google-auth-oauthlib・pytest・ruff

**Spec:** `docs/superpowers/specs/2026-11-13-google-chat-readonly-mcp-design.md`

## Global Constraints

- スコープは次の4つだけ、この順で `auth.SCOPES`（tuple）に置く: `https://www.googleapis.com/auth/chat.spaces.readonly`・`https://www.googleapis.com/auth/chat.messages.readonly`・`https://www.googleapis.com/auth/chat.memberships.readonly`・`https://www.googleapis.com/auth/chat.users.readstate.readonly`
- Google のクライアントに触れるのは `src/google_chat_mcp/api.py` だけ。呼ぶメソッドは `spaces`・`messages`・`members`・`users`・`list`・`get`・`getSpaceReadState`・`execute` だけ。`execute(num_retries=3)`
- 依存: `mcp>=2.3,<3`・`google-api-python-client>=2.200`・`google-auth>=2.60`・`google-auth-oauthlib>=1.5`・`google-auth-httplib2>=0.2`。開発用 extra `dev`: `pytest>=9`・`ruff>=0.17`。`requires-python = ">=3.12"`
- 開発環境は `/opt/homebrew/bin/python3.14 -m venv .venv`。ruff 設定は line-length 100・select `E,F,W,I,B,UP`・ignore `E501`
- MCP サーバー名 `google-chat-readonly`、`MCPServer(..., log_level="WARNING")`。メッセージ本文・スペース名・トークンを print もログもしない
- 認証情報の既定: `~/.config/google-chat-mcp/credentials.json`・`token.json`。環境変数 `GOOGLE_CHAT_MCP_CREDENTIALS`・`GOOGLE_CHAT_MCP_TOKEN` で上書き。token は 0600、フォルダは 0700
- 出力の時刻は日本時間 `YYYY-MM-DDTHH:MM:SS+09:00`（秒まで）。**`next_since` だけは API の `createTime` をそのまま**（例 `2026-11-13T12:05:12.345678Z`）
- 既定値と上限: `get_messages` limit 100・上限 500／`search_messages` limit 50・期間 30日・スペースごとの走査上限 5000／`get_unread` limit 200・遡り 90日
- 利用者向けのエラー文は日本語。各モジュールは `ChatMcpError` を投げ、`server.py` だけが `ToolError` に変換する
- 公開リポジトリ: 組織名・アカウント・クラウドのプロジェクト ID・実測値・利用者のホームの実パスを入れない。commit のメールはリポジトリ設定の noreply（設定済み）。commit の末尾に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. **同じ秒に複数のメッセージ（ボットの連投など）が `limit` の境目にかかる** → `next_since` で続きを読むと、残りがちょうど1回ずつ返る（Task 5 `test_continuation_within_same_second`）
2. **URL にクエリ（`?cls=11`）やスレッドの続き（`/room/ID/THREAD`）が付いている** → 正しく ID を取り出す（Task 4 `test_parse_space_ref_forms`）
3. **DM を相手の名前の一部（「山田」）や全角半角の違いで指定する** → 一意なら解決し、複数なら候補を返す（Task 4 `test_resolve_dm_by_person_name_and_width_insensitive`）
4. **本文の無いメッセージ（添付だけ・ボットのカード）** → 落とさず、添付またはカードの代替文で返す（Task 5 `test_textless_messages_are_kept`）
5. **一度も開いていないスペース（`lastReadTime` が無い）** → 遡りを 90日に限り、その旨を `notes` に書く。自分の発言は数えない（Task 7 `test_never_opened_space_is_bounded_and_noted`）

---

### Task 1: 土台と認証

**Files:**
- Create: `pyproject.toml`・`.gitignore`（Sheets MCP と同じ内容＋`.pytest_cache/`）・`LICENSE`（MIT・`Copyright (c) 2026 tg-tool`）
- Create: `src/google_chat_mcp/__init__.py`・`src/google_chat_mcp/errors.py`・`src/google_chat_mcp/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces: `errors.ChatMcpError(Exception)`・`errors.AuthRequiredError(ChatMcpError)`
- Produces: `auth.SCOPES: tuple[str, ...]`・`auth.credentials_path() -> Path`・`auth.token_path() -> Path`・`auth.has_required_scopes(granted: Iterable[str] | None) -> bool`・`auth.write_token(path: Path, data: str) -> None`・`auth.load_credentials(*, interactive: bool = True, timeout_seconds: int = 300) -> Credentials`・`auth.login(*, timeout_seconds: int = 600) -> Credentials`・`auth.main() -> None`
- `pyproject.toml` の scripts: `google-chat-mcp = "google_chat_mcp.server:main"`・`google-chat-mcp-auth = "google_chat_mcp.auth:main"`

- [ ] **Step 1: 開発環境を作る**

Run: `/opt/homebrew/bin/python3.14 -m venv .venv && .venv/bin/pip install -q -e ".[dev]" && .venv/bin/python -c "import mcp.server.mcpserver, googleapiclient, google_auth_oauthlib; print('ok')"`
Expected: `ok`

- [ ] **Step 2: 失敗するテストを書く**（`auth` の `Credentials`・`InstalledAppFlow`・`Request` はモジュール直下で import し、テストで差し替える）

```python
EXPECTED = (
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/chat.users.readstate.readonly",
)
def test_scopes_are_exactly_the_four_readonly(): assert auth.SCOPES == EXPECTED
def test_paths_default_and_env_override(monkeypatch, tmp_path):
    # 既定は ~/.config/google-chat-mcp/{credentials,token}.json、環境変数があればそちら
def test_has_required_scopes():
    assert auth.has_required_scopes([*EXPECTED, "openid"]) is True
    assert auth.has_required_scopes(EXPECTED[:3]) is False
    assert auth.has_required_scopes(None) is False
def test_write_token_permissions(tmp_path):
    # 新規: ファイル 0o600・親フォルダ 0o700／既存 0o644 を上書き → 0o600 に戻る
def test_load_uses_valid_token_without_login(...):        # 有効・4スコープ → ログインを呼ばない
def test_load_refreshes_expired_token_and_saves(...):     # 期限切れ＋refresh_token → refresh、token を書き直す
def test_load_logs_in_again_when_scopes_missing(...):     # 3スコープの token → ログインを SCOPES で呼び、保存
def test_refresh_failure_falls_back_to_login(...):        # RefreshError → ログイン
def test_broken_token_file_falls_back_to_login(...):      # JSON が壊れている → ログイン
def test_noninteractive_raises_auth_required(...):        # 文面に "python -m google_chat_mcp.auth"
def test_missing_client_file_raises_with_path(...):       # 文面に credentials_path() の文字列
```

- [ ] **Step 3: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_auth.py -q` / Expected: FAIL（`google_chat_mcp.auth` が無い）
- [ ] **Step 4: `errors.py` と `auth.py` を実装する** — token は `Credentials.from_authorized_user_file(str(path))`（scopes を渡さない＝ファイルの許可スコープで判定）。ログインは `InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0, timeout_seconds=..., open_browser=True)`。`main()` は `login()` を呼び「認証 OK（スコープ 4/4）」だけを表示する（秘密は出さない）
- [ ] **Step 5: 通ることを確かめる** — Run: `.venv/bin/pytest tests/test_auth.py -q && .venv/bin/ruff check src tests` / Expected: all passed・ruff 指摘なし
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: scaffold package and read-only OAuth handling"`

---

### Task 2: 時刻と文字の正規化

**Files:**
- Create: `src/google_chat_mcp/timeutil.py`・`src/google_chat_mcp/textutil.py`
- Test: `tests/test_timeutil.py`

**Interfaces:**
- Produces: `timeutil.JST`・`timeutil.to_rfc3339(value: str) -> str`・`timeutil.to_epoch_us(ts: str) -> int`・`timeutil.to_jst(ts: str) -> str`・`timeutil.ago(days: int, *, now: datetime | None = None) -> str`
- Produces: `textutil.norm(s: str) -> str`（NFKC ＋ casefold）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_date_only_becomes_jst_midnight(): assert to_rfc3339("2026-11-01") == "2026-11-01T00:00:00+09:00"
def test_naive_datetime_gets_jst_offset():
    assert to_rfc3339("2026-11-01T09:00") == "2026-11-01T09:00:00+09:00"
    assert to_rfc3339("2026-11-01T09:00:05.123") == "2026-11-01T09:00:05.123+09:00"
def test_aware_input_is_returned_verbatim():
    for v in ("2026-11-13T12:05:12.345678Z", "2026-11-13T21:05:12+09:00"): assert to_rfc3339(v) == v
def test_invalid_inputs_raise():
    for v in ("", "11/1", "2026-13-01", "2026-11-01 09:00", "yesterday"):
        with pytest.raises(ChatMcpError, match="時刻の形式"): to_rfc3339(v)
def test_to_jst_converts_and_drops_fraction():
    assert to_jst("2026-11-13T12:05:12.345678Z") == "2026-11-13T21:05:12+09:00"
def test_epoch_us_compares_across_offsets_and_precision():
    assert to_epoch_us("2026-11-13T09:00:00+09:00") == to_epoch_us("2026-11-13T00:00:00Z")
    assert to_epoch_us("2026-11-13T00:00:00.000001Z") - to_epoch_us("2026-11-13T00:00:00Z") == 1
    assert to_epoch_us("2026-11-13T00:00:00.123456789Z") == to_epoch_us("2026-11-13T00:00:00.123456Z")
def test_ago(): assert ago(30, now=datetime(2026, 11, 13, 21, 0, tzinfo=JST)) == "2026-10-14T21:00:00+09:00"
def test_norm_width_and_case(): assert norm("ＡＢＣ ｶﾀｶﾅ") == norm("abc カタカナ")
```

- [ ] **Step 2: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_timeutil.py -q` / Expected: FAIL（import エラー）
- [ ] **Step 3: 実装する** — 入力の判定は正規表現 `^\d{4}-\d{2}-\d{2}$` と `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:\d{2})?$` に加え、実在する日付かを確かめる。エラー文は「時刻の形式が読めません: '…'（例: 2026-11-01 / 2026-11-01T09:00 / 2026-11-01T09:00:00+09:00）」。`to_epoch_us` は小数を6桁に切り詰めて整数マイクロ秒にする
- [ ] **Step 4: 通ることを確かめる** — Run: `.venv/bin/pytest tests/test_timeutil.py -q` / Expected: all passed
- [ ] **Step 5: Commit** — `git commit -am "feat: add JST time handling and text normalization"`（新規ファイルは `git add` してから）

---

### Task 3: API の出入口と読み取り専用の担保

**Files:**
- Create: `src/google_chat_mcp/api.py`・`tests/fakes.py`
- Test: `tests/test_api.py`・`tests/test_readonly_guard.py`

**Interfaces:**
- Produces: `api.RETRIES = 3`・`api.build_chat(credentials) -> Any`・`api.iter_spaces(chat) -> Iterator[dict]`・`api.get_space(chat, space: str) -> dict`・`api.iter_messages(chat, space: str, *, filter: str | None = None, order_by: str = "createTime asc", page_size: int = 1000) -> Iterator[dict]`・`api.list_members(chat, space: str) -> list[dict]`・`api.get_read_state(chat, space: str) -> dict`・`api.build_filter(*, since: str | None = None, until: str | None = None, thread: str | None = None) -> str | None`
- Produces（テスト用）: `fakes.FakeChat(spaces=(), messages=None, members=None, read_states=None, me="users/111", errors=None)` と `.calls: list[tuple[str, dict]]`。`errors` は `{"messages.list": 403}` のように「呼び出し名 → HTTP ステータス」。ファクトリ `fakes.space(id, *, name=None, type="SPACE", last_active=..., threaded=False)`・`fakes.message(space_id, n, ts, *, sender="users/222", sender_name="山田 太郎", sender_type="HUMAN", text="…", **extra)`・`fakes.member(user, name=None, *, type="HUMAN", role="ROLE_MEMBER", email=None)`。偽 API は `pageSize`/`pageToken` のページング、`orderBy` の昇順・降順、`build_filter` が作る絞り込み（`createTime >`・`createTime <`・`thread.name =`、比較は `to_epoch_us`）を再現し、`getSpaceReadState` は名前の `users/me` を `me` に置き換えて返す。書き込みのメソッドは持たない

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_iter_spaces_follows_all_pages():          # 2500件 → 全件、pageSize=1000 で3回
def test_build_filter_forms():
    assert build_filter() is None
    assert build_filter(since="S") == 'createTime > "S"'
    assert build_filter(since="S", until="U", thread="spaces/A/threads/T") == \
        'createTime > "S" AND createTime < "U" AND thread.name = spaces/A/threads/T'
def test_iter_messages_passes_filter_and_order():   # calls に filter と orderBy="createTime desc" が渡る
def test_get_read_state_uses_me_alias():            # name="users/me/spaces/A/spaceReadState" で呼び、返り値は "users/111/…"
def test_list_members_follows_all_pages():
def test_execute_uses_retries():                    # 全呼び出しで num_retries == 3

# tests/test_readonly_guard.py
ALLOWED = {"spaces", "messages", "members", "users", "list", "get", "getSpaceReadState", "execute"}
def test_only_api_module_touches_google_client():   # api.py 以外に r"\.spaces\(|\.users\(|googleapiclient\.discovery" が無い
def test_api_module_calls_read_methods_only():      # api.py の r"\)\s*\.\s*([A-Za-z_]\w*)\s*\(" と r"\bchat\.(\w+)\(" がすべて ALLOWED
def test_no_write_method_names_anywhere():          # src 全体に r"\b(create|patch|delete|updateSpaceReadState|upload)\s*\(" が無い
def test_fake_has_no_write_methods():               # spaces()/messages()/members() に create・patch・update・delete が無い
```

- [ ] **Step 2: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_api.py tests/test_readonly_guard.py -q` / Expected: FAIL
- [ ] **Step 3: `api.py` と `tests/fakes.py` を実装する** — `get_read_state` の name は `f"users/me/{space}/spaceReadState"`。`build_chat` は `googleapiclient.discovery.build("chat", "v1", credentials=..., cache_discovery=False)`。HttpError は `googleapiclient.errors.HttpError(httplib2.Response({"status": n}), b"{}")` で作る
- [ ] **Step 4: 通ることを確かめる** — Run: `.venv/bin/pytest -q` / Expected: all passed
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: add single read-only gateway to the Chat API with guard tests"`

---

### Task 4: スペース（一覧・指定・参加者）

**Files:**
- Create: `src/google_chat_mcp/spaces.py`
- Test: `tests/test_spaces.py`

**Interfaces:**
- Consumes: `api.iter_spaces`・`api.list_members`・`api.get_read_state`・`api.iter_messages`・`timeutil.to_jst`・`textutil.norm`
- Produces: `spaces.SpaceInfo`（frozen dataclass: `space: str`・`display_name: str`・`type: str`・`threaded: bool`・`last_active_ts: str | None`・`url: str | None`、`to_dict() -> dict` は `space`・`display_name`・`type`・`threaded`・`last_active`（日本時間）・`url`）
- Produces: `spaces.parse_space_ref(ref: str) -> str | None`・`spaces.SpaceDirectory(chat)` と `.me() -> str | None`・`.all() -> list[SpaceInfo]`・`.resolve(ref: str) -> SpaceInfo`・`.members(space: str) -> list[dict]`

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_parse_space_ref_forms():
    assert parse_space_ref("spaces/AAAAbbb123") == "spaces/AAAAbbb123"
    assert parse_space_ref("https://chat.google.com/room/AAAAbbb123?cls=7") == "spaces/AAAAbbb123"
    assert parse_space_ref("https://chat.google.com/room/AAAAbbb123/ThReAd99?cls=7") == "spaces/AAAAbbb123"
    assert parse_space_ref("https://chat.google.com/dm/DDDDeee456") == "spaces/DDDDeee456"
    assert parse_space_ref("https://mail.google.com/chat/u/0/#chat/space/AAAAbbb123") == "spaces/AAAAbbb123"
    assert parse_space_ref("https://mail.google.com/chat/u/0/#chat/dm/DDDDeee456") == "spaces/DDDDeee456"
    assert parse_space_ref("営業定例") is None and parse_space_ref("AAAAbbb123") is None
def test_me_from_read_state():                       # FakeChat(me="users/111") → "users/111"
def test_all_sorted_and_dm_named_after_counterpart():
    # 新しい順。DM（自分＋山田 太郎）→ display_name == "DM: 山田 太郎"。last_active は日本時間
def test_bot_only_dm_named_from_latest_sender():     # メンバーに名前が無いボットだけ → 最新の送信者名 "Drive" → "DM: Drive"
def test_group_chat_without_name_lists_members():    # → "グループ: 佐藤、鈴木"（自分を除く）
def test_resolve_by_id_url_and_exact_name():
def test_resolve_dm_by_person_name_and_width_insensitive():
    # "山田" → "DM: 山田 太郎"／"ｴｲｷﾞｮｳ定例" → "エイギョウ定例"
def test_resolve_ambiguous_lists_candidates():       # "定例" が2件 → ChatMcpError、文面に両方の "（spaces/"
def test_resolve_unknown_points_to_list_spaces():    # 文面に "list_spaces"
def test_resolve_bare_id_of_listed_space():          # "AAAAbbb123"（名前に一致なし・一覧にある ID）→ そのスペース
def test_members_formatting():                       # role → "管理者"/"メンバー"、ボットは email なし、HUMAN が先
```

- [ ] **Step 2: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_spaces.py -q` / Expected: FAIL
- [ ] **Step 3: 実装する** — `me()` は最初のスペースで `get_read_state` を呼び、`name` が `users/me/` で始まらなければ `name.split("/spaces/")[0]`（結果をインスタンス内に保持）。URL の ID は正規表現 `(?:/room/|/dm/|#chat/space/|#chat/dm/)([A-Za-z0-9_-]+)` で取る。名前の照合は「完全一致（`DM: ` 等の接頭辞を外した名前も対象）→ 部分一致 → 一覧にある素の ID」の順で、候補が複数ならその時点でエラー（最大10件を「表示名（spaces/ID）」で列挙）
- [ ] **Step 4: 通ることを確かめる** — Run: `.venv/bin/pytest -q` / Expected: all passed
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: list, resolve and describe spaces with readable DM names"`

---

### Task 5: メッセージを読む（記録の続きから読む）

**Files:**
- Create: `src/google_chat_mcp/messages.py`
- Test: `tests/test_messages.py`

**Interfaces:**
- Consumes: `api.iter_messages`・`api.build_filter`・`spaces.SpaceInfo`・`timeutil.to_rfc3339`・`timeutil.to_jst`
- Produces: `messages.MAX_LIMIT = 500`・`messages.format_message(raw: dict) -> dict`（キー `id`・`time`・`sender`・`sender_type`・`text`・`thread`、該当時のみ `is_reply`・`edited`・`attachments`）
- Produces: `messages.read_messages(chat, space: SpaceInfo, *, since: str | None = None, until: str | None = None, limit: int = 100, thread: str | None = None, include_bots: bool = True) -> dict`（キー `space`・`messages`・`count`・`has_more`・`next_since`、該当時のみ `notes: list[str]`）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_format_message_fields():        # time は日本時間、is_reply は threadReply の時だけ、edited は lastUpdateTime の時だけ、
                                         # attachments は name・type・source・drive_file_id（Drive の時だけ）
def test_textless_messages_are_kept():   # 添付だけ → text "" と attachments／カード＋fallbackText → その文／カードのみ → "（カード・本文なし）"
def test_latest_mode_returns_newest_in_ascending_order():
    # 5件・limit=3 → #3,#4,#5 を古い順、has_more True、next_since == #5 の createTime（API の値そのまま）
def test_since_mode_is_exclusive_and_ascending():   # since == #2 の createTime → #3 から
def test_continuation_within_same_second():
    # createTime 12:00:00.000001Z / .000002Z / .000003Z、limit=2
    first = read_messages(fake, sp, since="2026-11-13T00:00:00Z", limit=2)
    assert first["next_since"] == "2026-11-13T12:00:00.000002Z" and first["has_more"] is True
    second = read_messages(fake, sp, since=first["next_since"], limit=2)
    assert [m["id"] for m in second["messages"]] == [third_id] and second["has_more"] is False
def test_limit_is_capped_with_note():    # 600件・limit=1000 → count 500、has_more True、notes に "500"
def test_invalid_limit_raises():         # limit=0 → ChatMcpError
def test_include_bots_false_skips_bots():
def test_thread_filter_reads_one_thread():
def test_until_is_exclusive():
def test_nothing_new():                  # since モードで0件 → next_since は since（正規化後）、latest モードで0件 → None
```

- [ ] **Step 2: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_messages.py -q` / Expected: FAIL
- [ ] **Step 3: 実装する** — `since` なしは `order_by="createTime desc"` で `limit + 1` 件まで集めて逆順に、`since` ありは昇順で `limit + 1` 件まで集め、1件余れば `has_more`。`include_bots=False` の除外は数える前に行う。`since`・`until` は `to_rfc3339` で正規化してから `build_filter` に渡す
- [ ] **Step 4: 通ることを確かめる** — Run: `.venv/bin/pytest -q` / Expected: all passed
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: read messages by period or from where the last read stopped"`

---

### Task 6: キーワード検索

**Files:**
- Create: `src/google_chat_mcp/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `SpaceDirectory.all`・`SpaceDirectory.resolve`・`api.iter_messages`・`api.build_filter`・`messages.format_message`・`timeutil.ago`・`timeutil.to_rfc3339`・`timeutil.to_epoch_us`・`textutil.norm`
- Produces: `search.DEFAULT_DAYS = 30`・`search.SCAN_CAP_PER_SPACE = 5000`・`search.search_messages(chat, directory: SpaceDirectory, *, keywords: str, spaces: list[str] | None = None, since: str | None = None, until: str | None = None, limit: int = 50, now: datetime | None = None) -> dict`（キー `keywords`・`since`・`until`・`hits`（各要素は `space`・`space_name` ＋ `format_message` の中身）・`count`・`scanned_spaces`・`scanned_messages`・`truncated`、該当時のみ `truncated_reasons`）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_and_match_case_and_width_insensitive():   # "見積　ＡＢＣ"（全角空白）→ "abc社の見積" に当たり、"abc社の請求" には当たらない
def test_matches_attachment_names():               # "見積書" → 添付名 "見積書_v2.pdf" のメッセージに当たる
def test_default_window_is_30_days_and_skips_inactive_spaces():
    # now 固定。40日前に最後に動いたスペースは調べない（scanned_spaces に入らない）。31日前のメッセージは当たらない
def test_explicit_spaces_are_resolved():           # spaces=["営業定例"] → そのスペースだけ調べる
def test_hits_newest_first_and_limit_truncates():  # 3件当たり limit=2 → 新しい2件、truncated True、理由に "件数"
def test_scan_cap_is_reported():                   # SCAN_CAP_PER_SPACE=3 に差し替え → 理由に "走査上限" とスペース名
def test_empty_keywords_raise():                   # "　 " → ChatMcpError
```

- [ ] **Step 2: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_search.py -q` / Expected: FAIL
- [ ] **Step 3: 実装する** — 照合の対象は `norm(本文 + " " + 添付名を空白で連結)`。語の分割は `str.split()`（全角空白も分ける）。各スペースは新しい順に走査する
- [ ] **Step 4: 通ることを確かめる** — Run: `.venv/bin/pytest -q` / Expected: all passed
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: keyword search over a bounded period"`

---

### Task 7: 未読

**Files:**
- Create: `src/google_chat_mcp/unread.py`
- Test: `tests/test_unread.py`

**Interfaces:**
- Consumes: `SpaceDirectory.all`・`.resolve`・`.me`・`api.get_read_state`・`api.iter_messages`・`api.build_filter`・`messages.format_message`・`timeutil.ago`・`timeutil.to_epoch_us`・`timeutil.to_jst`
- Produces: `unread.LOOKBACK_DAYS = 90`・`unread.find_unread_spaces(chat, directory, *, now: datetime | None = None) -> tuple[list[tuple[SpaceInfo, str | None]], list[str]]`（未読のあるスペースとその `lastReadTime`（無ければ None）、notes）
- Produces: `unread.read_unread(chat, directory, *, space: str | None = None, limit: int = 200, include_bots: bool = True, now: datetime | None = None) -> dict`（キー `spaces`（各要素 `space`・`display_name`・`type`・`url`・`last_read`・`messages`・`unread_count`・`has_more`）・`total`・`truncated`、該当時のみ `notes`）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_unread_after_last_read_excluding_own():
    # 既読 t2。t1（他人）・t3（自分）・t4（他人）→ messages は t4 だけ、unread_count 1
def test_space_without_new_activity_is_skipped_without_message_calls():   # last_active <= lastReadTime → messages.list を呼ばない
def test_never_opened_space_is_bounded_and_noted():
    # lastReadTime なし → 90日前より後だけを対象、notes に "90日"、自分の発言は数えない
def test_old_spaces_not_checked_and_noted():       # 100日前に動いたスペース → getSpaceReadState を呼ばない、notes に件数
def test_global_limit_truncates():                 # 2スペース×2件・limit=2 → total 2、truncated True、切れたスペースの has_more True
def test_single_space_mode():                      # space="営業定例" → そのスペースだけ
def test_find_unread_spaces_for_listing():         # 未読のあるスペースだけ、lastReadTime つき
```

- [ ] **Step 2: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_unread.py -q` / Expected: FAIL
- [ ] **Step 3: 実装する** — 自分の判定は `directory.me()` と `sender.name` の一致。対象のスペースの判定は `to_epoch_us` で比べる
- [ ] **Step 4: 通ることを確かめる** — Run: `.venv/bin/pytest -q` / Expected: all passed
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: collect unread messages without changing read state"`

---

### Task 8: MCP サーバーとして束ねる・README・CI

**Files:**
- Create: `src/google_chat_mcp/server.py`・`README.md`・`.github/workflows/ci.yml`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: Task 1〜7 の全部
- Produces: `server.server: MCPServer`（名前 `google-chat-readonly`・`log_level="WARNING"`）・`server._chat() -> Any`（`api.build_chat(auth.load_credentials(interactive=True))`。テストで差し替える）・`server.explain_http_error(err: HttpError) -> str`・`server.main() -> None`（`server.run("stdio")`）
- ツール（docstring は日本語。Claude が読む説明なので、`get_messages` には「記録は `next_since` で続きから」、`get_unread` には「未読は記録の起点に使わない」を書く）:
  - `list_spaces(space_type: str | None = None, active_since: str | None = None, name_contains: str | None = None, unread_only: bool = False) -> dict` → `{"count", "spaces"}`。`space_type` は `SPACE`・`GROUP_CHAT`・`DIRECT_MESSAGE` 以外ならエラー。`unread_only` の時は各要素に `last_read`・`unread: true`、該当時のみ `notes`
  - `get_messages(space: str, since: str | None = None, until: str | None = None, limit: int = 100, thread: str | None = None, include_bots: bool = True) -> dict`
  - `search_messages(keywords: str, spaces: list[str] | None = None, since: str | None = None, until: str | None = None, limit: int = 50) -> dict`
  - `get_members(space: str) -> dict` → `{"space", "count", "members"}`
  - `get_unread(space: str | None = None, limit: int = 200, include_bots: bool = True) -> dict`
- 例外の変換: `ChatMcpError` → `ToolError(str(e))`／`HttpError` → `ToolError(explain_http_error(e))`（403 は「権限がありません（スペースに参加していない、または組織の管理設定）」、404 は「見つかりません（ID の誤り、または参加していない）」、429 は「混み合っています。少し待って再実行」、それ以外はステータスと理由）

- [ ] **Step 1: 失敗するテストを書く**

```python
def test_tools_registered():
    names = {t.name for t in asyncio.run(server.server.list_tools())}
    assert names == {"list_spaces", "get_messages", "search_messages", "get_members", "get_unread"}
def test_log_level_is_warning(): assert server.server.settings.log_level == "WARNING"
def test_get_messages_end_to_end_with_fake(monkeypatch):
    # _chat を FakeChat に差し替え、call_tool("get_messages", {"space": "営業定例", "limit": 2})
    # → content[0].text の JSON に next_since（末尾 "Z"）と messages 2件
def test_domain_error_reaches_claude_as_tool_error(monkeypatch):
    # 未知のスペース → ToolError（UnexpectedToolError ではない）、文面に "list_spaces"
def test_http_403_is_explained(monkeypatch):       # FakeChat(errors={"messages.list": 403}) → 文面に "権限"
def test_auth_required_is_explained(monkeypatch):  # _chat が AuthRequiredError → 文面に "python -m google_chat_mcp.auth"
def test_list_spaces_filters(monkeypatch):         # space_type・name_contains・active_since が効く、不正な space_type はエラー
```

- [ ] **Step 2: 失敗を確かめる** — Run: `.venv/bin/pytest tests/test_server.py -q` / Expected: FAIL
- [ ] **Step 3: `server.py` を実装する**
- [ ] **Step 4: README と CI を書く** — README（日本語・環境固有の情報なし）: 目的／セキュリティの性質（4スコープ・書き込み不可の三重の担保・送信先は Google だけ）／ツール5つ／セットアップ（Workspace の組織内にプロジェクト・Chat API の有効化と「構成」（インタラクティブ機能オフ）・同意画面「内部」・デスクトップ クライアント（AI エージェントの印）・JSON の配置）／インストール／`python -m google_chat_mcp.auth`／`claude mcp add google-chat-readonly --scope user -- /path/to/.venv/bin/python -m google_chat_mcp.server`／記録の使い方（`next_since`）と未読の使い分け／MIT。CI: ubuntu・Python 3.12/3.13/3.14・`pip install -e ".[dev]"`・`ruff check src tests`・`pytest -q`・import 確認・`pip-audit`（continue-on-error）
- [ ] **Step 5: 全体を確かめる** — Run: `.venv/bin/ruff check src tests && .venv/bin/pytest -q && .venv/bin/python -c "from google_chat_mcp.server import server, main; print('imports OK')"` / Expected: 指摘なし・all passed・`imports OK`
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat: expose five read-only tools over MCP; add README and CI"`

---

### Task 9: 実物での確認・登録・スキル・記録・公開

**Files:**
- Create（リポジトリ外）: `~/.claude/skills/google-chat-readonly-mcp/SKILL.md`・グローバル memory の記録1件と索引1行
- 公開: GitHub `tg-tool/google-chat-mcp-server`（public）

- [ ] **Step 1: 実 API で煙試験** — `call_tool` で実物の `list_spaces`・`get_unread` を呼ぶ使い捨ての確認を作業フォルダで実行し、**件数だけ**を表示する / Expected: エラーなし
- [ ] **Step 2: Claude Code に登録** — Run: `claude mcp add google-chat-readonly --scope user -- "$PWD/.venv/bin/python" -m google_chat_mcp.server && claude mcp get google-chat-readonly` / Expected: `Status: ✔ Connected`
- [ ] **Step 3: 利用者と実物を確認（完了の条件）** — Claude Code から `list_spaces` → 利用者が選んだスペース1つを `get_messages` で読み、チャットの画面と内容が一致することを利用者に確かめてもらう。このセッションでツールが見えなければ、新しいセッションで確かめてもらう
- [ ] **Step 4: 使い方のスキルを書く** — superpowers:writing-skills を使う。内容: 起動すべき場面／ツール5つ／標準の流れ（記録＝`list_spaces` → `get_messages`（初回は期間、2回目からは記録に残した `next_since`）→ 記録に「どこまで読んだか（`next_since`）」を残す／把握＝`get_unread`／探す＝`search_messages`）／未読と記録の使い分け／セキュリティの性質／失敗時の対処（token を消して `python -m google_chat_mcp.auth`、403・404 の意味、管理者の設定）／機密の扱い（記録先は各プロジェクトの規定に従い、外部と共有する面に生のチャットを書かない）
- [ ] **Step 5: 環境の具体をグローバル memory に残す** — クラウドのプロジェクト・同意画面・クライアントの設定、スコープ、保存先、判断とその理由、実測した API の性質（公開版の設計書に書かなかったもの）
- [ ] **Step 6: 公開前の検査** — リポジトリ外に置いた語の一覧（組織のドメイン・アカウント名・個人のメール・プロジェクト ID・実パス・Google の秘密情報の接頭辞〈クライアントの秘密・アクセストークン・API キー〉など）で、`git ls-files` の全ファイルと `git log --format='%an %ae %B'` を検査する / Expected: 該当なし（commit のメールは noreply）
- [ ] **Step 7: 利用者に公開するファイルの一覧を見せ、了承を得てから push** — Run: `gh repo create tg-tool/google-chat-mcp-server --public --source . --remote origin --push --description "Minimal read-only Google Chat MCP server for Claude Code"` / Expected: 作成と push の成功。その後 `gh run list -L 1` を1回だけ見て CI の結果を報告する

---

## 実行体制（モデルと effort）

2026-11-13 に利用者が確定（推奨どおり）:

| 役割 | モデル | effort | 理由 |
|---|---|---|---|
| メイン（実装・TDD・統合・実物確認） | Opus 5.5 | high | 実測した API の事実と設計の経緯を保持したまま、受け渡しの多い9作業を一貫して進める |
| 最終レビュー（ブランチ全体を1回・新しい文脈） | Opus 5.5 | high | 読み取り専用の担保・取りこぼし・情報の扱いの監査。規模が小さいので xhigh は不要 |
| 公開前の検査 | エージェントなし | — | 機械的な照合なのでメインが直接行う |

- 進め方: superpowers:executing-plans（このセッションで実装し、最後に新しい文脈のレビューを1回）
- 作業ブランチ: `feat/initial-implementation`（完了後に main へ統合）
