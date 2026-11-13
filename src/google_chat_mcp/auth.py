"""OAuth for the read-only Google Chat MCP server.

- Requests read-only scopes only (see SCOPES). Writing is impossible both here
  (no write scope is ever requested) and at Google (read-only tokens are refused).
- The token is stored locally with 0600 permissions; nothing is sent anywhere
  except Google's OAuth endpoints.
- Run `python -m google_chat_mcp.auth` to log in again by hand.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .errors import AuthRequiredError

SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/chat.users.readstate.readonly",
)

_DEFAULT_DIR = Path.home() / ".config" / "google-chat-mcp"
_REAUTH_HINT = (
    "この MCP の仮想環境の python で `python -m google_chat_mcp.auth` を実行し、"
    "ブラウザでログインし直してください。"
)


def credentials_path() -> Path:
    """The OAuth client JSON (desktop app) downloaded from Google Cloud."""
    return Path(os.environ.get("GOOGLE_CHAT_MCP_CREDENTIALS") or _DEFAULT_DIR / "credentials.json")


def token_path() -> Path:
    """Where the authorized-user token is kept."""
    return Path(os.environ.get("GOOGLE_CHAT_MCP_TOKEN") or _DEFAULT_DIR / "token.json")


def has_required_scopes(granted: Iterable[str] | None) -> bool:
    return granted is not None and set(SCOPES) <= set(granted)


def write_token(path: Path, data: str) -> None:
    """Write the token readable by the owner only (file 0600, new folder 0700)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.chmod(path, 0o600)


def _load_saved() -> Credentials | None:
    """The saved token if it is usable with all SCOPES, refreshing it when expired."""
    path = token_path()
    if not path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(path))
    except (ValueError, KeyError, OSError):
        return None
    if not has_required_scopes(creds.scopes):
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        write_token(path, creds.to_json())
        return creds
    return None


def login(*, timeout_seconds: int = 600) -> Credentials:
    """Run the browser login for SCOPES and save the token."""
    client = credentials_path()
    if not client.exists():
        raise AuthRequiredError(
            f"OAuth クライアントの JSON が見つかりません: {client}。"
            "Google Cloud で作った「デスクトップ アプリ」のクライアントの JSON をここに置くか、"
            "環境変数 GOOGLE_CHAT_MCP_CREDENTIALS で場所を指定してください。"
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client), list(SCOPES))
    try:
        creds = flow.run_local_server(port=0, timeout_seconds=timeout_seconds, open_browser=True)
    except Exception as exc:  # the browser flow can time out or be cut short in many ways
        raise AuthRequiredError(
            f"Google へのログインが完了しませんでした（{type(exc).__name__}）。"
            "同意の画面では、表示される読み取りの権限をすべて許可してください。" + _REAUTH_HINT
        ) from exc
    write_token(token_path(), creds.to_json())
    return creds


def load_credentials(*, interactive: bool = True, timeout_seconds: int = 300) -> Credentials:
    """Usable credentials: the saved token, else (if interactive) a fresh browser login."""
    creds = _load_saved()
    if creds is not None:
        return creds
    if not interactive:
        raise AuthRequiredError(
            "Google へのログインが必要です（保存済みの認証が無い・期限切れ・権限が足りない）。"
            + _REAUTH_HINT
        )
    return login(timeout_seconds=timeout_seconds)


def main() -> None:
    """`python -m google_chat_mcp.auth`: log in again by hand. Prints no secrets."""
    try:
        creds = login()
    except AuthRequiredError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
    granted = set(creds.scopes or [])
    print(f"認証 OK（スコープ {sum(s in granted for s in SCOPES)}/{len(SCOPES)}）")


if __name__ == "__main__":
    main()
