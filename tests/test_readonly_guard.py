"""Structural guarantees that this server can only read.

These scan the source on purpose: the spec (§6-5) requires a test-enforced
single gateway to Google that calls read methods only. A failure here means a
change touched the read-only promise and needs a deliberate review.
"""

import re
from pathlib import Path

from fakes import FakeChat

SRC = Path(__file__).resolve().parents[1] / "src" / "google_chat_mcp"
ALLOWED = {"spaces", "messages", "members", "users", "list", "get", "getSpaceReadState", "execute"}
WRITE_CALL = re.compile(r"\b(create|patch|delete|updateSpaceReadState|upload)\s*\(")


def _sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in SRC.glob("*.py")}


def test_only_api_module_touches_google_client():
    pattern = re.compile(r"\.spaces\(|\.users\(|googleapiclient\.discovery")
    offenders = [
        name for name, text in _sources().items() if name != "api.py" and pattern.search(text)
    ]
    assert offenders == []


def test_api_module_calls_read_methods_only():
    text = (SRC / "api.py").read_text(encoding="utf-8")
    called = set(re.findall(r"\)\s*\.\s*([A-Za-z_]\w*)\s*\(", text))
    called |= set(re.findall(r"\bchat\.(\w+)\(", text))
    assert called, "api.py should call the Chat client"
    assert called <= ALLOWED, called - ALLOWED


def test_no_write_method_names_anywhere():
    offenders = {name: WRITE_CALL.findall(text) for name, text in _sources().items()}
    assert {name: found for name, found in offenders.items() if found} == {}


def test_fake_has_no_write_methods():
    chat = FakeChat()
    resources = (
        chat.spaces(),
        chat.spaces().messages(),
        chat.spaces().members(),
        chat.users().spaces(),
    )
    for res in resources:
        for name in ("create", "patch", "update", "delete", "updateSpaceReadState"):
            assert not hasattr(res, name), (type(res).__name__, name)
