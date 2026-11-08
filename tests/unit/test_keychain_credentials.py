"""Claude tokens kept in the macOS Keychain (`credential: keychain`), through a fake `security`."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys

import pytest
from pydantic import ValidationError

from bridge import cli
from bridge.accounts import (CLAUDE_KEY_ENV, CLAUDE_TOKEN_ENV, KEYCHAIN_SERVICE, Account,
                             account_env, keychain_account, login_status)
from bridge.config import BridgeConfig
from bridge.orchestrator import AccountProfile
from tests.conftest import FAKES

TOKEN_A, TOKEN_B = "sk-ant-oat01-aaaa", "sk-ant-oat01-bbbb"


@pytest.fixture
def fake_security(tmp_path, monkeypatch, fake_codex):
    exe = FAKES / "security"
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    state = tmp_path / "fake_security"
    monkeypatch.setenv("FAKE_SECURITY_DIR", str(state))
    return state


def store(state, acc, value):
    state.mkdir(parents=True, exist_ok=True)
    path = state / "items.json"
    items = json.loads(path.read_text()) if path.exists() else {}
    items[f"{KEYCHAIN_SERVICE}\n{keychain_account(acc)}"] = value
    path.write_text(json.dumps(items))


def argv_log(state):
    return [json.loads(line) for line in (state / "argv.log").read_text().splitlines()]


# @pt.id KIT-KEYCHAIN-001-per-account
# @pt.item Each keychain account's run gets its own token, read at run time
# @pt.condition two anthropic accounts with credential keychain and stored tokens; a global token and API key in the base env
# @pt.expected each env carries only its own token; the global token and ANTHROPIC_API_KEY are removed; no token is in the bridge's own env or in any security argv
# @pt.target bridge.accounts.account_env
# @pt.method unit
# @pt.legacy none
def test_keychain_token_per_account(fake_security, tmp_path):
    a = Account("a", tmp_path / "a", "anthropic", credential="keychain")
    b = Account("b", tmp_path / "b", "anthropic", credential="keychain")
    store(fake_security, a, TOKEN_A)
    store(fake_security, b, TOKEN_B)
    base = {CLAUDE_TOKEN_ENV: "global", CLAUDE_KEY_ENV: "sk-ant-api-global", "PATH": os.environ["PATH"]}
    env_a, env_b = account_env(a, base), account_env(b, base)
    assert env_a[CLAUDE_TOKEN_ENV] == TOKEN_A and env_b[CLAUDE_TOKEN_ENV] == TOKEN_B
    assert CLAUDE_KEY_ENV not in env_a and CLAUDE_KEY_ENV not in env_b
    assert TOKEN_A not in os.environ.values() and TOKEN_B not in os.environ.values()
    assert keychain_account(a) != keychain_account(b)
    for argv in argv_log(fake_security):
        assert TOKEN_A not in argv and TOKEN_B not in argv


# @pt.id KIT-KEYCHAIN-002-missing-or-wrong
# @pt.item A missing, wrong-shaped or unreadable keychain token reads as logged out
# @pt.condition no stored item; then an item holding a GitHub token; then no `security` binary at all
# @pt.expected login_status is False without starting the CLI and the run env has no token, every time
# @pt.target bridge.accounts.login_status, account_env
# @pt.method unit
# @pt.legacy none
def test_keychain_missing_is_logged_out(fake_security, tmp_path, monkeypatch):
    acc = Account("a", tmp_path / "a", "anthropic", credential="keychain")
    cfg = BridgeConfig(claude_bin="/nonexistent/claude")
    assert login_status(cfg, acc) is False and CLAUDE_TOKEN_ENV not in account_env(acc, {})
    store(fake_security, acc, "ghp_not_a_claude_token")
    assert login_status(cfg, acc) is False and CLAUDE_TOKEN_ENV not in account_env(acc, {})
    monkeypatch.setenv("PATH", "/nonexistent")
    assert login_status(cfg, acc) is False and CLAUDE_TOKEN_ENV not in account_env(acc, {})


# @pt.id KIT-KEYCHAIN-003-config
# @pt.item credential: keychain is for anthropic accounts and excludes token_env
# @pt.condition profiles with keychain on anthropic, on openai, and together with token_env
# @pt.expected only the first is accepted
# @pt.target bridge.orchestrator.AccountProfile
# @pt.method unit
# @pt.legacy none
def test_keychain_config_validation():
    assert AccountProfile(provider="anthropic", credential="keychain").credential == "keychain"
    for bad in ({"provider": "openai", "credential": "keychain"},
                {"provider": "anthropic", "credential": "keychain",
                 "token_env": "AGENT_BRIDGE_CLAUDE_X_TOKEN"},
                {"provider": "anthropic", "credential": "file"}):
        with pytest.raises(ValidationError):
            AccountProfile(**bad)


YAML = """version: 1
accounts:
  cl: {provider: anthropic, home: '%s', credential: keychain}
agents:
  claude: {adapter: claude-cli, account: cl, allow_write: true}
roles: {executor: claude}
workflow: {stages: [execute, test]}
"""


# @pt.id KIT-KEYCHAIN-004-set-token
# @pt.item account-set-token stores and deletes the token through security's own prompt
# @pt.condition a project account with credential keychain; the token is typed at the prompt; then --delete
# @pt.expected the item holds the token, add-generic-password ends with -w and never carries the token; --delete removes it; every call is scoped to service agent-bridge
# @pt.target bridge.cli._set_token
# @pt.method unit
# @pt.legacy none
def test_set_token_prompts_and_deletes(fake_security, fake_claude, tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    home = tmp_path / "homes" / "cl"
    home.mkdir(parents=True)
    (root / ".ai" / ("orchestrator" + ".yaml")).write_text(YAML % home)
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    real = subprocess.run

    def typed(argv, *a, **k):  # stands in for the human typing at security's TTY prompt
        if argv[:2] == ["security", "add-generic-password"]:
            k["input"], k["text"] = TOKEN_A + "\n", True
        return real(argv, *a, **k)

    monkeypatch.setattr(cli.subprocess, "run", typed)
    out = cli._set_token(argparse.Namespace(name="cl", provider=None, delete=False))
    assert out["account"] == "cl" and out["stored"] is True
    acc = Account("cl", home.resolve(), "anthropic", credential="keychain")
    assert account_env(acc, {})[CLAUDE_TOKEN_ENV] == TOKEN_A
    add, = [argv for argv in argv_log(fake_security) if argv[0] == "add-generic-password"]
    assert add[-1] == "-w" and TOKEN_A not in add
    out = cli._set_token(argparse.Namespace(name="cl", provider=None, delete=True))
    assert out == {"account": "cl", "deleted": True}
    assert CLAUDE_TOKEN_ENV not in account_env(acc, {})
    # Existing logins (Claude Code's and Codex's own Keychain items) are never touched.
    for argv in argv_log(fake_security):
        assert argv[argv.index("-s") + 1] == KEYCHAIN_SERVICE, argv


# @pt.id KIT-KEYCHAIN-005-human-only
# @pt.item account-set-token is a human-only command
# @pt.condition the installed CLI runs account-set-token without a terminal
# @pt.expected it is refused with exit 2 before reading any config
# @pt.target bridge.cli._run
# @pt.method integration
# @pt.legacy none
def test_set_token_needs_terminal(tmp_path):
    exe = os.path.join(os.path.dirname(sys.executable), "agent-bridge")
    out = subprocess.run([exe, "account-set-token", "cl"], cwd=tmp_path, capture_output=True,
                         text=True, stdin=subprocess.DEVNULL)
    assert out.returncode == 2 and "human only" in out.stderr
    assert "account-set-token" in cli.HUMAN_ACCOUNT_CMDS


SHOW_YAML = """version: 1
accounts:
  kc: {provider: anthropic, home: '%s', credential: keychain}
agents:
  claude-kc: {adapter: claude-cli, account: kc, allow_write: true}
roles:
  executor: {agents: [claude-kc], default: claude-kc}
workflow: {stages: [execute, test]}
"""


# @pt.id KIT-KEYCHAIN-006-show-verify
# @pt.item account-show and account-verify report the credential reference, never its value
# @pt.condition a keychain account that is the executor default, with no item, then a wrong-shaped item, then a valid token
# @pt.expected show names the keychain reference, its agent and role default; verify is not ok until the token is valid; no output holds the token
# @pt.target bridge.service.Bridge.show_account, verify_account, bridge.accounts.credential_check
# @pt.method unit
# @pt.legacy none
def test_show_and_verify_account(fake_security, repo, tmp_path):
    from bridge.config import BridgeConfig as Cfg
    from bridge.service import Bridge

    home = tmp_path / "homes" / "kc"
    home.mkdir(parents=True)
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "orchestrator.yaml").write_text(SHOW_YAML % home)
    b = Bridge(repo, Cfg(claude_bin="/nonexistent/claude"), create=False)
    acc = b._account("kc")
    shown = b.show_account("kc")
    assert shown["credential"] == {"reference": f"keychain {KEYCHAIN_SERVICE}/{keychain_account(acc)}",
                                   "present": False, "well_formed": False}
    assert (shown["agents"], shown["role_defaults"]) == (["claude-kc"], ["executor"])
    assert b.verify_account("kc")["ok"] is False
    store(fake_security, acc, "ghp_wrong")
    assert b.verify_account("kc")["credential"] == {**shown["credential"], "present": True}
    store(fake_security, acc, TOKEN_A)
    out = b.verify_account("kc")
    assert out["credential"]["well_formed"] is True
    assert out["ok"] is False  # the claude binary is missing, so the CLI login check fails
    assert TOKEN_A not in json.dumps(out) + json.dumps(b.show_account("kc"))
    with pytest.raises(ValueError):
        b.show_account("nope")
