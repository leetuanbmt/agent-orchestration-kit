"""Per-account Claude tokens (`claude setup-token`) through token_env (audit AUTH-02)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bridge.accounts import CLAUDE_TOKEN_ENV, Account, account_env, login_status
from bridge.config import BridgeConfig
from bridge.executor import _CLAUDE_SECRET_READS
from bridge.orchestrator import AccountProfile
from bridge.security import clean_env


# @pt.id KIT-AUTH-02-token-per-account
# @pt.item Each Claude account's run gets its own token and no other
# @pt.condition two anthropic accounts with token_env AGENT_BRIDGE_CLAUDE_A_TOKEN and _B_TOKEN; a global CLAUDE_CODE_OAUTH_TOKEN is also set
# @pt.expected each run's env carries only its own token as CLAUDE_CODE_OAUTH_TOKEN; scrubbing hides both variables; an account without token_env gets none
# @pt.target bridge.accounts.account_env
# @pt.method unit
# @pt.legacy audit AUTH-02: one env token overrode every account's login
def test_token_per_account(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_CLAUDE_A_TOKEN", "sk-ant-oat01-a")
    monkeypatch.setenv("AGENT_BRIDGE_CLAUDE_B_TOKEN", "sk-ant-oat01-b")
    monkeypatch.setenv(CLAUDE_TOKEN_ENV, "global")
    import os
    base = clean_env(os.environ, BridgeConfig().env_deny, [])
    assert "AGENT_BRIDGE_CLAUDE_A_TOKEN" not in base and CLAUDE_TOKEN_ENV not in base
    a = Account("a", tmp_path / "a", "anthropic", "AGENT_BRIDGE_CLAUDE_A_TOKEN")
    b = Account("b", tmp_path / "b", "anthropic", "AGENT_BRIDGE_CLAUDE_B_TOKEN")
    plain = Account("c", tmp_path / "c", "anthropic")
    env_a, env_b, env_c = (account_env(acc, base) for acc in (a, b, plain))
    assert env_a[CLAUDE_TOKEN_ENV] == "sk-ant-oat01-a" and "AGENT_BRIDGE_CLAUDE_B_TOKEN" not in env_a
    assert env_b[CLAUDE_TOKEN_ENV] == "sk-ant-oat01-b" and "AGENT_BRIDGE_CLAUDE_A_TOKEN" not in env_b
    assert CLAUDE_TOKEN_ENV not in env_c
    assert env_a["CLAUDE_CONFIG_DIR"] == str(tmp_path / "a")


# @pt.id KIT-AUTH-02-token-missing
# @pt.item A configured token variable that is not set reads as logged out
# @pt.condition token_env names an unset variable, then one holding a non-Claude token
# @pt.expected login_status is False without starting the CLI, and the run env has no token
# @pt.target bridge.accounts.login_status, account_env
# @pt.method unit
# @pt.legacy none
def test_missing_token_is_logged_out(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_BRIDGE_CLAUDE_MISSING_TOKEN", raising=False)
    acc = Account("a", tmp_path, "anthropic", "AGENT_BRIDGE_CLAUDE_MISSING_TOKEN")
    cfg = BridgeConfig(claude_bin="/nonexistent/claude")
    assert login_status(cfg, acc) is False
    assert CLAUDE_TOKEN_ENV not in account_env(acc, {CLAUDE_TOKEN_ENV: "leaked"})
    monkeypatch.setenv("AGENT_BRIDGE_CLAUDE_MISSING_TOKEN", "ghp_not_a_claude_token")
    assert login_status(cfg, acc) is False  # a value of the wrong shape is never forwarded
    assert CLAUDE_TOKEN_ENV not in account_env(acc, {})


# @pt.id KIT-AUTH-02-token-config
# @pt.item token_env holds a variable name, for anthropic accounts only
# @pt.condition profiles with a valid name, a token-like value, another provider, CLAUDE_CODE_OAUTH_TOKEN, GITHUB_TOKEN and a name without _TOKEN
# @pt.expected only an anthropic AGENT_BRIDGE_CLAUDE_*_TOKEN name is accepted: its own namespace, hidden by the default scrub
# @pt.target bridge.orchestrator.AccountProfile
# @pt.method unit
# @pt.legacy none
def test_token_env_validation():
    assert AccountProfile(provider="anthropic",
                          token_env="AGENT_BRIDGE_CLAUDE_PRINTLESS_TOKEN").token_env
    for bad in ({"provider": "anthropic", "token_env": "sk-ant-oat01-abc"},
                {"provider": "openai", "token_env": "AGENT_BRIDGE_CLAUDE_X_TOKEN"},
                {"provider": "anthropic", "token_env": "CLAUDE_CODE_OAUTH_TOKEN"},
                {"provider": "anthropic", "token_env": "GITHUB_TOKEN"},  # another secret
                {"provider": "anthropic", "token_env": "AGENT_BRIDGE_CLAUDE_X"}):  # not scrubbed
        with pytest.raises(ValidationError):
            AccountProfile(**bad)


# @pt.id KIT-AUTH-02-yaml-read-deny
# @pt.item Claude agents cannot read the orchestrator config
# @pt.condition the fixed read denies passed to every Claude agent run
# @pt.expected they include .ai/orchestrator.yaml (anchored at the project root when the rule is built)
# @pt.target bridge.executor._CLAUDE_SECRET_READS
# @pt.method unit
# @pt.legacy none
def test_orchestrator_yaml_read_denied():
    assert ".ai/orchestrator.yaml" in _CLAUDE_SECRET_READS
