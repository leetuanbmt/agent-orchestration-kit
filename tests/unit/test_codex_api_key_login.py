"""Codex API-key login into an account's own home (`--with-api-key`), through the fake codex."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bridge import cli
from bridge.accounts import Account, login_status
from bridge.config import BridgeConfig

KEY = "sk-proj-fakekey0123456789"


# @pt.id KIT-CODEX-APIKEY-001-login
# @pt.item The API key goes from a hidden prompt to `codex login --with-api-key` on stdin, inside the account's home
# @pt.condition an openai account with its own home; getpass returns a key; then a value that is not sk-...; then an anthropic account
# @pt.expected auth.json in that home holds the key, the argv has --with-api-key and the file-store override but no key, the global ~/.codex is untouched; the bad value and the wrong provider are refused before codex runs
# @pt.target bridge.cli._login, bridge.accounts.login_argv
# @pt.method unit
# @pt.legacy none
def test_codex_api_key_login(fake_codex, tmp_path, monkeypatch):
    cfg = BridgeConfig()
    acc = Account("cx", tmp_path / "homes" / "cx", "openai")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: KEY + "\n")
    assert cli._login(cfg, acc, False, api_key=True) is True
    assert json.loads((acc.home / "auth.json").read_text()) == {"OPENAI_API_KEY": KEY}
    argv = json.loads((fake_codex / "login.log").read_text().splitlines()[0])
    assert argv[0] == "login" and "--with-api-key" in argv and KEY not in " ".join(argv)
    assert 'cli_auth_credentials_store="file"' in argv
    assert not (Path(os.environ["HOME"]) / ".codex" / "auth.json").exists()

    other = Account("cy", tmp_path / "homes" / "cy", "openai")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "not-a-key")
    with pytest.raises(ValueError, match="not an OpenAI API key"):
        cli._login(cfg, other, False, api_key=True)
    assert login_status(cfg, other) is False
    with pytest.raises(ValueError, match="openai account"):
        cli._login(cfg, Account("cl", tmp_path / "homes" / "cl", "anthropic"), False,
                   api_key=True)
