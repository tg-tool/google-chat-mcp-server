"""Read-only OAuth handling: scopes, token storage and the login fallbacks."""

import json
import stat
from pathlib import Path

import pytest
from google.auth.exceptions import RefreshError

from google_chat_mcp import auth
from google_chat_mcp.errors import AuthRequiredError

EXPECTED = (
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.memberships.readonly",
    "https://www.googleapis.com/auth/chat.users.readstate.readonly",
)
FUTURE = "2999-01-01T00:00:00Z"
DEFAULT_PROMPT = "Please visit this URL to authorize this application: {url}"
WRITE_SCOPE = "https://www.googleapis.com/auth/chat.messages"


def authorized_user_json(scopes, *, expiry=FUTURE):
    """A token file shaped like the ones google-auth writes."""
    return json.dumps(
        {
            "token": "access-x",
            "refresh_token": "refresh-x",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client-x",
            "client_secret": "secret-x",
            "scopes": list(scopes),
            "expiry": expiry,
        }
    )


class LoginResult:
    """What a completed browser login returns."""

    def __init__(self, scopes):
        self.scopes = list(scopes)

    def to_json(self):
        return json.dumps({"login": True, "scopes": self.scopes})


class ExpiredCreds:
    def __init__(self, error=None):
        self.valid = False
        self.expired = True
        self.refresh_token = "refresh-x"
        self.scopes = list(EXPECTED)
        self.error = error
        self.refreshed_with = None

    def refresh(self, request):
        if self.error:
            raise self.error
        self.refreshed_with = request
        self.valid, self.expired = True, False

    def to_json(self):
        return json.dumps({"refreshed": True})


@pytest.fixture
def conf(tmp_path, monkeypatch):
    cred = tmp_path / "conf" / "credentials.json"
    tok = tmp_path / "conf" / "token.json"
    cred.parent.mkdir()
    cred.write_text("{}")
    monkeypatch.setenv("GOOGLE_CHAT_MCP_CREDENTIALS", str(cred))
    monkeypatch.setenv("GOOGLE_CHAT_MCP_TOKEN", str(tok))
    return cred, tok


class _Calls(list):
    """A call log that can also carry the fake flow class."""


@pytest.fixture
def logins(monkeypatch):
    """Replace the browser login; record each call."""
    calls = _Calls()

    class FakeFlow:
        fail_with = None

        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            flow = cls()
            flow.path, flow.scopes = path, tuple(scopes)
            return flow

        def run_local_server(self, **kwargs):
            calls.append({"path": self.path, "scopes": self.scopes, **kwargs})
            # like google_auth_oauthlib: the prompt goes to stdout unless it is None
            prompt = kwargs.get("authorization_prompt_message", DEFAULT_PROMPT)
            if prompt:
                print(prompt.format(url="https://accounts.example/auth"))
            if FakeFlow.fail_with:
                raise FakeFlow.fail_with
            return LoginResult(self.scopes)

    monkeypatch.setattr(auth, "InstalledAppFlow", FakeFlow)
    calls.flow = FakeFlow
    return calls


def test_scopes_are_exactly_the_four_readonly():
    assert auth.SCOPES == EXPECTED


def test_paths_default_and_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_CHAT_MCP_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CHAT_MCP_TOKEN", raising=False)
    base = Path.home() / ".config" / "google-chat-mcp"
    assert auth.credentials_path() == base / "credentials.json"
    assert auth.token_path() == base / "token.json"
    monkeypatch.setenv("GOOGLE_CHAT_MCP_CREDENTIALS", str(tmp_path / "c.json"))
    monkeypatch.setenv("GOOGLE_CHAT_MCP_TOKEN", str(tmp_path / "t.json"))
    assert auth.credentials_path() == tmp_path / "c.json"
    assert auth.token_path() == tmp_path / "t.json"


def test_scopes_match_requires_the_exact_set():
    assert auth.scopes_match(list(EXPECTED)) is True
    assert auth.scopes_match(list(reversed(EXPECTED))) is True
    assert auth.scopes_match([*EXPECTED, WRITE_SCOPE]) is False
    assert auth.scopes_match(EXPECTED[:3]) is False
    assert auth.scopes_match(None) is False


def test_token_with_extra_write_scope_triggers_login(conf, logins):
    _, tok = conf
    tok.write_text(authorized_user_json([*EXPECTED, WRITE_SCOPE]))
    creds = auth.load_credentials()
    assert len(logins) == 1
    assert set(creds.scopes) == set(EXPECTED)


def test_in_server_login_keeps_stdout_clean(conf, logins, capsys):
    auth.load_credentials()
    assert len(logins) == 1
    assert logins[0]["authorization_prompt_message"] is None
    out, err = capsys.readouterr()
    assert out == ""
    assert "python -m google_chat_mcp.auth" in err


def test_write_token_permissions(tmp_path):
    p = tmp_path / "new" / "token.json"
    auth.write_token(p, '{"a": 1}')
    assert p.read_text() == '{"a": 1}'
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    p.chmod(0o644)
    auth.write_token(p, '{"a": 2}')
    assert p.read_text() == '{"a": 2}'
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_load_uses_valid_token_without_login(conf, logins):
    _, tok = conf
    tok.write_text(authorized_user_json(EXPECTED))
    creds = auth.load_credentials()
    assert creds.valid is True
    assert set(creds.scopes) == set(EXPECTED)
    assert logins == []


def test_load_refreshes_expired_token_and_saves(conf, logins, monkeypatch):
    _, tok = conf
    tok.write_text(authorized_user_json(EXPECTED, expiry="2000-01-01T00:00:00Z"))
    expired = ExpiredCreds()
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: expired)
    creds = auth.load_credentials()
    assert creds is expired
    assert expired.refreshed_with is not None
    assert json.loads(tok.read_text()) == {"refreshed": True}
    assert logins == []


def test_load_logs_in_again_when_scopes_missing(conf, logins):
    cred, tok = conf
    tok.write_text(authorized_user_json(EXPECTED[:3]))
    creds = auth.load_credentials(timeout_seconds=42)
    assert len(logins) == 1
    assert logins[0]["scopes"] == EXPECTED
    assert logins[0]["path"] == str(cred)
    assert logins[0]["timeout_seconds"] == 42
    assert logins[0]["port"] == 0
    assert logins[0]["open_browser"] is True
    assert json.loads(tok.read_text()) == {"login": True, "scopes": list(EXPECTED)}
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600
    assert creds.scopes == list(EXPECTED)


def test_refresh_failure_falls_back_to_login(conf, logins, monkeypatch):
    _, tok = conf
    tok.write_text("{}")
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        lambda path: ExpiredCreds(RefreshError("invalid_grant")),
    )
    auth.load_credentials()
    assert len(logins) == 1


def test_broken_token_file_falls_back_to_login(conf, logins):
    _, tok = conf
    tok.write_text("not json")
    auth.load_credentials()
    assert len(logins) == 1


def test_noninteractive_raises_auth_required(conf, logins):
    with pytest.raises(AuthRequiredError, match=r"python -m google_chat_mcp\.auth"):
        auth.load_credentials(interactive=False)
    assert logins == []


def test_missing_client_file_raises_with_path(conf, logins):
    cred, _ = conf
    cred.unlink()
    with pytest.raises(AuthRequiredError) as excinfo:
        auth.load_credentials()
    assert str(cred) in str(excinfo.value)
    assert logins == []


def test_unfinished_login_is_reported_as_auth_required(conf, logins):
    logins.flow.fail_with = Warning("Scope has changed")
    with pytest.raises(AuthRequiredError, match="権限をすべて許可"):
        auth.load_credentials()


def test_login_always_runs_the_browser_flow(conf, logins):
    _, tok = conf
    tok.write_text(authorized_user_json(EXPECTED))
    auth.login(timeout_seconds=7)
    assert len(logins) == 1
    assert logins[0]["timeout_seconds"] == 7


def test_main_reports_ok_with_scope_count_and_shows_the_url(conf, logins, capsys):
    auth.main()
    out = capsys.readouterr().out
    assert "認証 OK（スコープ 4/4）" in out
    assert "https://accounts.example/auth" in out
