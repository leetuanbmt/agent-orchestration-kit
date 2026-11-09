"""`agent-bridge config-set` / `config-unset`: one key, in the file that owns it, validated first."""

from __future__ import annotations

import argparse
import asyncio
import json
from unittest.mock import Mock

import pytest
import yaml

from bridge import cli, orchestrator
from bridge.config import load_config

ORCH = """version: 1
agents:
  codex: {adapter: codex-cli, account: cx}
  codex-b: {adapter: codex-cli, account: cx}
roles:
  executor: {agents: [codex, codex-b], default: codex}
  reviewer: codex
workflow: {stages: [execute, test]}
"""
ACCOUNTS = """accounts:
  cx: {provider: openai, home: '%(home)s/cx', limits: {weekly_pct: 80}}
"""
ORCH_WITH_ACCOUNT = ORCH.replace("agents:\n", "accounts:\n  ox: {provider: openai, home: "
                                 "'%(home)s/ox'}\nagents:\n", 1)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    ai = root / ".ai"
    ai.mkdir(parents=True)
    home = str(tmp_path / "homes")
    (ai / "orchestrator.yaml").write_text(ORCH)
    (ai / "accounts.yaml").write_text(ACCOUNTS % {"home": home})
    (ai / "config.json").write_text('{\n  "retry_backoff_s": 0\n}\n')
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    return root


TTY = {"on": True}


def change(key, value=None, apply=False, unset=False):
    # Patched at call time: pytest's capture swaps sys.stdout after fixtures have run.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli.sys, "stdout", Mock(isatty=lambda: TTY["on"]))
        mp.setattr(cli.sys, "stdin", Mock(isatty=lambda: TTY["on"]))
        cmd = "config-unset" if unset else "config-set"
        ns = argparse.Namespace(cmd=cmd, key=key, value=value, apply=apply)
        return asyncio.run(cli._run(ns))


def snapshot(root):
    return {p.name: p.read_bytes() for p in (root / ".ai").iterdir() if p.is_file()}


def load(root):
    return orchestrator.load(root, load_config(root))


def refused(root, *args, match=None, **kw):
    before = snapshot(root)
    backups = sorted((root / ".ai").glob("backup-*"))
    with pytest.raises(ValueError, match=match):
        change(*args, apply=True, **kw)
    assert snapshot(root) == before
    assert sorted((root / ".ai").glob("backup-*")) == backups


# @pt.id KIT-CONFIG-SET-001
# @pt.item config-set roles.executor.default previews, then writes orchestrator.yaml with a backup
# @pt.condition set the executor default to codex-b without --apply, then with --apply
# @pt.expected the preview writes nothing; apply changes the file, keeps the old one in .ai/backup-<stamp>/, and orchestrator.load sees the new default first
# @pt.target bridge.cli._config_change
# @pt.method unit
# @pt.legacy executor defaults were edited by hand in the YAML
def test_set_default_preview_and_apply(project):
    before = snapshot(project)
    out = change("roles.executor.default", "codex-b")
    assert out["applied"] is False and out["old"] == "codex" and out["new"] == "codex-b"
    assert snapshot(project) == before and not list((project / ".ai").glob("backup-*"))
    out = change("roles.executor.default", "codex-b", apply=True)
    assert out["file"] == ".ai/orchestrator.yaml" and out["applied"] is True
    backup = list((project / ".ai").glob("backup-*"))
    assert len(backup) == 1 and (backup[0] / "orchestrator.yaml").read_text() == ORCH
    assert not list((project / ".ai").glob("*.tmp"))
    assert load(project).roles["executor"].agents[0] == "codex-b"


# @pt.id KIT-CONFIG-SET-002
# @pt.item account keys change accounts.yaml; an account declared in orchestrator.yaml is refused with its owner
# @pt.condition set limits.weekly_pct and max_concurrency on cx; then declare ox in orchestrator.yaml and set a key on it
# @pt.expected accounts.yaml has the new values and loads; the ox change is refused naming orchestrator.yaml and config-migrate, nothing written
# @pt.target bridge.cli._config_change
# @pt.method unit
# @pt.legacy accounts were edited by hand
def test_account_keys(project, tmp_path):
    change("accounts.cx.limits.weekly_pct", "90", apply=True)
    out = change("accounts.cx.max_concurrency", "2", apply=True)
    assert out["file"] == ".ai/accounts.yaml"
    acc = load(project).accounts["cx"]
    assert acc.limits.weekly_pct == 90 and acc.max_concurrency == 2
    ai = project / ".ai"
    (ai / "orchestrator.yaml").write_text(ORCH_WITH_ACCOUNT % {"home": tmp_path / "homes"})
    refused(project, "accounts.ox.max_concurrency", "2", match="orchestrator.yaml.*config-migrate")


# @pt.id KIT-CONFIG-SET-003
# @pt.item a config.json setting carries the security flag; an invalid value is refused
# @pt.condition set verify_sandbox strict with --apply; then bogus
# @pt.expected config.json is JSON with indent 2, a trailing newline and key order kept; the output has security and the tracked-file line; bogus is refused with nothing written
# @pt.target bridge.cli._config_change
# @pt.method unit
# @pt.legacy none
def test_security_setting_and_invalid_value(project):
    out = change("verify_sandbox", "strict", apply=True)
    assert out["file"] == ".ai/config.json" and out["security"] is True
    assert "applies to every clone" in out["security_note"]
    assert (project / ".ai" / "config.json").read_text() == (
        '{\n  "retry_backoff_s": 0,\n  "verify_sandbox": "strict"\n}\n')
    assert "security" not in change("default_timeout_s", "60")
    refused(project, "verify_sandbox", "bogus")


# @pt.id KIT-CONFIG-SET-004
# @pt.item a change may not create a deprecated or duplicate setting
# @pt.condition set workflow.verify_sandbox auto; set verify_sandbox when workflow already has it
# @pt.expected both refused, nothing written
# @pt.target bridge.cli._config_change
# @pt.method unit
# @pt.legacy verify settings could live in orchestrator.yaml
def test_deprecated_layout_refused(project):
    refused(project, "workflow.verify_sandbox", "auto", match="outdated layout")
    refused(project, "max_attempts", "2", match="outdated layout")


# @pt.id KIT-CONFIG-SET-005
# @pt.item config-unset removes keys and empty parents; unchanged values and missing keys write nothing
# @pt.condition unset roles.executor.default and accounts.cx.limits; set an equal value; unset a missing key
# @pt.expected keys are gone and the config loads; the other two report changed False and leave files byte-identical
# @pt.target bridge.cli._config_change
# @pt.method unit
# @pt.legacy none
def test_unset_and_unchanged(project):
    out = change("roles.executor.default", apply=True, unset=True)
    assert out["removed"] is True and out["old"] == "codex"
    assert "default" not in yaml.safe_load((project / ".ai/orchestrator.yaml").read_text())[
        "roles"]["executor"]
    change("accounts.cx.limits", apply=True, unset=True)
    assert "limits" not in yaml.safe_load((project / ".ai/accounts.yaml").read_text())["accounts"]["cx"]
    load(project)
    before = snapshot(project)
    assert change("workflow.max_rework", "2")["changed"] is True
    assert change("roles.reviewer", "codex", apply=True)["changed"] is False
    assert change("roles.executor.default", apply=True, unset=True)["changed"] is False
    assert snapshot(project) == before


# @pt.id KIT-CONFIG-SET-006
# @pt.item invalid keys and values are refused before any write
# @pt.condition unknown first segment, unknown agent in a role, default with strategy quota, secret-looking keys
# @pt.expected each raises ValueError naming the problem; files unchanged
# @pt.target bridge.cli._config_change
# @pt.method unit
# @pt.legacy none
def test_refusals(project):
    refused(project, "bogus.key", "1", match="accounts.*roles.*verify_sandbox")
    refused(project, "roles.executor.agents", "[codex, nobody]", match="nobody")
    refused(project, "roles.executor.strategy", "quota", match="default")
    refused(project, "accounts.cx.api_token", "abc", match="secret")
    refused(project, "accounts.cx.password", None, unset=True, match="secret")
    refused(project, "roles.executor.default", "{a: [", match="YAML")


# @pt.id KIT-CONFIG-SET-007
# @pt.item values are YAML, a bare-string role grows a mapping, token_env is allowed
# @pt.condition set roles.reviewer.agents to a list, an account's token_env, a mapping value and a bool
# @pt.expected reviewer becomes {agents: [...]}; token_env is written; the mapping and bool load
# @pt.target bridge.cli._config_change
# @pt.method unit
# @pt.legacy none
def test_value_forms(project):
    change("roles.reviewer.agents", "[codex-b, codex]", apply=True)
    assert yaml.safe_load((project / ".ai/orchestrator.yaml").read_text())["roles"][
        "reviewer"] == {"agents": ["codex-b", "codex"]}
    change("accounts.cx.limits", "{five_hour_pct: 70}", apply=True)
    assert load(project).accounts["cx"].limits.five_hour_pct == 70
    change("routing.quota_fallback.enabled", "true", apply=True)
    assert load(project).routing.quota_fallback.enabled is True
    change("accounts.an", "{provider: anthropic, home: %s/an}" % (project.parent / "homes"),
           apply=True)
    out = change("accounts.an.token_env", "AGENT_BRIDGE_CLAUDE_AN_TOKEN", apply=True)
    assert out["new"] == "AGENT_BRIDGE_CLAUDE_AN_TOKEN"
    assert load(project).accounts["an"].token_env == "AGENT_BRIDGE_CLAUDE_AN_TOKEN"


# @pt.id KIT-CONFIG-SET-008
# @pt.item both commands need a terminal; legacy mode accepts only config.json keys
# @pt.condition run through main() without a tty; then in a project without orchestrator.yaml
# @pt.expected exit code 2 without a tty; legacy: a config.json key is written, roles.* is refused with the config-init hint
# @pt.target bridge.cli.main, bridge.cli._config_change
# @pt.method unit
# @pt.legacy none
def test_human_only_and_legacy(project, monkeypatch):
    before = snapshot(project)
    for argv in (["config-set", "workflow.max_rework", "1", "--apply"],
                 ["config-unset", "workflow.max_rework", "--apply"]):
        monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: False))
        with pytest.raises(SystemExit) as e:
            cli.main(argv)
        assert e.value.code == 2
    assert snapshot(project) == before
    (project / ".ai/orchestrator.yaml").unlink()
    (project / ".ai/accounts.yaml").unlink()
    out = change("default_timeout_s", "120", apply=True)
    assert json.loads((project / ".ai/config.json").read_text())["default_timeout_s"] == 120
    assert out["file"] == ".ai/config.json"
    refused(project, "roles.executor.default", "codex", match="config-init")
    refused(project, "accounts.cx.max_concurrency", "2", match="config-init")
