"""project-agent-add / project-agent-remove edit agents and roles in .ai/orchestrator.yaml."""

from __future__ import annotations

import argparse
import builtins
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from rich.console import Console

from bridge import cli, orchestrator, render
from bridge.config import load_config

YAML = """version: 1
accounts:
  cx: {provider: openai, home: '%(cx)s'}
  cl: {provider: anthropic, home: '%(cl)s'}
  gg: {provider: google, home: '%(gg)s'}
agents:
  codex: {adapter: codex-cli, account: cx, allow_write: true}
roles:
  executor: codex
  reviewer: {agents: [codex], default: codex}
workflow: {stages: [execute, test]}
"""


@pytest.fixture
def project(repo, monkeypatch):
    homes = Path(os.environ["HOME"]) / "homes"
    paths = {}
    for name in ("cx", "cl", "gg"):
        (homes / name).mkdir(parents=True)
        paths[name] = homes / name
    (repo / ".ai").mkdir()
    yaml_file = repo / ".ai" / orchestrator.ORCHESTRATOR_FILE
    yaml_file.write_text(YAML % paths)
    monkeypatch.chdir(repo)
    return repo, yaml_file


def add(agent, **over):
    args = {"agent": agent, "account": None, "adapter": None, "model": None, "allow_bash": False,
            "role": None, "default": False, "apply": False, **over}
    return cli._project_agent_add(argparse.Namespace(**args))


def remove(agent, apply=False):
    return cli._project_agent_remove(argparse.Namespace(agent=agent, apply=apply))


def backups(root):
    return sorted((root / ".ai").glob("backup-*"))


def text(renderable) -> str:
    console = Console(record=True, width=100)
    console.print(renderable)
    return console.export_text()


# @pt.id KIT-AGENT-CMD-001-add-preview-apply
# @pt.item project-agent-add previews by default and writes with --apply after a backup
# @pt.condition add a claude agent on account cl to role executor (a bare string) as default, first without then with --apply
# @pt.expected the preview writes nothing and shows the spec; --apply backs up the old file, the loaded config has a claude-cli agent with allow_write first in executor, and executor became a list
# @pt.target bridge.cli._project_agent_add
# @pt.method unit
# @pt.legacy hand-editing orchestrator.yaml after project-account-add
def test_add_preview_then_apply(project):
    root, yaml_file = project
    before = yaml_file.read_text()
    out = add("claude", account="cl", role="executor", default=True)
    assert out["changed"] and not out["applied"] and out["spec"]["allow_write"] is True
    assert yaml_file.read_text() == before and not backups(root)
    out = add("claude", account="cl", role="executor", default=True, apply=True)
    assert out["applied"] and len(backups(root)) == 1
    assert (backups(root)[0] / orchestrator.ORCHESTRATOR_FILE).read_text() == before
    orch = orchestrator.load(root, load_config(root))
    assert orch.agents["claude"].adapter == "claude-cli" and orch.agents["claude"].allow_write
    role = orch.role("executor")
    assert role.agents == ["claude", "codex"] and role.default == "claude"


# @pt.id KIT-AGENT-CMD-002-add-adapters
# @pt.item the adapter follows the account's provider; --allow-bash and the client adapter are checked
# @pt.condition add agents on a google account, with --allow-bash on claude and on codex, a client agent and a client agent with a model
# @pt.expected google gives a read-only agy-cli agent without allow_write; allow_bash warns on claude and is refused on codex; the client agent has no account; a client model is refused
# @pt.target bridge.cli._project_agent_add
# @pt.method unit
# @pt.legacy none
def test_add_adapters(project):
    gg = add("gem", account="gg", role="reviewer")["spec"]
    assert gg == {"adapter": "agy-cli", "account": "gg"}
    out = add("sh", account="cl", allow_bash=True)
    assert out["spec"]["allow_bash"] is True and "sandbox" in out["warnings"][0]
    with pytest.raises(ValueError, match="claude-cli"):
        add("cx2", account="cx", allow_bash=True)
    assert add("me", adapter="client", role="reviewer")["spec"] == {"adapter": "client"}
    with pytest.raises(ValueError, match="model"):
        add("me", adapter="client", model="m")


# @pt.id KIT-AGENT-CMD-003-add-refusals
# @pt.item project-agent-add refuses bad input and writes nothing
# @pt.condition an existing agent name, an unknown account, --default without --role, a role that would break validation (default on a quota role)
# @pt.expected each raises ValueError, orchestrator.yaml is unchanged and no backup exists even with --apply
# @pt.target bridge.cli._project_agent_add
# @pt.method unit
# @pt.legacy none
def test_add_refusals_write_nothing(project):
    root, yaml_file = project
    yaml_file.write_text(yaml_file.read_text().replace(
        "reviewer: {agents: [codex], default: codex}",
        "reviewer: {agents: [codex], strategy: quota}"))
    before = yaml_file.read_text()
    for name, over in (("codex", {"account": "cx"}), ("n", {"account": "nope"}),
                       ("n", {"account": "cx", "default": True}),
                       ("n", {"account": "cx", "role": "reviewer", "default": True})):
        with pytest.raises(ValueError):
            add(name, apply=True, **over)
        assert yaml_file.read_text() == before and not backups(root), (name, over)


# @pt.id KIT-AGENT-CMD-004-remove
# @pt.item project-agent-remove drops the agent from agents and every role, and refuses an emptied role
# @pt.condition add agent claude to executor as default and reviewer; remove it; remove codex whose executor role is a bare string
# @pt.expected claude is gone from agents and both roles and the default; removing codex names roles.executor and changes nothing
# @pt.target bridge.cli._project_agent_remove
# @pt.method unit
# @pt.legacy deleting the agent by hand
def test_remove_and_empty_role(project):
    root, yaml_file = project
    add("claude", account="cl", role="executor", default=True, apply=True)
    data = yaml.safe_load(yaml_file.read_text())
    data["roles"]["reviewer"] = {"agents": ["codex", "claude"], "default": "claude"}
    yaml_file.write_text(yaml.safe_dump(data, sort_keys=False))
    out = remove("claude", apply=True)
    assert out["changed"] and out["applied"]
    orch = orchestrator.load(root, load_config(root))
    assert "claude" not in orch.agents
    assert orch.role("executor").agents == ["codex"] and orch.role("reviewer").default is None
    before = yaml_file.read_text()
    with pytest.raises(ValueError, match="roles.executor"):
        remove("codex", apply=True)
    assert yaml_file.read_text() == before


# @pt.id KIT-AGENT-CMD-005-remove-account-flow
# @pt.item after project-agent-remove an account in accounts.yaml can be removed; its refusal names the agents
# @pt.condition project-account-add an account, project-agent-add an agent for it, project-account-remove, project-agent-remove, project-account-remove
# @pt.expected the first removal is refused naming the agent and project-agent-remove; the second succeeds; the printed account-add result points to project-agent-add
# @pt.target bridge.cli._project_account_remove, bridge.cli._project_account_add
# @pt.method unit
# @pt.legacy none
def test_account_remove_flow(project, monkeypatch):
    root, _ = project
    args = {"name": "extra", "provider": "anthropic", "home": None, "credential": None,
            "token_env": None, "credential_kind": "oauth_token", "agent": None, "model": None,
            "executor": False, "five_hour_pct": None, "weekly_pct": None, "login": None}
    out = cli._project_account_add(argparse.Namespace(**args))
    assert out["agent_command"] == ("agent-bridge project-agent-add agent-extra --account extra "
                                    "--role executor --apply")
    assert "agent-extra: {" in out["paste"]["agents"]
    assert "project-agent-add agent-extra" in text(render.account_added_view(out))
    add("agent-extra", account="extra", apply=True)
    monkeypatch.setattr(builtins, "input", lambda prompt: "extra")
    with pytest.raises(ValueError) as err:
        cli._project_account_remove(argparse.Namespace(name="extra"))
    assert "agent-extra" in str(err.value) and "project-agent-remove" in str(err.value)
    remove("agent-extra", apply=True)
    assert cli._project_account_remove(argparse.Namespace(name="extra"))["agents_removed"] == []


# @pt.id KIT-AGENT-CMD-006-unchanged-pinned
# @pt.item an unchanged result writes nothing; unfinished pinned tasks are listed, and an unreadable state is skipped
# @pt.condition remove an agent that is not declared; remove one with _pinned_tasks patched; call _pinned_tasks with no bridge state
# @pt.expected the first reports unchanged with no backup; the second lists the pinned task ids; the third returns []
# @pt.target bridge.cli._project_agent_remove, bridge.cli._pinned_tasks
# @pt.method unit
# @pt.legacy none
def test_unchanged_and_pinned(project, monkeypatch):
    root, yaml_file = project
    before = yaml_file.read_text()
    out = remove("ghost", apply=True)
    assert out["changed"] is False and yaml_file.read_text() == before and not backups(root)
    add("claude", account="cl", apply=True)
    monkeypatch.setattr(cli, "_pinned_tasks", lambda agent: ["T0001"])
    preview = remove("claude")
    assert preview["pinned_tasks"] == ["T0001"] and not preview["applied"]
    assert "T0001" in text(render.agent_change_view(preview))
    monkeypatch.undo()
    assert cli._pinned_tasks("claude") == []


# @pt.id KIT-AGENT-CMD-007-views
# @pt.item the readable view shows the change, warnings, the comments note and the next step
# @pt.condition render a previewed add with allow_bash, an applied remove and an unchanged result
# @pt.expected the preview shows the spec, the warning and --apply; the applied view shows the backup and config-check; unchanged says nothing written
# @pt.target bridge.render.agent_change_view
# @pt.method unit
# @pt.legacy none
def test_views(project):
    prev = text(render.agent_change_view(add("sh", account="cl", allow_bash=True, role="reviewer")))
    assert "sh:" in prev and "sandbox" in prev and "--apply" in prev
    assert "comments in .ai/orchestrator.yaml are not kept" in prev
    done = text(render.agent_change_view(add("sh", account="cl", apply=True)))
    assert "Backup" in done and "config-check" in done
    assert "nothing written" in text(render.agent_change_view(remove("ghost")))


# @pt.id KIT-AGENT-CMD-008-human-legacy
# @pt.item both commands are human only and refuse in legacy mode
# @pt.condition run each without a terminal; call each in a project without orchestrator.yaml
# @pt.expected exit 2 with "human only"; ValueError naming config-init
# @pt.target bridge.cli._run, bridge.cli._managed_paths
# @pt.method integration
# @pt.legacy none
def test_human_only_and_legacy(repo, monkeypatch, tmp_path):
    exe = os.path.join(os.path.dirname(sys.executable), "agent-bridge")
    for argv in (["project-agent-add", "x", "--account", "a"], ["project-agent-remove", "x"]):
        out = subprocess.run([exe, *argv], cwd=tmp_path, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
        assert out.returncode == 2 and "human only" in out.stderr, argv
    monkeypatch.chdir(repo)
    with pytest.raises(ValueError, match="config-init"):
        add("x", account="a")
    with pytest.raises(ValueError, match="config-init"):
        remove("x")
