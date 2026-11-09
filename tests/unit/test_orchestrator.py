"""Orchestrator config: schema validation, legacy mapping, account isolation and slots."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from bridge import cli, server
from bridge.accounts import Account, AccountLock, resolve_profile
from bridge.config import BridgeConfig
from bridge.executor import AGENT_ENV
from bridge.orchestrator import legacy, load, load_managed, parse
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
# @pt.condition Every examples/<name>.yaml is parsed together with its <name>.accounts.yaml
# @pt.expected All parse with no accounts in orchestrator.yaml and no agents in accounts.yaml; the main example maps planner/reviewer to Claude, executor to two Codex agents
# @pt.target bridge.orchestrator.parse, bridge.orchestrator.load_managed
# @pt.method unit
# @pt.legacy none
def test_examples():
    files = sorted(p for p in EXAMPLES.glob("*.yaml") if not p.name.endswith(".accounts.yaml"))
    assert len(files) >= 4
    for path in files:
        accounts = path.with_suffix(".accounts.yaml")
        assert accounts.exists()
        assert "accounts" not in yaml.safe_load(path.read_text())
        assert set(yaml.safe_load(accounts.read_text())) == {"accounts"}
        orch = parse(path.read_text(), str(path), managed=load_managed(accounts),
                     managed_source=str(accounts))
        assert not orch.accounts_in_orchestrator
        assert not orch.agents_in_accounts_file
    path = EXAMPLES / "orchestrator.yaml"
    main = parse(path.read_text(), managed=load_managed(path.with_suffix(".accounts.yaml")))
    assert main.role("executor").agents == ["codex-a", "codex-b"]
    assert main.agent(main.role("reviewer").agents[0]).adapter == "claude-cli"
    assert main.max_attempts == 3


# @pt.id KIT-ORCH-001c-config-init-layout
# @pt.item config-init writes the one-owner layout
# @pt.condition config-init <example> runs in an empty project, then again, then with only one of the two files present
# @pt.expected .ai/orchestrator.yaml and .ai/accounts.yaml are written from the example pair; either file existing refuses and writes nothing; the listing names only the orchestrator examples
# @pt.target bridge.cli._run
# @pt.method unit
# @pt.legacy none
def test_config_init_writes_both_files(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: True))
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)  # keep stdout capturable
    orch, accounts = root / ".ai" / "orchestrator.yaml", root / ".ai" / "accounts.yaml"

    def init(example="single-agent"):
        return asyncio.run(cli._run(argparse.Namespace(cmd="config-init", example=example)))

    init(None)
    names = [line.split()[0] for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert names[:6] == sorted(p.stem for p in EXAMPLES.glob("*.yaml")
                               if not p.name.endswith(".accounts.yaml"))
    assert not any(n.endswith(".accounts") for n in names)
    init()
    assert orch.read_text() == (EXAMPLES / "single-agent.yaml").read_text()
    assert accounts.read_text() == (EXAMPLES / "single-agent.accounts.yaml").read_text()
    for keep, other in ((orch, accounts), (accounts, orch)):
        other.unlink()
        before = keep.read_text()
        with pytest.raises(SystemExit) as exc:
            init("orchestrator")
        assert exc.value.code == 1
        assert keep.read_text() == before and not other.exists()
        other.write_text("# mine\n")
    with pytest.raises(SystemExit):  # both exist again: nothing is overwritten
        init("orchestrator")
    assert orch.read_text() == accounts.read_text() == "# mine\n"


# @pt.id KIT-ORCH-001b-config-example
# @pt.item Shipped .ai/config.example.json
# @pt.condition the example is loaded as BridgeConfig next to an orchestrator.yaml
# @pt.expected it validates, lists every non-legacy key with its default, and holds no legacy-only keys (accounts, auto_switch) that orchestrator.yaml rejects
# @pt.target bridge.config.BridgeConfig, bridge.orchestrator.load
# @pt.method unit
# @pt.legacy .ai/config.json accounts (moved to orchestrator.yaml)
def test_config_example():
    data = json.loads((EXAMPLES.parent / ".ai" / "config.example.json").read_text())
    cfg = BridgeConfig.model_validate(data)
    assert not cfg.accounts and not cfg.auto_switch.enabled
    legacy = {"accounts", "default_account", "auto_switch", "max_attempts"}
    assert not legacy & set(data)
    defaults = BridgeConfig().model_dump()
    assert data == {k: v for k, v in defaults.items() if k not in legacy}


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
    ("executor: codex", "executor: {agents: [codex], default: codex, strategy: explicit}",
     "default needs strategy ordered"),
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


# @pt.id KIT-ORCH-011-claude-login
# @pt.item Claude account login stays in its own home
# @pt.condition account-login for an anthropic account; the global Claude identity unchanged or changed
# @pt.expected claude auth login runs with CLAUDE_CONFIG_DIR set; a changed global identity exits 3
# @pt.target bridge.cli._run, bridge.accounts.global_claude_identity
# @pt.method unit
# @pt.legacy none
def test_claude_login(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import Mock

    root = tmp_path / "project"
    (root / ".ai").mkdir(parents=True)
    home = tmp_path / "claude-home"
    (root / ".ai" / "orchestrator.yaml").write_text(BASE.replace("/tmp/y", str(home)))
    monkeypatch.setattr(cli, "find_root", lambda: root)
    monkeypatch.setattr(cli, "load_config", lambda root: BridgeConfig())
    monkeypatch.setattr(cli.sys, "stdin", Mock(isatty=lambda: True))
    monkeypatch.setattr(cli.sys, "stdout", Mock(isatty=lambda: True))
    monkeypatch.setattr(cli, "login_status", lambda cfg, acc: True)
    runner = Mock()
    monkeypatch.setattr(cli.subprocess, "run", runner)
    args = cli.argparse.Namespace(cmd="account-login", name="cl", device_auth=False)
    monkeypatch.setattr(cli, "global_claude_identity", lambda cfg: "same")
    assert asyncio.run(cli._run(args)) is True
    assert runner.call_args.args[0] == ["claude", "auth", "login"]
    assert runner.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(home.resolve())
    identities = iter(["before", "after"])
    monkeypatch.setattr(cli, "global_claude_identity", lambda cfg: next(identities))
    with pytest.raises(SystemExit) as stop:
        asyncio.run(cli._run(args))
    assert stop.value.code == 3


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


# @pt.id KIT-AUDIT-CFG-06
# @pt.item A broken machine-wide account is reported against the registry file
# @pt.condition the registry holds an entry with an unknown key that the project yaml does not declare
# @pt.expected the error names accounts.json and the entry, not orchestrator.yaml
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy audit CFG-06: the message blamed .ai/orchestrator.yaml for a registry entry
def test_registry_error_names_registry_file():
    text = "version: 1\nagents: {}\nroles: {}\n"
    with pytest.raises(ValueError) as err:
        parse(text, "proj/.ai/orchestrator.yaml",
              {"r1": {"provider": "openai", "home": None, "extra": 1}})
    message = str(err.value)
    assert "accounts.json: accounts.r1.extra" in message
    assert "orchestrator.yaml" not in message
