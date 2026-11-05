"""Opt-in quota routing: an unpinned task goes to the eligible executor with the most quota."""

import json

import pytest

from bridge.accounts import AccountLock
from bridge.orchestrator import parse
from bridge.quota import codex_home_for
from tests.conftest import run, spec
from tests.integration.test_accounts_routing import calls
from tests.integration.test_orchestration import CONFIG_C, events

# Config C with the quota strategy and the MCP client as reviewer (tasks stop in REVIEW).
QUOTA = (CONFIG_C
         .replace("executor: {agents: [codex1, codex2]}",
                  "executor: {agents: [codex1, codex2], strategy: quota}")
         .replace("reviewer: claude", "reviewer: me")
         .replace("  claude: {adapter: claude-cli, account: cl}",
                  "  claude: {adapter: claude-cli, account: cl}\n  me: {adapter: client}"))


def seed(bridge, account, remaining):
    """Give a codex account a known remaining quota through a rollout in its home."""
    path = codex_home_for(bridge._account(account)) / "sessions/2026/10/19/rollout-x.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {"primary": {
            "used_percent": 100 - remaining, "resets_at": 9999999999,
        }},
    }}) + "\n")


def execute(bridge):
    task = run(bridge.create_task(spec(verification_commands=[])))["task_id"]
    out = run(bridge.execute_task(task, wait=True))
    return task, out


# @pt.id KIT-ROUTE-QUOTA-1
# @pt.item Quota strategy picks the account with the most remaining quota
# @pt.condition Strategy quota; cx1 has 20% left, cx2 70%; an unpinned task is executed
# @pt.expected The task routes to codex2; agent_routed lists strategy, 70% and both candidates ranked
# @pt.target AccountGate._route, AccountGate._pick, RoleConfig.strategy
# @pt.method integration
# @pt.legacy none
def test_quota_picks_most_remaining(make_orch, fake_codex):
    b = make_orch(QUOTA)
    seed(b, "cx1", 20)
    seed(b, "cx2", 70)
    task, out = execute(b)
    assert out["state"] == "REVIEW" and (out["agent"], out["account"]) == ("codex2", "cx2")
    routed, = events(b, task, "agent_routed")
    assert routed["detail"] == {
        "role": "executor", "agent": "codex2", "account": "cx2", "strategy": "quota",
        "remaining_pct": 70,
        "candidates": [{"agent": "codex2", "remaining_pct": 70},
                       {"agent": "codex1", "remaining_pct": 20}]}
    assert calls(fake_codex)[-1]["codex_home"].endswith("/cx2")


# @pt.id KIT-ROUTE-QUOTA-2
# @pt.item Known quota ranks before unknown quota
# @pt.condition Strategy quota; cx1's quota is unknown, cx2 has only 5% left
# @pt.expected The task routes to codex2; codex1 is ranked last with remaining_pct None
# @pt.target AccountGate._ranked
# @pt.method integration
# @pt.legacy none
def test_quota_known_before_unknown(make_orch, fake_codex):
    b = make_orch(QUOTA)
    seed(b, "cx2", 5)
    task, out = execute(b)
    assert out["agent"] == "codex2"
    routed, = events(b, task, "agent_routed")
    assert routed["detail"]["remaining_pct"] == 5
    assert routed["detail"]["candidates"] == [{"agent": "codex2", "remaining_pct": 5},
                                              {"agent": "codex1", "remaining_pct": None}]


# @pt.id KIT-ROUTE-QUOTA-3
# @pt.item Unknown quota everywhere keeps list order
# @pt.condition Strategy quota; neither account has a readable quota
# @pt.expected The task routes to codex1, the first agent in list order
# @pt.target AccountGate._ranked
# @pt.method integration
# @pt.legacy none
def test_quota_all_unknown_keeps_order(make_orch, fake_codex):
    b = make_orch(QUOTA)
    task, out = execute(b)
    assert out["agent"] == "codex1"
    routed, = events(b, task, "agent_routed")
    assert routed["detail"]["strategy"] == "quota"
    assert routed["detail"]["remaining_pct"] is None
    assert [c["agent"] for c in routed["detail"]["candidates"]] == ["codex1", "codex2"]


# @pt.id KIT-ROUTE-QUOTA-4
# @pt.item A busy account is skipped even with the most quota
# @pt.condition Strategy quota; cx2 has 90% left but another bridge holds its slot; cx1 has 10%
# @pt.expected The task routes to codex1, the only eligible candidate
# @pt.target AccountGate._ranked, AccountGate._unavailable
# @pt.method integration
# @pt.legacy none
def test_quota_skips_busy(make_orch, fake_codex):
    b = make_orch(QUOTA)
    seed(b, "cx1", 10)
    seed(b, "cx2", 90)
    held = AccountLock(b._account("cx2"), str(fake_codex / "locks"))
    held.acquire()
    try:
        task, out = execute(b)
    finally:
        held.release()
    assert out["agent"] == "codex1"
    routed, = events(b, task, "agent_routed")
    assert routed["detail"]["candidates"] == [{"agent": "codex1", "remaining_pct": 10}]


# @pt.id KIT-ROUTE-QUOTA-5
# @pt.item Routed tasks keep their agent under the quota strategy
# @pt.condition Strategy quota routes a task to codex2; the client asks for rework; the quotas swap
# @pt.expected The resume stays on codex2 with the same session; agent_routed is logged once
# @pt.target AccountGate._route, Bridge.resume_task
# @pt.method integration
# @pt.legacy none
def test_quota_affinity(make_orch, fake_codex):
    b = make_orch(QUOTA)
    seed(b, "cx1", 20)
    seed(b, "cx2", 70)
    task, first = execute(b)
    assert first["state"] == "REVIEW" and first["agent"] == "codex2"
    run(b.review_task(task, "rework", "Again"))
    seed(b, "cx1", 90)
    seed(b, "cx2", 10)
    second = run(b.resume_task(task, wait=True))
    assert second["agent"] == "codex2" and second["session_id"] == first["session_id"]
    assert calls(fake_codex)[-1]["codex_home"].endswith("/cx2")
    assert len(events(b, task, "agent_routed")) == 1


# @pt.id KIT-ROUTE-QUOTA-6
# @pt.item A role default cannot be combined with the quota strategy
# @pt.condition The executor role sets default codex2 and strategy quota
# @pt.expected Parsing fails: default needs strategy ordered
# @pt.target RoleConfig._default_first
# @pt.method integration
# @pt.legacy none
def test_quota_rejects_default(tmp_path):
    text = (CONFIG_C.replace("{home}", str(tmp_path))
            .replace("executor: {agents: [codex1, codex2]}",
                     "executor: {agents: [codex1, codex2], strategy: quota, default: codex2}"))
    with pytest.raises(ValueError, match="default needs strategy ordered"):
        parse(text)
