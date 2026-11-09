"""verify_sandbox / verify_writable: `.ai/config.json` owns them, orchestrator.yaml is deprecated."""

from __future__ import annotations

import pytest

from bridge import orchestrator
from bridge.config import BridgeConfig

BASE = """version: 1
agents:
  codex: {adapter: codex-cli, account: cx}
roles:
  executor: codex
  reviewer: codex
"""


@pytest.fixture(autouse=True)
def no_registry(monkeypatch):
    monkeypatch.setattr(orchestrator, "load_registry", lambda: {})


def project(tmp_path, workflow: str | None = None):
    """A project with an orchestrator.yaml (workflow block optional)."""
    ai = tmp_path / ".ai"
    ai.mkdir()
    (ai / "accounts.yaml").write_text("accounts: {cx: {provider: openai}}\n")
    (ai / "orchestrator.yaml").write_text(BASE + (workflow or ""))
    return tmp_path


# @pt.id T0144-config-only
# @pt.item config.json owns verify_* for orchestrator.yaml and legacy projects
# @pt.condition verify_sandbox/verify_writable set only in BridgeConfig; once with an orchestrator.yaml, once without
# @pt.expected both projects load with those effective workflow values and no deprecation entry
# @pt.target bridge.orchestrator.load, bridge.orchestrator.with_verify_settings
# @pt.method unit
# @pt.legacy orchestrator.yaml workflow.verify_* (deprecated location)
def test_config_json_only(tmp_path):
    cfg = BridgeConfig(verify_sandbox="strict", verify_writable=["/var/cache/x"])
    with_yaml = orchestrator.load(project(tmp_path), cfg)
    assert with_yaml.workflow.verify_sandbox == "strict"
    assert with_yaml.workflow.verify_writable == ["/var/cache/x"]
    assert orchestrator.deprecated(with_yaml, cfg) == []
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    old = orchestrator.load(legacy_dir, cfg)
    assert old.source.startswith("legacy")
    assert old.workflow.verify_sandbox == "strict"
    assert old.workflow.verify_writable == ["/var/cache/x"]


# @pt.id T0144-yaml-only
# @pt.item orchestrator.yaml workflow.verify_* still loads, as deprecated
# @pt.condition verify_* set only in orchestrator.yaml workflow
# @pt.expected effective values are the yaml ones; deprecated() lists both keys with the move hint
# @pt.target bridge.orchestrator.load, bridge.orchestrator.deprecated
# @pt.method unit
# @pt.legacy orchestrator.yaml workflow.verify_* (deprecated location)
def test_orchestrator_only(tmp_path):
    root = project(tmp_path, "workflow: {verify_sandbox: none, verify_writable: [/tmp/w]}\n")
    cfg = BridgeConfig()
    orch = orchestrator.load(root, cfg)
    assert orch.workflow.verify_sandbox == "none"
    assert orch.workflow.verify_writable == ["/tmp/w"]
    assert orch.verify_in_orchestrator == ["verify_sandbox", "verify_writable"]
    notes = orchestrator.deprecated(orch, cfg)
    assert ('workflow.verify_sandbox in orchestrator.yaml; move it to .ai/config.json as '
            '"verify_sandbox"') in notes
    assert ('workflow.verify_writable in orchestrator.yaml; move it to .ai/config.json as '
            '"verify_writable"') in notes


# @pt.id T0144-both-error
# @pt.item the same key in both files is an error
# @pt.condition verify_sandbox set in config.json and in orchestrator.yaml workflow, with different values
# @pt.expected ValueError naming the key, .ai/config.json and the orchestrator.yaml path; nothing is overridden
# @pt.target bridge.orchestrator.with_verify_settings
# @pt.method unit
# @pt.legacy none
def test_same_key_in_both_files(tmp_path):
    root = project(tmp_path, "workflow: {verify_sandbox: none}\n")
    with pytest.raises(ValueError) as e:
        orchestrator.load(root, BridgeConfig(verify_sandbox="strict"))
    msg = str(e.value)
    assert "verify_sandbox" in msg and ".ai/config.json" in msg
    assert str(root / ".ai" / "orchestrator.yaml") in msg


# @pt.id T0144-split-keys
# @pt.item different keys in each file are both applied
# @pt.condition verify_sandbox in orchestrator.yaml, verify_writable in config.json
# @pt.expected both effective values present
# @pt.target bridge.orchestrator.load
# @pt.method unit
# @pt.legacy orchestrator.yaml workflow.verify_* (deprecated location)
def test_different_keys_in_each(tmp_path):
    root = project(tmp_path, "workflow: {verify_sandbox: strict}\n")
    orch = orchestrator.load(root, BridgeConfig(verify_writable=["/srv/cache"]))
    assert orch.workflow.verify_sandbox == "strict"
    assert orch.workflow.verify_writable == ["/srv/cache"]


# @pt.id T0144-relative-refused
# @pt.item a relative verify_writable from config.json is refused
# @pt.condition config.json verify_writable ["relative/dir"]
# @pt.expected ValueError naming .ai/config.json and the absolute-path rule
# @pt.target bridge.orchestrator.with_verify_settings, WorkflowConfig._stages
# @pt.method unit
# @pt.legacy none
def test_relative_writable_refused(tmp_path):
    with pytest.raises(ValueError) as e:
        orchestrator.load(project(tmp_path), BridgeConfig(verify_writable=["relative/dir"]))
    assert ".ai/config.json" in str(e.value) and "absolute" in str(e.value)


# @pt.id T0144-defaults
# @pt.item defaults when neither file sets verify_*
# @pt.condition no verify_* anywhere
# @pt.expected auto and []; no deprecation entry
# @pt.target bridge.orchestrator.load
# @pt.method unit
# @pt.legacy none
def test_defaults(tmp_path):
    cfg = BridgeConfig()
    orch = orchestrator.load(project(tmp_path), cfg)
    assert orch.workflow.verify_sandbox == "auto" and orch.workflow.verify_writable == []
    assert orchestrator.deprecated(orch, cfg) == []


# @pt.id T0144-migration-plan
# @pt.item migration_plan keeps validating when config.json sets verify_*
# @pt.condition config.json sets verify_sandbox, orchestrator.yaml sets verify_writable
# @pt.expected plan is produced, leaves workflow.verify_writable in orchestrator.yaml, and no old-layout error
# @pt.target bridge.orchestrator.migration_plan
# @pt.method unit
# @pt.legacy orchestrator.yaml workflow.verify_* (deprecated location)
def test_migration_plan_with_verify_settings(tmp_path):
    root = project(tmp_path, "workflow: {verify_writable: [/tmp/w]}\n")
    plan = orchestrator.migration_plan(root, BridgeConfig(verify_sandbox="strict"))
    assert plan["orchestrator_yaml"]["workflow"] == {"verify_writable": ["/tmp/w"]}
