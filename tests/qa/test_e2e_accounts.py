"""E2E: several accounts, per-account limits, automatic failover and account isolation."""

from __future__ import annotations

import pytest

from tests.qa.conftest import Sandbox

WRITE = "src/app.py=x = 2\n"


def claude_accounts(cl1: str = "", cl2: str = "", fallback: str = "{enabled: true}") -> str:
    def limits(text: str) -> str:
        return f", limits: {text}" if text else ""
    return f"""
version: 1
accounts:
  cl1: {{provider: anthropic, home: {{home}}/cl1{limits(cl1)}}}
  cl2: {{provider: anthropic, home: {{home}}/cl2{limits(cl2)}}}
agents:
  claude1: {{adapter: claude-cli, account: cl1, allow_write: true}}
  claude2: {{adapter: claude-cli, account: cl2, allow_write: true}}
roles: {{executor: {{agents: [claude1, claude2]}}}}
workflow: {{stages: [execute, test]}}
routing: {{quota_fallback: {fallback}}}
"""


def homes(box: Sandbox) -> list[str]:
    return [c["config_dir"].rsplit("/", 1)[1] for c in box.calls("claude")]


# @pt.id QA-E2E-040-config-views
# @pt.item A two-account setup as the user sees it
# @pt.condition two claude-cli executors on cl1 and cl2, cl1 with a five-hour limit
# @pt.expected config-check OK; agents lists both available in order; usage shows cl1's limit as ok and cl2 without limits
# @pt.target bridge.cli config-check/agents/usage
# @pt.method e2e
# @pt.legacy none
def test_config_views(sandbox: Sandbox) -> None:
    sandbox.orchestrator(claude_accounts(cl1="{five_hour_pct: 80}"))
    assert sandbox.ok("config-check")["ok"] is True
    agents = {a["agent"]: a for a in sandbox.ok("agents")["agents"]}
    assert agents["claude1"]["available"] and agents["claude2"]["available"]
    usage = {a["name"]: a for a in sandbox.ok("usage")["accounts"]}
    assert usage["cl1"]["limits"]["status"] == "ok"
    assert usage["cl2"]["limits"]["status"] == "none"


# @pt.id QA-E2E-041-route-around-limit
# @pt.item Work moves to the next account once one is over its usage limit
# @pt.condition cl1 stops at five_hour_pct 80; its first run reports 90 % used
# @pt.expected task 1 runs on cl1; afterwards agents shows claude1 over limit and usage shows limited; task 2 is routed to claude2 and runs in cl2's home
# @pt.target routing via bridge.cli execute/agents/usage
# @pt.method e2e
# @pt.legacy none
def test_route_around_limit(sandbox: Sandbox) -> None:
    sandbox.orchestrator(claude_accounts(cl1="{five_hour_pct: 80}"))
    env = {"FAKE_CLAUDE_UTIL": "0.9", "FAKE_CLAUDE_WRITE": WRITE}
    first = sandbox.create()
    assert sandbox.ok("execute", first, env=env)["agent"] == "claude1"
    agents = {a["agent"]: a for a in sandbox.ok("agents")["agents"]}
    assert not agents["claude1"]["available"]
    assert "over limit" in agents["claude1"]["unavailable_reason"]
    assert {a["name"]: a for a in sandbox.ok("usage")["accounts"]}["cl1"]["limits"]["status"] == "limited"
    second = sandbox.create("Second task")
    out = sandbox.ok("execute", second, env=env)
    assert out["agent"] == "claude2" and out["state"] == "DONE"  # no review stage: gates approve
    assert homes(sandbox) == ["cl1", "cl2"]


# @pt.id QA-E2E-042-quota-failover
# @pt.item A task paused on QUOTA moves to the next account when the user enabled fallback
# @pt.condition quota_fallback enabled; claude1 reports a usage limit on its first run
# @pt.expected PAUSED (QUOTA); resume moves it to claude2 (account_auto_switched, reason quota) and finishes in cl2's home
# @pt.target Bridge._quota_fallback via bridge.cli resume
# @pt.method e2e
# @pt.legacy none
def test_quota_failover(sandbox: Sandbox) -> None:
    sandbox.orchestrator(claude_accounts())
    task = sandbox.create()
    out = sandbox.ok("execute", task, env={"FAKE_CLAUDE_SCENARIO": "quota,ok"})
    assert out["state"] == "PAUSED" and out["failure"].startswith("QUOTA")
    out = sandbox.ok("resume", task, env={"FAKE_CLAUDE_SCENARIO": "quota,ok"})
    assert out["state"] == "DONE" and out["agent"] == "claude2", out
    switched, = sandbox.events(task, "account_auto_switched")
    assert switched["detail"]["reason"] == "quota" and switched["detail"]["to"] == "claude2"
    assert homes(sandbox) == ["cl1", "cl2"]


# @pt.id QA-E2E-043-no-fallback
# @pt.item Without fallback a QUOTA pause waits for the user
# @pt.condition quota_fallback disabled; claude1 reports a usage limit
# @pt.expected PAUSED; resume is refused naming account-clear; no account_auto_switched event; claude2 never runs
# @pt.target bridge.cli resume
# @pt.method e2e
# @pt.legacy none
def test_no_fallback_waits(sandbox: Sandbox) -> None:
    sandbox.orchestrator(claude_accounts(fallback="{enabled: false}"))
    task = sandbox.create()
    assert sandbox.ok("execute", task, env={"FAKE_CLAUDE_SCENARIO": "quota"})["state"] == "PAUSED"
    res = sandbox.cli("resume", task)
    assert res.code == 1 and "account-clear cl1" in res.err
    assert not sandbox.events(task, "account_auto_switched") and homes(sandbox) == ["cl1"]


# @pt.id QA-E2E-044-budget
# @pt.item A daily budget stops an account; the next account takes the work
# @pt.condition cl1 has budget_usd day 1.0 and reserve_usd 0.5; each run costs 0.7
# @pt.expected run 1 on cl1 spends 0.7; usage shows the spend and agents shows claude1 over limit; a task pinned to claude1 is refused with the limit message; an unpinned task runs on claude2
# @pt.target per-account limits via bridge.cli execute/usage
# @pt.method e2e
# @pt.legacy none
def test_daily_budget(sandbox: Sandbox) -> None:
    sandbox.orchestrator(claude_accounts(cl1="{budget_usd: {day: 1.0}, reserve_usd: 0.5}",
                                         fallback="{enabled: false}"))
    env = {"FAKE_CLAUDE_COST": "0.7", "FAKE_CLAUDE_WRITE": WRITE}
    first = sandbox.create()
    assert sandbox.ok("execute", first, env=env)["agent"] == "claude1"
    cl1 = {a["name"]: a for a in sandbox.ok("usage")["accounts"]}["cl1"]
    assert abs(cl1["limits"]["spend"]["day"]["spent"] - 0.7) < 1e-9
    agents = {a["agent"]: a for a in sandbox.ok("agents")["agents"]}
    assert "over limit: budget_usd.day" in agents["claude1"]["unavailable_reason"]
    pinned = sandbox.create("Pinned", agent="claude1")
    res = sandbox.cli("execute", pinned, env=env)
    assert res.code == 1 and "over its configured limit" in res.err, res.err
    assert sandbox.ok("status", pinned)["state"] == "READY"
    free = sandbox.create("Unpinned")
    assert sandbox.ok("execute", free, env=env)["agent"] == "claude2"


# @pt.id QA-E2E-045-account-isolation
# @pt.item Every run uses its own account's auth home and never sees provider keys
# @pt.condition two Codex accounts with their own homes; OPENAI_API_KEY and ANTHROPIC_API_KEY set in the user's shell
# @pt.expected each run gets CODEX_HOME of its account; the agent process has no OPENAI_API_KEY
# @pt.target executor env scrubbing and account homes via the CLI
# @pt.method e2e
# @pt.legacy none
def test_account_isolation(sandbox: Sandbox) -> None:
    sandbox.orchestrator("""
version: 1
accounts:
  cx1: {provider: openai, home: {home}/cx1}
  cx2: {provider: openai, home: {home}/cx2}
agents:
  codex1: {adapter: codex-cli, account: cx1}
  codex2: {adapter: codex-cli, account: cx2}
roles: {executor: {agents: [codex1, codex2]}}
workflow: {stages: [execute, test]}
""")
    env = {"OPENAI_API_KEY": "sk-proj-shouldnotleak0123456789",
           "ANTHROPIC_API_KEY": "sk-ant-shouldnotleak0123456789"}
    for agent in ("codex1", "codex2"):
        sandbox.ok("execute", sandbox.create(f"Task for {agent}", agent=agent), env=env)
    calls = sandbox.calls()
    assert [c["codex_home"].rsplit("/", 1)[1] for c in calls] == ["cx1", "cx2"]
    assert not any(c["env_has_secret"] for c in calls)


# @pt.id QA-E2E-046-usage-agrees-with-gate
# @pt.item `usage` tells the user an account will refuse its next run
# @pt.condition cl1 has budget_usd day 1.0 and reserve_usd 0.5; one run cost 0.7, so the next run (0.7 + 0.5 > 1.0) is refused
# @pt.expected usage reports cl1 as limited (or otherwise not ok), matching agents and execute
# @pt.target Bridge.usage_report via bridge.cli usage
# @pt.method e2e
# @pt.legacy none
@pytest.mark.xfail(strict=True, reason="QA-BUG-002: usage evaluates limits without the next run's "
                   "reserve and reports ok while agents/execute refuse the account")
def test_usage_agrees_with_start_gate(sandbox: Sandbox) -> None:
    sandbox.orchestrator(claude_accounts(cl1="{budget_usd: {day: 1.0}, reserve_usd: 0.5}",
                                         fallback="{enabled: false}"))
    sandbox.ok("execute", sandbox.create(), env={"FAKE_CLAUDE_COST": "0.7"})
    refused = sandbox.cli("execute", sandbox.create("Pinned", agent="claude1"))
    assert refused.code == 1 and "over its configured limit" in refused.err
    cl1 = {a["name"]: a for a in sandbox.ok("usage")["accounts"]}["cl1"]
    assert cl1["limits"]["status"] != "ok", cl1["limits"]
