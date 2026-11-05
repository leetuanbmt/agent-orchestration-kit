"""Per-account limits enforced by the bridge service: routing, start gate, ledger, reporting."""

import time

import pytest

from bridge.accounts import AccountLock
from bridge.config import BridgeConfig
from bridge.orchestrator import parse
from bridge.service import Bridge
from tests.conftest import git, run, spec
from tests.integration.test_orchestration import events, log

WRITE = "src/app.py=x = 2\n"
FIVE_HOUR = "{five_hour_pct: 80}"


def config(cl1: str = "", cl2: str = "", stages: str = "[execute, test]",
           roles: str = "{executor: {agents: [claude1, claude2]}}") -> str:
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
roles: {roles}
workflow: {{stages: {stages}}}
"""


SINGLE = "{executor: claude1}"


def seed(b: Bridge, name: str, used: float = 90, resets_at: float | None = None) -> None:
    """Provider usage as the CLI last reported it: a five-hour window `used` percent full."""
    reset = time.time() + 3600 if resets_at is None else resets_at
    b.store.save_quota(name, {"primary": {"used_percent": used, "window_minutes": 300,
                                          "resets_at": reset}}, key=b._key(name))


def day_spend(b: Bridge, name: str) -> dict:
    return b.store.usage_spend(b._usage_key(name), b._period_starts(time.time()))["day"]


def task(b: Bridge, objective: str, **over: object) -> str:
    return run(b.create_task(spec(objective=objective, **over)))["task_id"]


# @pt.id KIT-LIMIT-101-no-limits
# @pt.item Accounts without limits keep today's behaviour and still record run cost
# @pt.condition A claude executor on an account with no limits; the first run reports total_cost_usd, the second reports none and no pricing exists
# @pt.expected Both runs finish DONE; the ledger records 0.25 actual then an unavailable cost; list_accounts entries stay unchanged (no limits key) and the limit report shows status none with the spend
# @pt.target Bridge._record_account, Bridge.list_accounts, Bridge._limit_report
# @pt.method integration
# @pt.legacy none
def test_no_limits_records_cost(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.25")
    b = make_orch(config(roles=SINGLE))
    first = task(b, "Set x to 2")
    assert run(b.execute_task(first, wait=True))["state"] == "DONE"
    recorded, = events(b, first, "usage_recorded")
    assert recorded["detail"] == {"account": "cl1", "cost_usd": 0.25, "source": "actual"}

    monkeypatch.delenv("FAKE_CLAUDE_COST")
    second = task(b, "Set x to 2 again")
    assert run(b.execute_task(second, wait=True))["state"] == "DONE"
    recorded, = events(b, second, "usage_recorded")
    assert recorded["detail"] == {"account": "cl1", "cost_usd": None, "source": "unavailable"}

    day = day_spend(b, "cl1")
    assert day["spent"] == pytest.approx(0.25) and day["runs"] == 2 and day["unknown"] == 1
    listing = {a["name"]: a for a in b.list_accounts()["accounts"]}
    assert "limits" not in listing["cl1"] and "limits" not in listing["cl2"]
    report = b._limit_report("cl1")
    assert report["status"] == "none" and report["reason"] is None
    assert report["spend"]["day"]["runs"] == 2
    assert run(b.health())["limits"] == {}


# @pt.id KIT-LIMIT-102-route-around
# @pt.item Routing skips an executor whose account is over its five-hour limit
# @pt.condition cl1 has five_hour_pct 80 and reports 90 % used; an unpinned task runs
# @pt.expected The task routes to claude2 (agent_routed names it) and finishes; claude1 reports the limit reason
# @pt.target Bridge._unavailable, Bridge._pick, Bridge._route
# @pt.method integration
# @pt.legacy none
def test_routes_around_limited_account(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    b = make_orch(config(cl1=FIVE_HOUR))
    seed(b, "cl1")
    reason = b._unavailable("claude1", "write")
    assert reason is not None and reason.startswith("account cl1 over limit: five_hour_pct")
    t = task(b, "Set x to 2")
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == "DONE" and out["agent"] == "claude2"
    routed, = events(b, t, "agent_routed")
    assert routed["detail"] == {"role": "executor", "agent": "claude2", "account": "cl2"}
    assert all(call["config_dir"].endswith("/cl2") for call in log(fake_claude))


# @pt.id KIT-LIMIT-103-all-exhausted
# @pt.item Every executor account over its limit
# @pt.condition cl1 and cl2 both have five_hour_pct 80 and report 90 % used; an unpinned task runs
# @pt.expected execute_task raises ValueError naming both accounts' limit reasons; the task stays READY with attempts 0 and no agent ran
# @pt.target Bridge._pick, Bridge._unavailable
# @pt.method integration
# @pt.legacy none
def test_all_accounts_exhausted(make_orch, fake_claude):
    b = make_orch(config(cl1=FIVE_HOUR, cl2=FIVE_HOUR))
    seed(b, "cl1")
    seed(b, "cl2")
    t = task(b, "Set x to 2")
    with pytest.raises(ValueError) as error:
        run(b.execute_task(t, wait=True))
    message = str(error.value)
    assert message.startswith("no eligible executor agent")
    assert "claude1: account cl1 over limit: five_hour_pct" in message
    assert "claude2: account cl2 over limit: five_hour_pct" in message
    row = b.store.get(t)
    assert row["state"] == "READY" and row["attempts"] == 0 and row["agent"] is None
    assert log(fake_claude) == []


# @pt.id KIT-LIMIT-104-pinned-refused
# @pt.item A task bound or pinned to an account over its limit is refused at start
# @pt.condition The task names agent claude1 or account cl1; cl1 has five_hour_pct 80 and reports 90 % used
# @pt.expected ValueError with the limit reason and recovery hint, a limit_blocked event, attempts unchanged, no attempt row, and the account lock released
# @pt.target Bridge._start, Bridge._reserve
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("pin", [{"agent": "claude1"}, {"account": "cl1"}])
def test_pinned_task_refused(make_orch, fake_claude, pin):
    b = make_orch(config(cl1=FIVE_HOUR))
    seed(b, "cl1")
    t = task(b, "Set x to 2", **pin)
    with pytest.raises(ValueError, match="account cl1 over its configured limit: "
                                         "five_hour_pct") as error:
        run(b.execute_task(t, wait=True))
    assert "routing.quota_fallback" in str(error.value)
    assert ".ai/orchestrator.yaml" in str(error.value)
    blocked, = events(b, t, "limit_blocked")
    assert blocked["detail"]["account"] == "cl1"
    assert blocked["detail"]["reason"].startswith("five_hour_pct: 90.0% used >= 80%")
    row = b.store.get(t)
    assert row["state"] == "READY" and row["attempts"] == 0 and row["agent"] == "claude1"
    assert b.store.attempts(t) == [] and log(fake_claude) == []
    lock = AccountLock(b._account("cl1"), b.cfg.lock_dir, 1)
    lock.acquire()
    lock.release()


# @pt.id KIT-LIMIT-105-recovery
# @pt.item An account takes work again once its window resets
# @pt.condition A pinned task is refused while cl1 reports 90 % of its five-hour window; the window's resets_at then lies in the past
# @pt.expected The same task runs on claude1 and finishes DONE
# @pt.target Bridge._reserve, bridge.quota.evaluate_limits
# @pt.method integration
# @pt.legacy none
def test_recovery_after_reset(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    b = make_orch(config(cl1=FIVE_HOUR))
    seed(b, "cl1")
    t = task(b, "Set x to 2", agent="claude1")
    with pytest.raises(ValueError, match="over its configured limit"):
        run(b.execute_task(t, wait=True))
    seed(b, "cl1", resets_at=time.time() - 1)
    assert b._unavailable("claude1", "write") is None
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == "DONE" and out["agent"] == "claude1" and out["attempts"] == 1


# @pt.id KIT-LIMIT-106-budget
# @pt.item A daily USD budget with a per-run reservation
# @pt.condition cl1 has budget_usd.day 1.0 and reserve_usd 0.5; each run reports total_cost_usd 0.6
# @pt.expected The first run is allowed; the second is refused (0.6 spent + 0.5 reserve > 1.0); the ledger shows 0.6 actual and no open reservation
# @pt.target Bridge._reserve, Bridge._record_account, Store.reserve_usage, Store.settle_usage
# @pt.method integration
# @pt.legacy none
def test_daily_budget(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.6")
    b = make_orch(config(cl1="{budget_usd: {day: 1.0}, reserve_usd: 0.5}", roles=SINGLE))
    first = task(b, "Set x to 2")
    assert run(b.execute_task(first, wait=True))["state"] == "DONE"
    recorded, = events(b, first, "usage_recorded")
    assert recorded["detail"] == {"account": "cl1", "cost_usd": 0.6, "source": "actual"}

    second = task(b, "Set x to 2 again", agent="claude1")
    with pytest.raises(ValueError, match="account cl1 over its configured limit: budget_usd.day"):
        run(b.execute_task(second, wait=True))
    assert b.store.get(second)["attempts"] == 0
    day = day_spend(b, "cl1")
    assert day["spent"] == pytest.approx(0.6) and day["reserved"] == 0
    assert day["runs"] == 1 and day["unknown"] == 0 and day["estimated"] is False
    assert len(log(fake_claude)) == 1


# @pt.id KIT-LIMIT-107-fail-closed
# @pt.item Unknown run cost on a budgeted account fails closed unless pricing is configured
# @pt.condition cl1 has budget_usd.day 1.0 and no reserve; the CLI reports no cost; pricing absent or configured
# @pt.expected Without pricing the first run is recorded as unavailable and the second is refused with an unknown-cost reason; with pricing the cost is estimated and the second run is allowed
# @pt.target Bridge._record_account, Bridge._reserve, bridge.quota.run_cost
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("pricing", [False, True])
def test_unknown_cost_fails_closed(make_orch, fake_claude, monkeypatch, pricing):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.delenv("FAKE_CLAUDE_COST", raising=False)
    limits = "{budget_usd: {day: 1.0}"
    limits += ", pricing: {input_per_mtok: 1000, output_per_mtok: 1000}}" if pricing else "}"
    b = make_orch(config(cl1=limits, roles=SINGLE))
    first = task(b, "Set x to 2")
    assert run(b.execute_task(first, wait=True))["state"] == "DONE"
    recorded, = events(b, first, "usage_recorded")
    second = task(b, "Set x to 2 again", agent="claude1")
    if not pricing:
        assert recorded["detail"] == {"account": "cl1", "cost_usd": None,
                                      "source": "unavailable"}
        with pytest.raises(ValueError, match="unknown cost"):
            run(b.execute_task(second, wait=True))
        assert b.store.get(second)["attempts"] == 0
        return
    # 10 input + 5 output tokens at $1000 per million tokens each.
    assert recorded["detail"]["source"] == "estimated"
    assert recorded["detail"]["cost_usd"] == pytest.approx(0.015)
    assert run(b.execute_task(second, wait=True))["state"] == "DONE"
    day = day_spend(b, "cl1")
    assert day["spent"] == pytest.approx(0.03) and day["estimated"] is True


# @pt.id KIT-LIMIT-108-concurrent-bridges
# @pt.item Budget reservations are shared between bridges on one machine
# @pt.condition Two Bridge instances on different repositories use the same account home and accounts.db; cl1 has budget_usd.day 1.0 and reserve_usd 0.6; the other bridge holds an open 0.5 reservation
# @pt.expected This bridge refuses the run (0.5 + 0.6 > 1.0); once the other bridge settles at 0.0 the run starts and finishes
# @pt.target Bridge._reserve, Store.reserve_usage
# @pt.method integration
# @pt.legacy none
def test_reservation_shared_between_bridges(make_orch, fake_claude, fake_codex, tmp_path,
                                            monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.1")
    text = config(cl1="{budget_usd: {day: 1.0}, reserve_usd: 0.6}", roles=SINGLE)
    b = make_orch(text)
    other_root = tmp_path / "other"
    other_root.mkdir()
    git(other_root, "init", "-q")
    homes = str(fake_codex.parent / "homes")
    other = Bridge(other_root, BridgeConfig(retry_backoff_s=0, lock_dir=b.cfg.lock_dir),
                   parse(text.replace("{home}", homes)))
    assert other._usage_key("cl1") == b._usage_key("cl1")
    held = other.store.reserve_usage("cl1", key=other._usage_key("cl1"), task_id=None,
                                     reserve=0.5, starts=other._period_starts(time.time()),
                                     check=lambda spend: None)
    assert "budget_usd.day" in (b._unavailable("claude1", "write") or "")
    t = task(b, "Set x to 2", agent="claude1")
    with pytest.raises(ValueError, match="over its configured limit: budget_usd.day"):
        run(b.execute_task(t, wait=True))
    assert log(fake_claude) == []
    other.store.settle_usage(held, 0.0, "actual")
    assert run(b.execute_task(t, wait=True))["state"] == "DONE"
    day = day_spend(other, "cl1")
    assert day["spent"] == pytest.approx(0.1) and day["reserved"] == 0 and day["runs"] == 2


# @pt.id KIT-LIMIT-109-reviewer
# @pt.item A reviewer agent on an account over its limit does not run
# @pt.condition claude1 executes, claude2 reviews on cl2 with five_hour_pct 80; cl2 reports 90 % before routing, or only after the reviewer was picked (another bridge used it meanwhile)
# @pt.expected The reviewer never runs and the task stays in REVIEW; before routing the review stage reports no eligible reviewer, after picking the start gate logs agent_skipped with the limit reason
# @pt.target Bridge._consult, Bridge._review_stage, Bridge._reserve
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("race", [False, True])
def test_reviewer_over_limit(make_orch, fake_claude, monkeypatch, race):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    b = make_orch(config(cl2=FIVE_HOUR, stages="[execute, test, review]",
                         roles="{executor: claude1, reviewer: claude2}"))
    if race:
        pick = b._pick

        def racing(role, need, task_spec=None):
            name = pick(role, need, task_spec)
            if role == "reviewer":
                seed(b, "cl2")
            return name
        monkeypatch.setattr(b, "_pick", racing)
    else:
        seed(b, "cl2")
    t = task(b, "Set x to 2")
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == "REVIEW"
    assert len(log(fake_claude)) == 1  # the executor only
    if race:
        skipped, = events(b, t, "agent_skipped")
        assert skipped["detail"]["agent"] == "claude2"
        assert skipped["detail"]["reason"].startswith("account cl2 over limit: five_hour_pct")
    else:
        skipped, = events(b, t, "agent_review_skipped")
        assert "claude2: account cl2 over limit: five_hour_pct" in skipped["detail"]["reason"]
    assert events(b, t, "review") == []


# @pt.id KIT-LIMIT-110-reporting
# @pt.item Limit status in list_accounts and health
# @pt.condition cl1 has five_hour_pct 80 and reports 90 % used; cl2 has no limits
# @pt.expected list_accounts reports cl1 limited with its checks and spend, cl2's entry has no limits key; health lists only cl1 with status and reason
# @pt.target Bridge.list_accounts, Bridge.health, Bridge._limit_report
# @pt.method integration
# @pt.legacy none
def test_limit_reporting(make_orch, fake_claude):
    b = make_orch(config(cl1=FIVE_HOUR))
    seed(b, "cl1")
    accounts = {a["name"]: a for a in b.list_accounts()["accounts"]}
    assert "limits" not in accounts["cl2"]
    listing = {"cl1": accounts["cl1"]["limits"]}
    assert listing["cl1"]["status"] == "limited"
    assert listing["cl1"]["reason"].startswith("five_hour_pct: 90.0% used >= 80%")
    check, = listing["cl1"]["checks"]
    assert check["limit"] == "five_hour_pct" and check["breached"] is True
    assert set(listing["cl1"]["spend"]) == {"day", "week", "month"}
    health = run(b.health())
    assert set(health["limits"]) == {"cl1"}
    assert health["limits"]["cl1"]["status"] == "limited"
    assert health["limits"]["cl1"]["reason"] == listing["cl1"]["reason"]
