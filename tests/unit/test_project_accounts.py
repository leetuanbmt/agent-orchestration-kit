"""Accounts added from the CLI into .ai/accounts.yaml, merged under orchestrator.yaml."""

from __future__ import annotations

import argparse
import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bridge import cli, orchestrator
from bridge.config import load_config

YAML = """version: 1
accounts:
  cx: {provider: openai, home: '%s'}
agents:
  codex: {adapter: codex-cli, account: cx}
roles:
  executor: {agents: [codex], default: codex}
workflow: {stages: [execute, test]}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    homes = Path(os.environ["HOME"]) / "homes"
    (homes / "cx").mkdir(parents=True)
    yaml_file = root / ".ai" / orchestrator.ORCHESTRATOR_FILE
    yaml_file.write_text(YAML % (homes / "cx"))
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    return root, yaml_file


def add(name, **over):
    args = {"name": name, "provider": "anthropic", "home": None, "credential": None,
            "token_env": None, "credential_kind": "oauth_token", "agent": None, "model": None, "executor": False,
            "five_hour_pct": None, "weekly_pct": None, **over}
    return cli._project_account_add(argparse.Namespace(**args))


def load(root):
    return orchestrator.load(root, load_config(root))


# @pt.id KIT-PROJ-ACCT-001-add
# @pt.item project-account-add declares an account and its agent without touching orchestrator.yaml
# @pt.condition a project with an orchestrator.yaml whose executor role has a default; add a keychain anthropic account with --executor
# @pt.expected orchestrator.yaml is byte-identical; the merged config has the account (credential keychain, its own new 0700 home), an allow_write claude-cli agent, executor order [default, new]
# @pt.target bridge.cli._project_account_add, bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_add_account_and_executor(project):
    root, yaml_file = project
    before = yaml_file.read_bytes()
    out = add("claude-thuyseatch", credential="keychain", executor=True)
    assert yaml_file.read_bytes() == before
    assert out["agent"] == "agent-claude-thuyseatch"
    assert out["next"] == "agent-bridge account-set-token claude-thuyseatch"
    orch = load(root)
    acc = orch.accounts["claude-thuyseatch"]
    assert (acc.provider, acc.credential) == ("anthropic", "keychain")
    home = Path(acc.home)
    assert home.is_dir() and oct(home.stat().st_mode & 0o777) == "0o700"
    agent = orch.agents["agent-claude-thuyseatch"]
    assert (agent.adapter, agent.account, agent.allow_write) == ("claude-cli", "claude-thuyseatch", True)
    assert orch.role("executor").agents == ["codex", "agent-claude-thuyseatch"]


# @pt.id KIT-PROJ-ACCT-002-refusals
# @pt.item project-account-add refuses duplicates, shared homes and invalid entries before writing
# @pt.condition a name orchestrator.yaml already declares; a home inside the project; a token_env outside the namespace
# @pt.expected each raises ValueError and .ai/accounts.yaml is never created
# @pt.target bridge.cli._project_account_add
# @pt.method unit
# @pt.legacy none
def test_add_refusals_write_nothing(project):
    root, _ = project
    managed = root / ".ai" / orchestrator.MANAGED_FILE
    for name, over in (("cx", {}), ("inside", {"home": str(root / "homes" / "x")}),
                       ("badtok", {"token_env": "GITHUB_TOKEN"})):
        with pytest.raises(ValueError):
            add(name, **over)
        assert not managed.exists(), name


# @pt.id KIT-PROJ-ACCT-003-precedence
# @pt.item an account in both files is an error, and managed errors name accounts.yaml
# @pt.condition the managed file declares cx (also in orchestrator.yaml) and a broken account
# @pt.expected the clash error names cx and both files; the broken entry's error names accounts.yaml
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_orchestrator_yaml_wins(project):
    root, yaml_file = project
    managed = {"accounts": {"cx": {"provider": "openai", "home": "/elsewhere"}}}
    with pytest.raises(ValueError) as clash:
        orchestrator.parse(yaml_file.read_text(), str(yaml_file), None, managed, "m.yaml")
    assert "'cx'" in str(clash.value) and str(yaml_file) in str(clash.value)
    assert "m.yaml" in str(clash.value)
    broken = {"accounts": {"bad": {"provider": "anthropic", "extra": 1}}}
    with pytest.raises(ValueError) as err:
        orchestrator.parse(yaml_file.read_text(), str(yaml_file), None, broken, "m.yaml")
    assert "m.yaml: accounts.bad.extra" in str(err.value)


# @pt.id KIT-PROJ-ACCT-004-remove
# @pt.item project-account-remove drops the account, its agent and executor entry, keeps the home
# @pt.condition an account added with --executor, then removed with the typed confirmation; then a name only orchestrator.yaml declares
# @pt.expected the merged config no longer has them, the home still exists, routing is back to [codex]; removing cx is refused
# @pt.target bridge.cli._project_account_remove
# @pt.method unit
# @pt.legacy none
def test_remove_keeps_home(project, monkeypatch):
    root, _ = project
    out = add("claude-thuyseatch", executor=True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "claude-thuyseatch")
    gone = cli._project_account_remove(argparse.Namespace(name="claude-thuyseatch"))
    assert gone["agents_removed"] == ["agent-claude-thuyseatch"]
    assert Path(out["home"]).is_dir() and gone["home_kept"] == out["home"]
    orch = load(root)
    assert "claude-thuyseatch" not in orch.accounts
    assert orch.role("executor").agents == ["codex"]
    with pytest.raises(ValueError, match="edited there"):
        cli._project_account_remove(argparse.Namespace(name="cx"))


# @pt.id KIT-PROJ-ACCT-006-limits
# @pt.item 5-hour and weekly limits on a CLI-managed account
# @pt.condition add with --five-hour-pct 80; set --weekly-pct 90; an out-of-range value; --clear; an account only orchestrator.yaml declares
# @pt.expected the merged config carries 80 then 80/90; 150 is refused and nothing changes; --clear removes both; the orchestrator.yaml account is refused
# @pt.target bridge.cli._project_account_limits, _project_account_add
# @pt.method unit
# @pt.legacy none
def test_window_limits(project):
    root, _ = project
    add("vydnt", credential="keychain", five_hour_pct=80.0)
    limits = load(root).accounts["vydnt"].limits
    assert (limits.five_hour_pct, limits.weekly_pct) == (80.0, None)

    def set_limits(name="vydnt", **over):
        args = {"name": name, "clear": False, "five_hour_pct": None, "weekly_pct": None, **over}
        return cli._project_account_limits(argparse.Namespace(**args))

    assert set_limits(weekly_pct=90.0)["limits"] == {"five_hour_pct": 80.0, "weekly_pct": 90.0}
    managed = root / ".ai" / orchestrator.MANAGED_FILE
    before = managed.read_text()
    with pytest.raises(ValueError):
        set_limits(weekly_pct=150.0)
    assert managed.read_text() == before
    assert set_limits(clear=True)["limits"] == {}
    assert load(root).accounts["vydnt"].limits is None
    with pytest.raises(ValueError, match="edited there"):
        set_limits(name="cx", weekly_pct=90.0)


# @pt.id KIT-PROJ-ACCT-005-human-only
# @pt.item Both commands are human-only
# @pt.condition the installed CLI runs each without a terminal
# @pt.expected exit 2 with "human only" before any file is read
# @pt.target bridge.cli._run
# @pt.method integration
# @pt.legacy none
def test_project_account_commands_need_terminal(tmp_path):
    exe = os.path.join(os.path.dirname(sys.executable), "agent-bridge")
    for argv in (["project-account-add", "x", "--provider", "anthropic"],
                 ["project-account-remove", "x"],
                 ["project-account-limits", "x", "--weekly-pct", "90"]):
        out = subprocess.run([exe, *argv], cwd=tmp_path, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
        assert out.returncode == 2 and "human only" in out.stderr, argv
