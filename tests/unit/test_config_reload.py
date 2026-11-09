"""The MCP server's Bridge reloads its configuration in place when the files change."""

from __future__ import annotations

import asyncio

import pytest

from bridge import orchestrator, server
from bridge.config import BridgeConfig
from bridge.service import Bridge

ONE = """version: 1
agents:
  codex: {adapter: codex-cli, account: cx}
roles:
  executor: codex
  reviewer: codex
"""
TWO = """version: 1
agents:
  codex: {adapter: codex-cli, account: cx}
  other: {adapter: codex-cli, account: cx}
roles:
  executor: codex
  reviewer: other
"""
BAD_ROLE = ONE.replace("reviewer: codex", "reviewer: nobody")


@pytest.fixture(autouse=True)
def no_registry(monkeypatch, fake_codex):
    monkeypatch.setattr(orchestrator, "load_registry", lambda: {})


def project(repo):
    """The shared throwaway git repo with .ai files written before any Bridge is built."""
    root = repo
    ai = root / ".ai"
    ai.mkdir(parents=True, exist_ok=True)
    (ai / "accounts.yaml").write_text("accounts: {cx: {provider: openai}}\n")
    (ai / "orchestrator.yaml").write_text(ONE)
    return root


class Live:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


# @pt.id KIT-RELOAD-001
# @pt.item Reload picks up an orchestrator.yaml edit
# @pt.condition A Bridge built from disk; orchestrator.yaml gains an agent
# @pt.expected reload_config is True, the new agent is listed, same Bridge, caches cleared
# @pt.target bridge.service.Bridge.reload_config
# @pt.method unit
# @pt.legacy none
def test_reload_orchestrator(repo):
    root = project(repo)
    b = Bridge(root)
    b._adapters["x"] = object()
    b.gate._reports[("cx", 0.0)] = (0.0, 0, {})
    assert "other" not in b.orch.agents
    (root / ".ai" / "orchestrator.yaml").write_text(TWO)
    same = b
    assert b.reload_config() is True
    assert b is same and "other" in b.orch.agents
    assert b._adapters == {} and b.gate._reports == {}
    assert "other" in str(b.list_agents(logins={}))
    assert b.reload_config() is False


# @pt.id KIT-RELOAD-002
# @pt.item No change, no reload
# @pt.condition No file changed
# @pt.expected False and cfg/orch are the same objects
# @pt.target bridge.service.Bridge.reload_config
# @pt.method unit
# @pt.legacy none
def test_no_change(repo):
    b = Bridge(project(repo))
    cfg, orch = b.cfg, b.orch
    assert b.reload_config() is False
    assert b.cfg is cfg and b.orch is orch


# @pt.id KIT-RELOAD-003
# @pt.item Reload picks up accounts.yaml and config.json edits
# @pt.condition accounts.yaml limits and config.json change
# @pt.expected each change reloads and clears caches
# @pt.target bridge.service.Bridge.reload_config
# @pt.method unit
# @pt.legacy none
def test_reload_accounts_and_config(repo):
    root = project(repo)
    b = Bridge(root)
    (root / ".ai" / "accounts.yaml").write_text(
        "accounts: {cx: {provider: openai, limits: {weekly_pct: 90}}}\n")
    b._adapters["x"] = object()
    assert b.reload_config() is True
    assert b.orch.accounts["cx"].limits.weekly_pct == 90 and b._adapters == {}
    (root / ".ai" / "config.json").write_text('{"network_retries": 2}')
    b.gate._reports[("cx", 0.0)] = (0.0, 0, {})
    assert b.reload_config() is True
    assert b.cfg.network_retries == 2 and b.gate._reports == {}


# @pt.id KIT-RELOAD-004
# @pt.item Invalid edit keeps the old config
# @pt.condition orchestrator.yaml becomes bad YAML, then a bad role, then is fixed
# @pt.expected False without raising, health has config_reload_failed, fix clears it
# @pt.target bridge.service.Bridge.reload_config, Bridge.health
# @pt.method unit
# @pt.legacy none
def test_invalid_edit(repo, monkeypatch):
    root = project(repo)
    b = Bridge(root)
    orch = b.orch
    path = root / ".ai" / "orchestrator.yaml"
    for bad in ("agents: [unclosed\n  - : :", BAD_ROLE):
        path.write_text(bad)
        assert b.reload_config() is False
        assert b.orch is orch
        assert b._reload_error is not None
    calls = []
    real = orchestrator.load
    monkeypatch.setattr("bridge.service.load_orchestrator",
                        lambda *a: calls.append(1) or real(*a))
    assert b.reload_config() is False and calls == []  # same bad content: not re-parsed
    report = asyncio.run(b.health())
    assert report["ok"] is False
    assert "config_reload_failed" in [p["code"] for p in report["problems"]]
    path.write_text(TWO)
    assert b.reload_config() is True
    report = asyncio.run(b.health())
    assert "config_reload_failed" not in [p["code"] for p in report["problems"]]


# @pt.id KIT-RELOAD-005
# @pt.item Reload is deferred while a task runs
# @pt.condition _runs holds a live task, then it is done
# @pt.expected False and nothing changes while live; the next call reloads
# @pt.target bridge.service.Bridge.reload_config
# @pt.method unit
# @pt.legacy none
def test_deferred_while_running(repo):
    root = project(repo)
    b = Bridge(root)
    orch = b.orch
    (root / ".ai" / "orchestrator.yaml").write_text(TWO)
    live = Live()
    b._runs["T1"] = live
    assert b.reload_config() is False and b.orch is orch
    b._workflows["W1"], b._runs["T1"] = live, Live(True)
    assert b.reload_config() is False and b.orch is orch
    b._workflows["W1"] = Live(True)
    assert b.reload_config() is True and "other" in b.orch.agents


# @pt.id KIT-RELOAD-006
# @pt.item Injected config never reloads
# @pt.condition Bridge built with cfg and orch; files change
# @pt.expected reload_config is False
# @pt.target bridge.service.Bridge.reload_config
# @pt.method unit
# @pt.legacy none
def test_injected_never_reloads(repo):
    root = project(repo)
    cfg = BridgeConfig()
    orch = orchestrator.load(root, cfg)
    b = Bridge(root, cfg=cfg, orch=orch)
    (root / ".ai" / "orchestrator.yaml").write_text(TWO)
    assert b.reload_config() is False
    assert b.orch is orch


# @pt.id KIT-RELOAD-007
# @pt.item server.bridge() reloads on every call
# @pt.condition A stub bridge is installed; bridge() is called twice
# @pt.expected reload_config is called each time
# @pt.target bridge.server.bridge
# @pt.method unit
# @pt.legacy none
def test_server_calls_reload(monkeypatch):
    class Stub:
        n = 0

        def reload_config(self):
            Stub.n += 1
            return False

    monkeypatch.setattr(server, "_bridge", Stub())
    server.bridge()
    server.bridge()
    assert Stub.n == 2


# @pt.id KIT-RELOAD-008
# @pt.item Reverting a bad edit clears the reload error
# @pt.condition A bad edit fails to reload; the original content is written back
# @pt.expected reload_config is False without reloading, the error is cleared, health is OK again
# @pt.target bridge.service.Bridge.reload_config, Bridge.health
# @pt.method unit
# @pt.legacy none
def test_revert_clears_error(repo):
    root = project(repo)
    b = Bridge(root)
    orch = b.orch
    path = root / ".ai" / "orchestrator.yaml"
    path.write_text(BAD_ROLE)
    assert b.reload_config() is False
    report = asyncio.run(b.health())
    assert "config_reload_failed" in [p["code"] for p in report["problems"]]
    path.write_text(ONE)
    assert b.reload_config() is False
    assert b._reload_error is None
    report = asyncio.run(b.health())
    assert "config_reload_failed" not in [p["code"] for p in report["problems"]]
    assert b.orch is orch
