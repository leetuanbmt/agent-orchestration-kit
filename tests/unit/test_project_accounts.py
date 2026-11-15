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
            "five_hour_pct": None, "weekly_pct": None, "login": None, **over}
    return cli._project_account_add(argparse.Namespace(**args))


def load(root):
    return orchestrator.load(root, load_config(root))


# @pt.id KIT-PROJ-ACCT-001-add
# @pt.item project-account-add writes only the account to accounts.yaml and prints the agent to paste
# @pt.condition a project with an orchestrator.yaml whose executor role has a default; add a keychain anthropic account with --executor
# @pt.expected orchestrator.yaml is byte-identical; accounts.yaml holds only `accounts`; the merged config has the account (credential keychain, its own new 0700 home) and no new agent; the result's paste has the agent stanza and the roles.executor line
# @pt.target bridge.cli._project_account_add, bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy it also wrote the agent and the executor order into accounts.yaml
def test_add_account_and_executor(project):
    root, yaml_file = project
    before = yaml_file.read_bytes()
    out = add("claude-thuyseatch", credential="keychain", executor=True)
    assert yaml_file.read_bytes() == before
    assert out["agent"] == "agent-claude-thuyseatch"
    assert out["next"] == "agent-bridge account-set-token claude-thuyseatch"
    managed = orchestrator.load_managed(root / ".ai" / orchestrator.MANAGED_FILE)
    assert set(managed) == {"accounts"}
    assert out["paste"]["agents"].startswith("agent-claude-thuyseatch: {")
    assert '"adapter": "claude-cli"' in out["paste"]["agents"]
    assert "roles.executor" in out["paste"]
    orch = load(root)
    acc = orch.accounts["claude-thuyseatch"]
    assert (acc.provider, acc.credential) == ("anthropic", "keychain")
    home = Path(acc.home)
    assert home.is_dir() and oct(home.stat().st_mode & 0o777) == "0o700"
    assert "agent-claude-thuyseatch" not in orch.agents
    assert "roles.executor" not in add("other")["paste"]


# @pt.id KIT-PROJ-ACCT-008-parse-pure
# @pt.item parse() never mutates its managed argument, so a written accounts.yaml keeps `login` only
# @pt.condition parse a config with a managed {login: reg1} account and a registry; then project-account-add --login reg1 and load the written files
# @pt.expected the managed dict equals a deepcopy taken before; accounts.yaml has no provider or home for the login account and orchestrator.load succeeds
# @pt.target bridge.orchestrator.parse, bridge.cli._write_managed
# @pt.method unit
# @pt.legacy parse filled provider and home into the caller's dict, which was then written out
def test_parse_does_not_mutate_managed(project):
    import copy

    from bridge.accounts import register
    root, yaml_file = project
    reg = register("reg1", "anthropic")
    managed = {"accounts": {"reg1": {"login": "reg1", "limits": {"five_hour_pct": 70.0}}}}
    before = copy.deepcopy(managed)
    registry = {"reg1": {"provider": "anthropic", "home": str(reg.home)}}
    orchestrator.parse(yaml_file.read_text(), str(yaml_file), registry, managed, "m.yaml")
    assert managed == before
    add("reg1", login="reg1", provider=None, five_hour_pct=70.0)
    written = orchestrator.load_managed(root / ".ai" / orchestrator.MANAGED_FILE)
    assert written == before
    assert load(root).accounts["reg1"].home == str(reg.home)


# @pt.id KIT-PROJ-ACCT-007-login
# @pt.item project-account-add --login references a registry login
# @pt.condition registry login reg1 (anthropic); add reg1 as a project account with --login and --five-hour-pct 70; add the same name again; add with a mismatching --provider; add an unknown login
# @pt.expected accounts.yaml holds {login: reg1, limits}; no home is created; the loaded account has the registry home and the 70 limit; the implicit registry name was accepted; a name now in accounts.yaml, a mismatch and an unknown login are refused
# @pt.target bridge.cli._project_account_add
# @pt.method unit
# @pt.legacy none
def test_add_with_login(project):
    from bridge.accounts import register
    root, _ = project
    reg = register("reg1", "anthropic")
    # an agent that uses reg1 makes it an implicit project account
    ai = root / ".ai"
    text = (ai / "orchestrator.yaml").read_text().replace(
        "agents:\n", "agents:\n  a-reg: {adapter: claude-cli, account: reg1}\n")
    (ai / "orchestrator.yaml").write_text(text)
    assert "reg1" in load(root).accounts_from_registry
    homes_before = sorted(p.name for p in (Path(os.environ["HOME"]) / ".agent-bridge").rglob("*"))
    out = add("reg1", login="reg1", provider=None, five_hour_pct=70.0)
    managed = orchestrator.load_managed(ai / orchestrator.MANAGED_FILE)
    assert managed == {"accounts": {"reg1": {"login": "reg1", "limits": {"five_hour_pct": 70.0}}}}
    assert out["home"] == str(reg.home)
    assert sorted(p.name for p in (Path(os.environ["HOME"]) / ".agent-bridge").rglob("*")) == homes_before
    acc = load(root).accounts["reg1"]
    assert acc.home == str(reg.home) and acc.limits.five_hour_pct == 70.0
    with pytest.raises(ValueError, match="already exists"):
        add("reg1", login="reg1", provider=None)
    with pytest.raises(ValueError, match="does not match"):
        add("reg2", login="reg1", provider="openai")
    with pytest.raises(ValueError, match="unknown login"):
        add("reg3", login="nope", provider=None)


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
# @pt.item project-account-remove drops the account (and legacy agent entries), keeps the home, and refuses accounts it does not own
# @pt.condition an account added then removed with the typed confirmation; a legacy accounts.yaml with an agent and executor entry for an account; the orchestrator.yaml account cx; an implicit registry account
# @pt.expected the account is gone and its home stays; the legacy agent and executor entries are dropped; cx and the registry account are refused with the account_owner text
# @pt.target bridge.cli._project_account_remove
# @pt.method unit
# @pt.legacy none
def test_remove_keeps_home(project, monkeypatch):
    from bridge.accounts import register
    root, _ = project
    out = add("claude-thuyseatch", executor=True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "claude-thuyseatch")
    gone = cli._project_account_remove(argparse.Namespace(name="claude-thuyseatch"))
    assert gone["agents_removed"] == []
    assert Path(out["home"]).is_dir() and gone["home_kept"] == out["home"]
    orch = load(root)
    assert "claude-thuyseatch" not in orch.accounts
    assert orch.role("executor").agents == ["codex"]

    managed = root / ".ai" / orchestrator.MANAGED_FILE
    add("old")
    data = orchestrator.load_managed(managed)
    data |= {"agents": {"agent-old": {"adapter": "claude-cli", "account": "old"}},
             "executor": ["agent-old"]}
    managed.write_text(__import__("yaml").safe_dump(data))
    monkeypatch.setattr(builtins, "input", lambda prompt: "old")
    gone = cli._project_account_remove(argparse.Namespace(name="old"))
    assert gone["agents_removed"] == ["agent-old"]
    assert orchestrator.load_managed(managed) == {"accounts": {}}

    with pytest.raises(ValueError, match="accounts.cx in .ai/orchestrator.yaml"):
        cli._project_account_remove(argparse.Namespace(name="cx"))
    register("reg1", "anthropic")
    ai = root / ".ai"
    (ai / "orchestrator.yaml").write_text((ai / "orchestrator.yaml").read_text().replace(
        "agents:\n", "agents:\n  a-reg: {adapter: claude-cli, account: reg1}\n"))
    with pytest.raises(ValueError, match="the machine registry"):
        cli._project_account_remove(argparse.Namespace(name="reg1"))


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
    with pytest.raises(ValueError, match="accounts.cx in .ai/orchestrator.yaml"):
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


# @pt.id KIT-PROJ-ACCT-001-opt-in-writers
# @pt.item project-account-add never sets allow_write for opencode or agy
# @pt.condition add a google and an opencode account
# @pt.expected the pasted agent stanza has no allow_write (the human opts in); a claude account's does
# @pt.target bridge.cli._project_account_add, bridge.orchestrator.OPT_IN_WRITE_ADAPTERS
# @pt.method unit
# @pt.legacy none
def test_opt_in_writers_get_no_allow_write(project):
    assert "allow_write" not in add("gg", provider="google")["paste"]["agents"]
    assert "allow_write" not in add("oo", provider="opencode")["paste"]["agents"]
    assert "allow_write" in add("cc", provider="anthropic")["paste"]["agents"]
