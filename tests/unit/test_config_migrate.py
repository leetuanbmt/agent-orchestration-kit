"""`agent-bridge config-migrate`: the one-owner layout with an unchanged effective config."""

from __future__ import annotations

import argparse
import asyncio
import json
from unittest.mock import Mock

import pytest
import yaml

from bridge import cli, orchestrator
from bridge.accounts import load_registry
from bridge.config import BridgeConfig
from bridge.registry import registry_dir

ORCH = """# my comment
version: 1
accounts:
  printless004:
    provider: openai
    home: '%(home)s/print'
    max_concurrency: 2
    limits: {five_hour_pct: 70, weekly_pct: 80}
agents:
  codex: {adapter: codex-cli, account: printless004, allow_write: true}
  agent-ntmt: {adapter: claude-cli, account: ntmt100199, allow_write: true}
  agy: {adapter: agy-cli, account: "leetuanbmt@google"}
roles:
  executor: {agents: [codex], default: codex}
  reviewer: agy
workflow: {stages: [execute, test]}
"""

MANAGED = """accounts:
  claude-thuyseatch: {provider: anthropic, home: '%(home)s/ct'}
  vydnt: {provider: anthropic, home: '%(home)s/vy', limits: {five_hour_pct: 60}}
agents:
  agent-claude-thuyseatch: {adapter: claude-cli, account: claude-thuyseatch, allow_write: true}
  agent-vydnt: {adapter: claude-cli, account: vydnt, allow_write: true}
executor: [agent-vydnt, agent-claude-thuyseatch]
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    ai = root / ".ai"
    ai.mkdir(parents=True)
    home = str(tmp_path / "homes")
    reg = registry_dir()
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "accounts.json").write_text(json.dumps({
        "ntmt100199": {"provider": "anthropic", "home": f"{home}/nt", "max_concurrency": 3,
                       "limits": {"five_hour_pct": 85, "weekly_pct": 90}},
        "leetuanbmt@google": {"provider": "google", "home": None},
    }))
    (ai / "orchestrator.yaml").write_text(ORCH % {"home": home})
    (ai / "accounts.yaml").write_text(MANAGED % {"home": home})
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    monkeypatch.setattr(cli, "load_config", lambda root: BridgeConfig())
    return root


def tty(monkeypatch, value=True):
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: value))
    monkeypatch.setattr(cli.sys, "stdout", Mock(isatty=lambda: value))


def run(apply):
    return asyncio.run(cli._run(argparse.Namespace(cmd="config-migrate", apply=apply)))


def snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in (root / ".ai").iterdir() if p.is_file()}


# @pt.id T0142-plan
# @pt.item migration_plan builds the one-owner layout from a live-shaped project
# @pt.condition orchestrator account with limits, accounts.yaml accounts/agents/executor, dict executor role with default, agents using registry accounts (limits 85/90 and name@google)
# @pt.expected same model_dump; accounts_yaml has only accounts with login entries carrying registry limits; orchestrator_yaml has no accounts; provenance lists empty
# @pt.target bridge.orchestrator.migration_plan
# @pt.method unit
# @pt.legacy none
def test_plan_keeps_effective_config(project):
    cfg = BridgeConfig()
    current = orchestrator.load(project, cfg)
    assert current.accounts_in_orchestrator and current.accounts_from_registry
    plan = orchestrator.migration_plan(project, cfg)
    assert plan["changed"] is True and plan["moves"]
    accounts = plan["accounts_yaml"]
    assert set(accounts) == {"accounts"}
    assert accounts["accounts"]["ntmt100199"] == {
        "login": "ntmt100199", "max_concurrency": 3,
        "limits": {"five_hour_pct": 85, "weekly_pct": 90}}
    assert accounts["accounts"]["leetuanbmt@google"] == {"login": "leetuanbmt@google"}
    assert accounts["accounts"]["printless004"]["limits"] == {"five_hour_pct": 70,
                                                              "weekly_pct": 80}
    assert "accounts" not in plan["orchestrator_yaml"]
    assert {"agent-vydnt", "agent-claude-thuyseatch"} <= set(plan["orchestrator_yaml"]["agents"])
    planned = orchestrator.parse(yaml.safe_dump(plan["orchestrator_yaml"]), "o", load_registry(),
                                 plan["accounts_yaml"], "a")
    assert planned.model_dump() == current.model_dump()
    assert not (planned.accounts_in_orchestrator or planned.accounts_from_registry
                or planned.agents_in_accounts_file)


# @pt.id T0142-preview
# @pt.item without --apply nothing is written
# @pt.condition run config-migrate in a terminal without --apply
# @pt.expected the result carries both new texts and a note; file bytes and mtimes are unchanged and no backup exists
# @pt.target bridge.cli._config_migrate
# @pt.method unit
# @pt.legacy none
def test_preview_writes_nothing(project, monkeypatch):
    tty(monkeypatch)
    before = snapshot(project)
    out = run(False)
    assert out["applied"] is False and out["changed"] is True
    assert ".ai/accounts.yaml" in out and ".ai/orchestrator.yaml" in out
    assert "Nothing was written" in out["note"]
    assert snapshot(project) == before
    assert not list((project / ".ai").glob("backup-*"))


# @pt.id T0142-apply
# @pt.item --apply backs up, writes both files and is idempotent; the registry is untouched
# @pt.condition apply twice
# @pt.expected backup holds both originals; load gives the same model_dump; the second run reports changed false and writes nothing; accounts.json is byte-identical
# @pt.target bridge.cli._config_migrate
# @pt.method unit
# @pt.legacy none
def test_apply(project, monkeypatch):
    tty(monkeypatch)
    ai = project / ".ai"
    registry = registry_dir() / "accounts.json"
    reg_before = registry.read_bytes()
    orch_before, managed_before = (ai / "orchestrator.yaml").read_bytes(), \
        (ai / "accounts.yaml").read_bytes()
    current = orchestrator.load(project, BridgeConfig()).model_dump()
    out = run(True)
    assert out["applied"] is True and out["moves"]
    backups = list(ai.glob("backup-*"))
    assert [str(b) for b in backups] == [out["backup"]]
    assert (backups[0] / "orchestrator.yaml").read_bytes() == orch_before
    assert (backups[0] / "accounts.yaml").read_bytes() == managed_before
    assert (ai / "accounts.yaml").read_text().startswith("# Project accounts.")
    after = orchestrator.load(project, BridgeConfig())
    assert after.model_dump() == current
    assert not (after.accounts_in_orchestrator or after.accounts_from_registry
                or after.agents_in_accounts_file)
    snap = snapshot(project)
    again = run(True)
    assert again["changed"] is False and again["applied"] is False
    assert snapshot(project) == snap
    assert len(list(ai.glob("backup-*"))) == 1
    assert registry.read_bytes() == reg_before


# @pt.id T0142-guards
# @pt.item legacy projects and non-terminal runs are refused
# @pt.condition no orchestrator.yaml; a non-tty run
# @pt.expected ValueError "nothing to migrate"; SystemExit 2 for the non-tty run
# @pt.target bridge.orchestrator.migration_plan, bridge.cli._run
# @pt.method unit
# @pt.legacy none
def test_legacy_and_non_tty(project, monkeypatch):
    tty(monkeypatch, False)
    with pytest.raises(SystemExit) as stop:
        run(False)
    assert stop.value.code == 2
    (project / ".ai" / "orchestrator.yaml").unlink()
    with pytest.raises(ValueError, match="nothing to migrate"):
        orchestrator.migration_plan(project, BridgeConfig())
