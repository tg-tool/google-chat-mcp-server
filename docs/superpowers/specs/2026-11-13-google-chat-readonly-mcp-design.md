# 読み取り専用 Google Chat MCP — 設計書（公開版）

- 作成: 2026-11-13
- 状態: 承認済み
- 先行事例: 同じ作者の読み取り専用 Sheets MCP（[tg-tool/google-sheets-mcp-server](https://github.com/tg-tool/google-sheets-mcp-server)）。同じ型で作る
- この公開版は、組織名・アカウント・クラウドのプロジェクト ID・実測した件数などの環境固有の情報を除いている

---

## 1. 目的と理由

利用者が **Claude Code に特定の Google Chat を確認させ、やりとりを把握し、記録をとらせる** ため。毎日のように使う。

- 「記録」が目的なので、**取りこぼしと重複が無いこと**が最重要。「取れた範囲」を「全体」と取り違える失敗を、返り値の構造で防ぐ
- 記録では「誰が何を言ったか」が要る。**送信者の名前は必須**
- 扱うのは業務のチャット。**書き込み能力を物理的に持たない**ことが前提

## 2. ゴール／非ゴール／アンチゴール

**ゴール**
- 参加しているスペース（スペース・グループチャット・DM）を一覧し、名前・URL・ID のどれかで指定できる
- 指定したチャットのメッセージを、期間で、または「前回の続きから」読める。スレッドの返信関係が分かる
- 期間を区切ったキーワード検索で、「あの話はどこだったか」を探せる
- スペースの参加者が分かる（記録の「参加者」欄に使う）
- 利用者がチャットの画面でまだ開いていない（未読の）メッセージを、スペースをまたいで拾える。用途は「まだ見ていない分の把握」で、記録の続きの起点には使わない
- Claude Code の全プロジェクトから使える（user scope 登録）

**非ゴール**
- 送信・編集・削除・リアクション
- 既読・未読の状態を変えること（読むだけ。Claude が読んでも利用者の未読は残る）
- 添付ファイルの中身のダウンロード（名前と種類、Drive のファイルなら ID まで）
- 新着の常時監視・通知・自動での記録（MCP はファイルを書かない。記録は呼び出した側が行う）
- 管理者権限での組織横断の閲覧

**アンチゴール**（動いても失敗とみなすもの）
- 書き込みのスコープやメソッドが1つでも入ること
- 件数の上限で**黙って切ること**。切ったら必ず `has_more` / `truncated` で知らせる
- トークン・クライアントの秘密・チャットの本文が、リポジトリ・ログ・標準エラーに出ること
- 送信者が ID（`users/123…`）のまま返り、記録に使えないこと

## 3. 前提と制約

| 項目 | 内容 |
|---|---|
| アカウント | Google Workspace のアカウント（Chat API は Workspace 専用） |
| クラウドのプロジェクト | 利用者の Workspace の組織の中に専用のプロジェクトを作る。Chat API を有効にし、「構成」（アプリ名・説明・**インタラクティブ機能オフ**）を登録する（読むだけでも必須） |
| 同意画面 | **「内部」**。内部なら更新トークンが7日で切れない（外部・テスト中だと7日で切れる。[出典](https://developers.google.com/identity/protocols/oauth2#expiration)） |
| クライアント | デスクトップ アプリ。「AI 搭載エージェントに使用する」の印を付ける（Google が MCP 連携に推奨する自己申告。権限は増えない。[出典](https://support.google.com/cloud/answer/15549257?hl=en)） |
| スコープ | **次の4つだけ**: `chat.spaces.readonly`・`chat.messages.readonly`・`chat.memberships.readonly`・`chat.users.readstate.readonly` |
| 認証情報 | `~/.config/google-chat-mcp/credentials.json`・`token.json`（どちらも 0600、フォルダは 0700）。環境変数で変更可 |
| Python | 3.12 以上（開発は 3.14） |
| MCP SDK | `mcp` 2.x（`MCPServer`） |

## 4. 確認済みの API の性質

- ユーザー認証の3スコープで、送信者の名前（`sender.displayName`）が取れる
- `createTime` は UTC・小数6桁。絞り込み `createTime > "…"` は境界を含まない。`+09:00` 付きの時刻・`thread.name = …`・両者の AND が使える
- `users/me/spaces/{space}/spaceReadState` の返り値の `name` は実際のユーザー ID に解決される（＝「自分」を1回の呼び出しで特定できる）
- メンバー一覧の既定にはボットも含まれる。ボットのメンバーには名前が付かないことがある（送信者としての名前は付く）
- DM には名前（`displayName`）が無い。`spaceUri` は `https://chat.google.com/dm/<ID>?cls=…` などの形
- キーワード検索の API は一般ユーザーには無い → 期間を区切って取り、手元で絞り込む
- 非スレッド型のスペースでも返信（`threadReply`）がある（インラインのスレッド）

## 5. 作り方の選択肢

| 案 | 判断 |
|---|---|
| **A. 自作の Python MCP（Sheets と同型）** | **採用**。権限・送信先・コードを全部自分で管理できる |
| B. Google 公式の Chat MCP | 却下。Developer Preview（参加申請が要る）・送信の機能を含む・Claude Code での接続手順が公式に無い |
| C. 第三者の MCP | 却下。書き込み込みの大型か、実績が乏しい |
| D. MCP でなくコマンドの台本 | 却下。プロジェクトごとに実行の許可が要る |
| 名前の解決に People API ＋名簿の権限 | 不要（3スコープで名前が取れる） |

## 6. 仕様

### 6-1. ツール（5つ）

**`list_spaces(space_type=None, active_since=None, name_contains=None, unread_only=False)`**
- 参加している全スペースを、最近動いた順に返す（ページングは全件たどる）
- 各要素: `space`（`spaces/AAAA`）・`display_name`・`type`・`threaded`・`last_active`（日本時間）・`url`
- **DM とグループチャットの `display_name` は相手の名前**（メンバー一覧から自分以外の名前。名前が無いボットだけの DM は、最新メッセージの送信者名で補う）
- `unread_only=True` のときだけ既読の時刻を取りに行き、未読のあるスペースに絞って `last_read` と `unread: true` を付ける（既定では取らない。名前でのスペース解決にも使わない）

**`get_messages(space, since=None, until=None, limit=100, thread=None, include_bots=True)`**
- `since` あり → `since` より**後**を古い順に最大 `limit` 件。返り値の **`has_more`**（新しい分が残っている）と **`next_since`**（最後に返したメッセージの時刻。次回これを `since` に渡せば重複も漏れも無く続きから読める）
- `since` が無い → **最新の `limit` 件**（古い順に並べて返す）。返り値の **`has_older`**（それより古い分が残っている）と **`older_until`**（返した中で最も古い時刻。`until` に渡すとさらに古い分を読める）。`next_since` も返す（そこから記録を始められる）
- 同じ時刻のメッセージは2回の読み取りに分けない（境目で取りこぼさないため。`limit` を少し超えることがある）
- 共通の返り値: `space`・`messages`・`count`
- `next_since` は API の `createTime` を**小数秒まで落とさずそのまま**返し、`since` に渡されたら丸めずにそのまま絞り込みに使う
- 各メッセージ: `id`・`time`（日本時間）・`sender`（名前）・`sender_type`・`text`・`thread`・返信なら `is_reply`・編集済みなら `edited`・`attachments`（名前・種類・出どころ・Drive なら `drive_file_id`）
- `text` が空のボットのカードは `fallbackText`、それも無ければ「（カード・本文なし）」
- `limit` の上限は 500。超える指定は 500 に丸め、丸めたことを返り値に書く

**`search_messages(keywords, spaces=None, since=None, until=None, limit=50)`**
- `keywords` は空白区切り（全角空白も可）の **AND**。大文字小文字と全角半角を区別しない（NFKC）。本文と添付の名前を対象にする
- `spaces` 省略時は期間内に動いたスペースだけを調べる。`since` 省略時は **30日前**から
- 返り値: ヒット（新しい順）・`scanned_spaces`・`scanned_messages`・**`truncated`** と理由

**`get_members(space)`**
- 参加者の `name`・`email`・`type`・`role`

**`get_unread(space=None, limit=200, include_bots=True)`**
- 既読の時刻（`lastReadTime`）より後のメッセージを未読とみなす。**自分の発言は数えない**
- `space` 省略時は、最後の動きが既読の時刻より新しいスペースを全部調べ、スペースごとにまとめて返す。長く動いていないスペースは調べない（その旨を返り値に書く）
- 全体で `limit` を超えたら打ち切り、`truncated` で知らせる
- **使い分け**: 未読は利用者が画面で開いた時点で消える。記録の続きの起点には使わず、`get_messages` の `next_since` を使う

### 6-2. スペースの指定（`space` 引数の共通ルール）
- `spaces/AAAA`・`AAAA`・Chat の URL（`chat.google.com/room/…`・`/dm/…`・`mail.google.com/chat/…#chat/space/…`・`#chat/dm/…`。クエリやスレッドの続きが付いていてもよい）・**名前**
- 名前は「完全一致 → 部分一致（NFKC・大文字小文字無視）」。候補が複数ならエラーで候補（最大10件）を返す。0件なら `list_spaces` を案内する
- ID・URL は `spaces.get` 1回で解決する（全スペースの一覧を作らない）。DM・グループの名前と「自分」の ID は、サーバーの中で10分間覚えておく

### 6-3. 時刻
- 出力は日本時間の ISO 8601（秒まで）。`next_since` だけは API の値そのまま
- 入力は `2026-11-01`（その日の 0:00 日本時間）・`2026-11-01T09:00`（日本時間）・時差つき ISO のどれでも受ける

### 6-4. 認証
- `token.json` があれば使い、期限切れは自動更新。無い・壊れている・スコープが4つと一致しない場合はブラウザのログインを起動する（待ち時間の上限つき）。MCP の標準出力は通信路なので、ログインの案内は標準エラーに出す
- 手動でやり直す入口: `python -m google_chat_mcp.auth`

### 6-5. 読み取り専用の担保（三重）
1. **スコープ**: 4つの readonly だけ。テストで完全一致を確かめる。実行時も、保存済みトークンのスコープが4つと完全に一致しなければ使わない（書き込みの権限を含むトークンも拒む）
2. **コード**: Google API に触れるのは `api.py` の1か所だけ。そこで呼ぶのは `list`・`get`・`getSpaceReadState` だけ。テストでソースを走査して確かめる。テスト用の偽 API は書き込みのメソッドを持たない
3. **Google 側**: readonly スコープのトークンでは書き込みが拒否される

### 6-6. エラーとログ
- 想定内の失敗（スペースが見つからない・形式の誤り・認証・403/404）は、理由と直し方を添えて Claude に返す（MCP SDK の `ToolError`）
- 一時的な失敗（429・5xx）は数回だけ自動で再試行
- サーバーのログは WARNING 以上だけ（メッセージの本文やスペース名を INFO ログに出さない）

## 7. テスト方針
- TDD（pytest）。Google API は偽物に差し替え、ネットにつながずに試す
- 最後に実物で確認: Claude Code に登録し、実際のスペースを1つ読む

## 8. リポジトリと公開
- 公開リポジトリ。環境固有の情報（組織・アカウント・プロジェクト ID・実測値・実パス）を入れない。commit と push の前に手元で機械的に検査する
- LICENSE は MIT（著作権者 `tg-tool`）
- GitHub Actions で ruff・pytest・import 確認
