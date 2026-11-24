"""Claude tokens kept in a private file (`credential: file`), the store for hosts without a Keychain."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

import pytest

from bridge import accounts, cli
from bridge.accounts import (CLAUDE_KEY_ENV, CLAUDE_TOKEN_ENV, Account, account_env,
                             credential_check, credential_file, login_status)
from bridge.config import BridgeConfig
from bridge.orchestrator import AccountProfile
from bridge.registry import registry_dir

TOKEN = "sk-ant-oat01-filestoretoken"


def acc(tmp_path, name="cl", kind="oauth_token"):
    return Account(name, tmp_path / "homes" / name, "anthropic", credential="file",
                   credential_kind=kind)


def put(path, value, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(value + "\n")
    path.chmod(mode)


# @pt.id KIT-FILECRED-001-good
# @pt.item A 0600 credential file owned by the user is read at run time only, into the provider's variable
# @pt.condition credential: file, the token in ~/.agent-bridge/credentials/<account> (0600, directory 0700); a global token and API key in the base env
# @pt.expected the run env carries the token as CLAUDE_CODE_OAUTH_TOKEN and in no other variable; the global key is removed; credential_check names the file and reports present and well_formed without the value
# @pt.target bridge.accounts.account_env, credential_check
# @pt.method unit
# @pt.legacy credential: file did not exist; Linux had only token_env
def test_file_token_good(tmp_path):
    a = acc(tmp_path)
    path = credential_file(a)
    assert path == registry_dir() / "credentials" / "cl"
    put(path, TOKEN)
    env = account_env(a, {CLAUDE_TOKEN_ENV: "global", CLAUDE_KEY_ENV: "sk-ant-api-global",
                          "PATH": "/bin"})
    assert env[CLAUDE_TOKEN_ENV] == TOKEN
    assert [k for k, v in env.items() if TOKEN in v] == [CLAUDE_TOKEN_ENV]
    assert not any(TOKEN in v for v in os.environ.values())  # no env repr on failure
    check = credential_check(a)
    assert check == {"reference": "file ~/.agent-bridge/credentials/cl", "kind": "oauth_token",
                     "present": True, "well_formed": True}
    assert TOKEN not in json.dumps(check)


# @pt.id KIT-FILECRED-002-refused
# @pt.item A missing, empty, wrong-shaped or unsafe credential file is never forwarded
# @pt.condition absent; empty; wrong prefix; mode 0644; a symlink to a good 0600 file; a directory; directory mode 0755; a file owned by someone else (geteuid patched)
# @pt.expected no run env gets a token, login_status is False without starting the CLI, and credential_check names the problem without the value
# @pt.target bridge.accounts.credential_check, account_env, login_status
# @pt.method unit
# @pt.legacy credential: file did not exist
@pytest.mark.parametrize("case", ["absent", "empty", "prefix", "0644", "symlink", "directory",
                                  "dir-0755", "owner"])
def test_file_token_refused(tmp_path, monkeypatch, case):
    a = acc(tmp_path)
    path = credential_file(a)
    cfg = BridgeConfig(claude_bin="/nonexistent/claude")
    problem = {"absent": None, "empty": None, "prefix": None, "0644": "group- or world-readable",
               "symlink": "not a regular file", "directory": "not a regular file",
               "dir-0755": "must be a 0700 directory", "owner": "owned by you"}[case]
    if case == "empty":
        put(path, "")
    elif case == "prefix":
        put(path, "ghp_not_a_claude_token")
    elif case == "0644":
        put(path, TOKEN, 0o644)
    elif case == "symlink":
        real = tmp_path / "elsewhere"
        put(real, TOKEN)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.symlink_to(real)
    elif case == "directory":
        path.parent.mkdir(mode=0o700, parents=True)
        path.mkdir(mode=0o700)
    elif case == "dir-0755":
        put(path, TOKEN)
        path.parent.chmod(0o755)
    elif case == "owner":
        put(path, TOKEN)
        uid = os.geteuid()
        monkeypatch.setattr(accounts.os, "geteuid", lambda: uid + 1)
    assert CLAUDE_TOKEN_ENV not in account_env(a, {CLAUDE_TOKEN_ENV: "global"})
    assert login_status(cfg, a) is False
    check = credential_check(a)
    assert check["well_formed"] is False and TOKEN not in json.dumps(check)
    assert check["present"] is (case not in ("absent",))
    if problem is None:
        assert "problem" not in check
    else:
        assert problem in check["problem"]


# @pt.id KIT-FILECRED-003-config
# @pt.item credential: file is a valid anthropic credential source
# @pt.condition profiles with credential file on anthropic, on openai, and together with token_env
# @pt.expected only the first is accepted
# @pt.target bridge.orchestrator.AccountProfile
# @pt.method unit
# @pt.legacy credential accepted only keychain
def test_file_config_validation():
    from pydantic import ValidationError
    assert AccountProfile(provider="anthropic", credential="file").credential == "file"
    for bad in ({"provider": "openai", "credential": "file"},
                {"provider": "anthropic", "credential": "file",
                 "token_env": "AGENT_BRIDGE_CLAUDE_X_TOKEN"}):
        with pytest.raises(ValidationError):
            AccountProfile(**bad)


YAML = """version: 1
accounts:
  cl: {provider: anthropic, home: '%s', credential: file}
agents:
  claude: {adapter: claude-cli, account: cl, allow_write: true}
roles: {executor: claude}
workflow: {stages: [execute, test]}
"""


@pytest.fixture
def project(tmp_path, monkeypatch, fake_claude):
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    home = tmp_path / "homes" / "cl"
    home.mkdir(parents=True)
    (root / ".ai" / "orchestrator.yaml").write_text(YAML % home)
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    return Account("cl", home.resolve(), "anthropic", credential="file")


# @pt.id KIT-FILECRED-004-set-token
# @pt.item account-set-token writes the file store atomically with 0600 in a 0700 directory, from stdin or a no-echo prompt
# @pt.condition a credential: file account; the token piped on stdin, then typed at a getpass prompt, then a wrong-shaped value, then --delete
# @pt.expected the file holds the token with mode 0600 in a 0700 directory, no temp file remains, argv never carries it, a wrong value is refused without touching the stored one, and --delete removes the file
# @pt.target bridge.cli._set_token, bridge.accounts.store_credential_file
# @pt.method unit
# @pt.legacy account-set-token stored only into the macOS Keychain
def test_set_token_file(project, monkeypatch):
    path = credential_file(project)
    calls = []
    real = subprocess.run
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, *a, **k: calls.append(argv)
                        or real(argv, *a, **k))
    monkeypatch.setattr(sys, "stdin", io.StringIO(TOKEN + "\n"))
    out = cli._set_token(argparse.Namespace(name="cl", provider=None, delete=False))
    assert out["account"] == "cl" and out["stored"] is True
    assert path.read_text().strip() == TOKEN
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert sorted(p.name for p in path.parent.iterdir()) == ["cl"]
    assert account_env(project, {})[CLAUDE_TOKEN_ENV] == TOKEN
    # A terminal: the value comes from a no-echo prompt.
    tty = io.StringIO()
    tty.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", tty)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": TOKEN[:-1] + "Z")
    cli._set_token(argparse.Namespace(name="cl", provider=None, delete=False))
    assert path.read_text().strip() == TOKEN[:-1] + "Z"
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "ghp_wrong")
    with pytest.raises(ValueError, match="sk-ant-oat"):
        cli._set_token(argparse.Namespace(name="cl", provider=None, delete=False))
    assert path.read_text().strip() == TOKEN[:-1] + "Z"
    for argv in calls:
        assert not any(TOKEN[:-1] in str(x) for x in argv)
    out = cli._set_token(argparse.Namespace(name="cl", provider=None, delete=True))
    assert out == {"account": "cl", "deleted": True} and not path.exists()


# @pt.id KIT-FILECRED-005-keychain-elsewhere
# @pt.item credential: keychain on a host without the macOS Keychain says so
# @pt.condition a keychain account while sys.platform is linux
# @pt.expected credential_check reports a problem that names credential: file
# @pt.target bridge.accounts.credential_check
# @pt.method unit
# @pt.legacy a Linux keychain account read as an absent token with no reason
def test_keychain_off_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts.sys, "platform", "linux")
    a = Account("k", tmp_path / "k", "anthropic", credential="keychain")
    assert "credential: file" in credential_check(a)["problem"]


# @pt.id KIT-FILECRED-006-config-check
# @pt.item config-check warns, without failing, about accounts that still read a token from the environment
# @pt.condition one token_env account and one credential: file account
# @pt.expected ok stays true and exactly one warning names the token_env account and the file-store migration
# @pt.target bridge.cli._run (config-check)
# @pt.method unit
# @pt.legacy token_env passed config-check silently
def test_config_check_warns_token_env(tmp_path, monkeypatch):
    import asyncio
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    (root / ".ai" / "orchestrator.yaml").write_text("""version: 1
accounts:
  env: {provider: anthropic, home: '%s/env', token_env: AGENT_BRIDGE_CLAUDE_ENV_TOKEN}
  fil: {provider: anthropic, home: '%s/fil', credential: file}
agents:
  a: {adapter: claude-cli, account: env, allow_write: true}
  b: {adapter: claude-cli, account: fil, allow_write: true}
roles: {executor: {agents: [a, b]}}
workflow: {stages: [execute, test]}
""" % (tmp_path, tmp_path))
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    out = asyncio.run(cli._run(argparse.Namespace(cmd="config-check")))
    assert out["ok"] is True
    assert out["warnings"] == [
        "accounts.env: token_env AGENT_BRIDGE_CLAUDE_ENV_TOKEN keeps the token in the environment "
        "of whatever starts the bridge; move it to the file store: set `credential: file`, run "
        "`agent-bridge account-set-token env`, then unset the variable"]
