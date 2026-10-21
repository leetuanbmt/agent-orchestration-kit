"""Orchestrator config: schema validation, legacy mapping, account isolation and slots."""

import os
from pathlib import Path

import pytest

from bridge import cli, server
from bridge.accounts import Account, AccountLock, resolve_profile
from bridge.config import BridgeConfig
from bridge.executor import AGENT_ENV
from bridge.orchestrator import legacy, load, parse
from bridge.state import ConflictError

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

BASE = """
version: 1
accounts:
  cx: {provider: openai, home: /tmp/x}
  cl: {provider: anthropic, home: /tmp/y}
agents:
  codex: {adapter: codex-cli, account: cx}
  claude: {adapter: claude-cli, account: cl}
roles:
  executor: codex
  reviewer: claude
"""


# @pt.id KIT-ORCH-001-examples
# @pt.item Shipped example configurations
# @pt.condition Every examples/*.yaml is parsed
# @pt.expected All parse; the main example maps planner/reviewer to Claude, executor to two Codex agents
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_examples():
    files = sorted(EXAMPLES.glob("*.yaml"))
    assert len(files) >= 4
    for path in files:
        parse(path.read_text(), str(path))
    main = parse((EXAMPLES / "orchestrator.yaml").read_text())
    assert main.role("executor").agents == ["codex-a", "codex-b"]
    assert main.agent(main.role("reviewer").agents[0]).adapter == "claude-cli"
    assert main.max_attempts == 3


# @pt.id KIT-ORCH-002-errors
# @pt.item Descriptive validation errors
# @pt.condition Each invalid variant of a valid config
# @pt.expected parse raises ValueError naming the field and the problem
# @pt.target OrchestratorConfig._references, AgentConfig._adapter, WorkflowConfig._stages
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("old,new,message", [
    ("adapter: codex-cli, account: cx", "adapter: gemini-cli, account: cx", "unknown adapter"),
    ("adapter: codex-cli, account: cx", "adapter: codex-cli, account: nope", "unknown account"),
    ("adapter: codex-cli, account: cx", "adapter: codex-cli, account: cl", "is anthropic"),
    ("adapter: codex-cli, account: cx", "adapter: codex-cli", "needs an account"),
    ("adapter: codex-cli, account: cx", "adapter: codex-cli, account: cx, allow_bash: true",
     "allow_bash only applies"),
    ("executor: codex", "executor: claude", "role executor needs 'write'"),
    ("executor: codex", "executor: ghost", "unknown agent"),
    ("executor: codex", "planner: codex", "roles.executor is required"),
    ("  reviewer: claude\n", "", "review stage needs roles.reviewer"),
    ("cl: {provider: anthropic, home: /tmp/y}", "cl: {provider: azure}", "unsupported provider"),
    ("cl: {provider: anthropic, home: /tmp/y}", "cl: {provider: openai, home: /tmp/x}",
     "share one auth home"),
    ("version: 1", "version: 1\nextra: true", "extra"),
    ("version: 1", "version: 2", "version"),
    ("cx: {provider", "Bad_Name: {provider", "invalid account id"),
])
def test_errors(old, new, message):
    assert old in BASE
    with pytest.raises(ValueError, match=message):
        parse(BASE.replace(old, new))


# @pt.id KIT-ORCH-003-stages
# @pt.item Workflow stage rules
# @pt.condition Unknown, duplicate, out-of-order stages; no execute; no gate; plan without planner
# @pt.expected Each is rejected with a message; execute+test alone is valid
# @pt.target WorkflowConfig._stages, OrchestratorConfig._references
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("stages,message", [
    ("[execute, deploy]", "unknown stages"),
    ("[execute, test, test]", "duplicate"),
    ("[test, execute]", "keep the order"),
    ("[test, review]", "execute stage"),
    ("[execute]", "needs a gate"),
    ("[plan, execute, test]", "plan stage needs roles.planner"),
])
def test_stages(stages, message):
    with pytest.raises(ValueError, match=message):
        parse(BASE + f"workflow:\n  stages: {stages}\n")
    assert parse(BASE + "workflow:\n  stages: [execute, test]\n").workflow.stages == [
        "execute", "test"]


# @pt.id KIT-ORCH-004-yaml
# @pt.item Malformed files
# @pt.condition Invalid YAML and a non-mapping document
# @pt.expected Errors name the source file
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_yaml():
    with pytest.raises(ValueError, match="x.yaml: invalid YAML"):
        parse("a: [", "x.yaml")
    with pytest.raises(ValueError, match="x.yaml: expected a mapping"):
        parse("- 1", "x.yaml")


# @pt.id KIT-ORCH-005-capabilities
# @pt.item Write capability per adapter
# @pt.condition claude-cli default, claude-cli allow_write, codex-cli, codex restricted to read, client
# @pt.expected Only codex (unrestricted) and claude with allow_write can write; client only reads
# @pt.target AgentConfig.can
# @pt.method unit
# @pt.legacy none
def test_capabilities():
    writer = parse(BASE.replace("adapter: claude-cli, account: cl",
                                "adapter: claude-cli, account: cl, allow_write: true"))
    assert writer.agent("claude").can("write") and writer.agent("codex").can("write")
    plain = parse(BASE)
    assert not plain.agent("claude").can("write") and plain.agent("claude").can("read")
    with pytest.raises(ValueError, match="role executor needs 'write'"):
        parse(BASE.replace("adapter: codex-cli, account: cx",
                           "adapter: codex-cli, account: cx, capabilities: [read]"))
    with_client = BASE.replace("  claude: {adapter: claude-cli, account: cl}",
                               "  claude: {adapter: claude-cli, account: cl}\n"
                               "  me: {adapter: client}")
    assert parse(with_client.replace("reviewer: claude", "reviewer: me")).agent("me").can("read")
    with pytest.raises(ValueError, match="executor role cannot use a client"):
        parse(with_client.replace("executor: codex", "executor:\n    agents: [codex, me]"))
    with pytest.raises(ValueError, match="client agent is the MCP caller"):
        parse(with_client.replace("me: {adapter: client}", "me: {adapter: client, model: x}"))


# @pt.id KIT-ORCH-006-legacy
# @pt.item Legacy config.json mapping
# @pt.condition Named accounts, a default account and an enabled auto_switch policy
# @pt.expected Agents are named after accounts, the client plans and reviews, the fallback policy carries over
# @pt.target bridge.orchestrator.legacy
# @pt.method unit
# @pt.legacy .ai/config.json accounts + auto_switch (0.1.x)
def test_legacy():
    cfg = BridgeConfig(accounts={"b": {"codex_home": "/tmp/b"}, "a": {"codex_home": "/tmp/a"}},
                       default_account="a", max_attempts=4,
                       auto_switch={"enabled": True, "min_remaining_pct": 20,
                                    "fallback_accounts": ["b", "default"]})
    orch = legacy(cfg)
    assert list(orch.accounts) == ["default", "a", "b"]
    assert orch.agent("a").account == "a" and orch.agent("a").adapter == "codex-cli"
    assert orch.role("executor").agents == ["a", "b", "default"]
    assert orch.role("reviewer").agents == ["client"] == orch.role("planner").agents
    assert orch.routing.quota_fallback.enabled and orch.routing.quota_fallback.agents == [
        "b", "default"]
    assert orch.max_attempts == 4 and orch.source.startswith("legacy")


# @pt.id KIT-ORCH-007-load
# @pt.item Config file discovery
# @pt.condition No orchestrator.yaml; a valid one; one beside legacy accounts in config.json
# @pt.expected Legacy mapping, the file, and a refusal to mix both
# @pt.target bridge.orchestrator.load
# @pt.method unit
# @pt.legacy none
def test_load(tmp_path):
    assert load(tmp_path, BridgeConfig()).source.startswith("legacy")
    (tmp_path / ".ai").mkdir()
    (tmp_path / ".ai" / "orchestrator.yaml").write_text(BASE)
    assert load(tmp_path, BridgeConfig()).source.endswith("orchestrator.yaml")
    with pytest.raises(ValueError, match="moved to orchestrator.yaml"):
        load(tmp_path, BridgeConfig(accounts={"a": {"codex_home": "/tmp/a"}}))


# @pt.id KIT-ORCH-008-isolation
# @pt.item Claude account home isolation
# @pt.condition Homes inside ~/.claude, inside the project, relative, and a separate directory
# @pt.expected Only the separate absolute directory resolves
# @pt.target bridge.accounts.resolve_profile
# @pt.method unit
# @pt.legacy none
def test_isolation(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    for home in (Path.home() / ".claude", Path.home() / ".claude" / "x", root / "acct"):
        with pytest.raises(ValueError, match="isolated"):
            resolve_profile("c", "anthropic", str(home), root)
    with pytest.raises(ValueError, match="absolute"):
        resolve_profile("c", "anthropic", "rel", root)
    acc = resolve_profile("c", "anthropic", str(tmp_path / "claude-c"), root)
    assert acc.provider == "anthropic" and acc.home == (tmp_path / "claude-c").resolve()


# @pt.id KIT-ORCH-009-slots
# @pt.item Per-account concurrency slots
# @pt.condition An account with two slots is locked three times
# @pt.expected Two locks succeed, the third reports the account busy, release frees a slot
# @pt.target bridge.accounts.AccountLock
# @pt.method unit
# @pt.legacy none
def test_slots(tmp_path):
    acc = Account("c", tmp_path / "home", "anthropic")
    locks = [AccountLock(acc, str(tmp_path / "locks"), slots=2) for _ in range(3)]
    locks[0].acquire()
    locks[1].acquire()
    with pytest.raises(ConflictError, match="busy"):
        locks[2].acquire()
    locks[0].release()
    locks[2].acquire()
    for lock in locks[1:]:
        lock.release()


# @pt.id KIT-ORCH-010-recursion
# @pt.item Recursion guard
# @pt.condition The MCP server and the CLI start with the agent marker in the environment
# @pt.expected Both refuse before doing any work
# @pt.target bridge.server.main, bridge.cli.main
# @pt.method unit
# @pt.legacy none
def test_recursion(monkeypatch):
    monkeypatch.setenv(AGENT_ENV, "1")
    with pytest.raises(SystemExit, match="refused"):
        server.main()
    with pytest.raises(SystemExit) as stop:
        cli.main(["health"])
    assert stop.value.code == 2
    assert os.environ[AGENT_ENV] == "1"
