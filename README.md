# google-chat-mcp-server

[![CI](https://github.com/tg-tool/google-chat-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/tg-tool/google-chat-mcp-server/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

Minimal **read-only** Google Chat MCP server for Claude Code.

Claude Code に Google Chat のやりとりを読ませ、把握・記録させるための MCP サーバーです。

- **読み取り専用のスコープ4つだけ**を要求します（送信・編集・削除・既読の変更はできません）
- 外部への送信先は Google の API だけです（第三者のサーバーを経由しません）
- ツールは5つ: スペース一覧／メッセージを読む／キーワード検索／参加者／未読

同じ作者の [google-sheets-mcp-server](https://github.com/tg-tool/google-sheets-mcp-server)（読み取り専用の Sheets MCP）と同じ考え方で作っています。

## ツール

| ツール | 用途 |
|---|---|
| `list_spaces(space_type?, active_since?, name_contains?, unread_only?)` | 参加しているスペース・グループチャット・DM を最近動いた順に一覧。DM は相手の名前で表示 |
| `get_messages(space, since?, until?, limit?, thread?, include_bots?)` | 1つのスペースのメッセージ。最新 N 件、または `since` より後を古い順に |
| `search_messages(keywords, spaces?, since?, until?, limit?)` | 期間を区切ったキーワード検索（AND・全角半角を区別しない・添付名も対象） |
| `get_members(space)` | 参加者の名前・メール・種類・役割 |
| `get_unread(space?, limit?, include_bots?)` | まだ画面で開いていない（未読の）メッセージ。既読にはしない |

`space` には、スペースの名前（一部でよい）・URL・ID（`spaces/…`）のどれでも渡せます。

## 記録のとり方と、未読との使い分け

- **記録は `next_since` で続きから読みます。** `get_messages` の返り値の `next_since` を記録に残し、次回はそれを `since` に渡すと、重複も取りこぼしもなく続きから読めます（`next_since` は API の時刻そのままで、小数秒まで落としません）。`has_more` が `true` なら、まだ続きがあります。
- **未読は「まだ見ていない分の把握」に使います。** 未読は、利用者が Chat の画面で開いた時点で（スマホで開いた分も）消えます。記録の続きの起点には使いません。
- 件数の上限で打ち切ったときは、必ず `has_more` / `truncated` / `notes` で知らせます。黙って切ることはしません。

## セットアップ

### 1. Google Cloud の準備（Google Workspace のアカウントが必要）

Chat API は Google Workspace のアカウント専用です。

1. Google Cloud コンソールで、**自分の Workspace の組織の中に**新しいプロジェクトを作る
2. 「API とサービス」→「ライブラリ」で **Google Chat API** を有効にする
3. Google Chat API の **「構成」** タブで、アプリ名・説明を入れ、**「インタラクティブ機能を有効にする」をオフ**にして保存する（読むだけでも、この登録が必要です）
4. 「Google Auth Platform」で同意画面を作る。**対象は「内部」** にする
   - 「外部」でテスト中のままだと、ログインが7日で切れます（[Google のドキュメント](https://developers.google.com/identity/protocols/oauth2#expiration)）
5. 「クライアント」→「クライアントを作成」で **「デスクトップ アプリ」** を作る。「このクライアントを AI 搭載エージェントに使用する」にチェックを入れる（MCP 連携向けの自己申告で、権限は増えません。[Google のヘルプ](https://support.google.com/cloud/answer/15549257)）
6. 作成直後の画面で **JSON をダウンロード**し、次の場所に置く

```bash
mkdir -p ~/.config/google-chat-mcp && chmod 700 ~/.config/google-chat-mcp
mv ~/Downloads/client_secret_*.json ~/.config/google-chat-mcp/credentials.json
chmod 600 ~/.config/google-chat-mcp/credentials.json
```

### 2. インストール

```bash
git clone https://github.com/tg-tool/google-chat-mcp-server.git
cd google-chat-mcp-server
python3 -m venv .venv
.venv/bin/pip install -e .
```

### 3. ログイン（初回のみ）

```bash
.venv/bin/python -m google_chat_mcp.auth
```

ブラウザが開くので、Workspace のアカウントでログインし、表示される読み取りの権限をすべて許可します。トークンは `~/.config/google-chat-mcp/token.json`（0600）に保存されます。

### 4. Claude Code に登録

```bash
claude mcp add google-chat-readonly --scope user -- /path/to/google-chat-mcp-server/.venv/bin/python -m google_chat_mcp.server
claude mcp get google-chat-readonly
```

保存先は環境変数 `GOOGLE_CHAT_MCP_CREDENTIALS` / `GOOGLE_CHAT_MCP_TOKEN` で変えられます。

## セキュリティ

**スコープは次の4つだけです。**
- `https://www.googleapis.com/auth/chat.spaces.readonly`
- `https://www.googleapis.com/auth/chat.messages.readonly`
- `https://www.googleapis.com/auth/chat.memberships.readonly`
- `https://www.googleapis.com/auth/chat.users.readstate.readonly`

**読み取り専用は三重に担保しています。**
1. 要求するスコープが読み取り専用だけ（テストで完全一致を確認）
2. Google の API に触れるのは `src/google_chat_mcp/api.py` の1か所だけで、呼ぶのは `list`・`get`・`getSpaceReadState` だけ（テストでソースを走査して確認）
3. Google 側でも、読み取り専用のトークンでの書き込みは拒否される

**認証情報とログの扱い**
- 認証情報はローカルの `~/.config/google-chat-mcp/`（0600）に置き、git に入れません（`.gitignore` 済み）
- サーバーのログは WARNING 以上だけで、メッセージの本文やスペース名をログに出しません

**読んだ内容の行き先**
- 読んだチャットの内容は、MCP クライアント（Claude）に渡ります。機密の扱いは、利用者の組織の規定に従ってください。

## 開発

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

テストは偽の Chat API（`tests/fakes.py`）で行い、ネットワークにはつなぎません。

## ライセンス

MIT
