"""Provider-reported run cost in the adapters and the machine-wide usage ledger in the Store."""

import subprocess
import sys
import threading
import time

import pytest

from bridge.config import BridgeConfig
from bridge.executor import ClaudeAdapter, CodexAdapter, OpenCodeAdapter, RunResult
from bridge.state import LimitExceeded, Store

KEY = "acct-key"
DAY = 86400.0


def _ctx():
    return {"on_session": None, "errors": [], "completed": False}


def _claude_result(**extra):
    return {"type": "result", "subtype": "success", "is_error": False, "result": "{}",
            "session_id": "s", "usage": {"input_tokens": 1}, "structured_output": {},
            **extra}


def _store(tmp_path, name="local.db", shared=True):
    return Store(tmp_path / name, tmp_path / "accounts.db" if shared else None)


def _ok(_spend):
    return None


def _rows(store):
    return [dict(r) for r in store.usage_db.execute("SELECT * FROM account_usage ORDER BY id")]


# @pt.id KIT-USAGE-001
# @pt.item Claude adapter captures total_cost_usd from the result event
# @pt.condition result events with a valid, missing, negative or boolean total_cost_usd
# @pt.expected cost_usd is the float cost only for a valid non-negative number; usage unchanged
# @pt.target bridge.executor.ClaudeAdapter.handle
# @pt.method unit
# @pt.legacy none
def test_claude_result_cost(tmp_path):
    ad = ClaudeAdapter(BridgeConfig(), tmp_path)
    res = RunResult(outcome="ok")
    ad.handle(_claude_result(total_cost_usd=0.25), res, _ctx())
    assert res.cost_usd == 0.25 and res.usage == {"input_tokens": 1}
    res = RunResult(outcome="ok")
    ad.handle(_claude_result(total_cost_usd=1), res, _ctx())
    assert res.cost_usd == 1.0 and isinstance(res.cost_usd, float)
    for extra in ({}, {"total_cost_usd": -0.5}, {"total_cost_usd": True},
                  {"total_cost_usd": "0.3"}):
        res = RunResult(outcome="ok")
        ad.handle(_claude_result(**extra), res, _ctx())
        assert res.cost_usd is None


# @pt.id KIT-USAGE-002
# @pt.item OpenCode adapter sums per-step cost
# @pt.condition two step_finish events with costs and one without a numeric cost
# @pt.expected cost_usd is the sum; None before any cost is seen
# @pt.target bridge.executor.OpenCodeAdapter.handle
# @pt.method unit
# @pt.legacy none
def test_opencode_cost_summed(tmp_path):
    ad = OpenCodeAdapter(BridgeConfig(), tmp_path)
    res, ctx = RunResult(outcome="ok"), _ctx()
    ad.handle({"type": "step_finish", "part": {"tokens": {"input": 3}}}, res, ctx)
    assert res.cost_usd is None
    ad.handle({"type": "step_finish", "part": {"cost": 0.1, "tokens": {"input": 3}}}, res, ctx)
    ad.handle({"type": "step_finish", "part": {"cost": True}}, res, ctx)
    ad.handle({"type": "step_finish", "part": {"cost": 0.2, "tokens": {"input": 4}}}, res, ctx)
    assert res.cost_usd == pytest.approx(0.3)
    assert res.usage == {"tokens": {"input": 4}, "cost": 0.2}


# @pt.id KIT-USAGE-003
# @pt.item Codex reports tokens only
# @pt.condition a turn.completed event with usage
# @pt.expected usage is recorded and cost_usd stays None
# @pt.target bridge.executor.CodexAdapter.handle
# @pt.method unit
# @pt.legacy none
def test_codex_cost_none(tmp_path):
    ad = CodexAdapter(BridgeConfig(), tmp_path)
    res = RunResult(outcome="ok")
    ad.handle({"type": "turn.completed", "usage": {"input_tokens": 5}}, res, _ctx())
    assert res.usage == {"input_tokens": 5} and res.cost_usd is None


# @pt.id KIT-USAGE-004
# @pt.item Reserve then settle updates spend per period
# @pt.condition one reservation settled with an actual cost, then settled again
# @pt.expected reserved before settle, spent after; second settle is a no-op
# @pt.target bridge.state.Store.reserve_usage, settle_usage, usage_spend
# @pt.method unit
# @pt.legacy none
def test_reserve_then_settle(tmp_path):
    s = _store(tmp_path)
    now = time.time()
    starts = {"day": now - DAY, "week": now - 7 * DAY}
    uid = s.reserve_usage("work", key=KEY, task_id="T0001", reserve=2.0, starts=starts,
                          check=_ok)
    spend = s.usage_spend(KEY, starts)
    for p in starts:
        assert spend[p] == {"spent": 0.0, "reserved": 2.0, "unknown": 0, "estimated": False,
                            "runs": 1}
    s.settle_usage(uid, 0.75, "actual")
    s.settle_usage(uid, 9.0, "estimated")  # no-op
    spend = s.usage_spend(KEY, starts)
    for p in starts:
        assert spend[p] == {"spent": 0.75, "reserved": 0.0, "unknown": 0, "estimated": False,
                            "runs": 1}
    with pytest.raises(ValueError):
        s.settle_usage(uid, 1.0, "guess")


# @pt.id KIT-USAGE-005
# @pt.item record_usage, unknown and estimated costs
# @pt.condition settled rows with actual, gated NULL, ungated NULL and estimated costs
# @pt.expected actual counts as spent, gated NULL as unknown, ungated NULL not as unknown, estimated sets the flag
# @pt.target bridge.state.Store.record_usage, usage_spend
# @pt.method unit
# @pt.legacy none
def test_record_unknown_estimated(tmp_path):
    s = _store(tmp_path)
    starts = {"day": time.time() - DAY}
    s.record_usage("work", key=KEY, task_id="T1", cost_usd=1.5, source="actual")
    assert s.usage_spend(KEY, starts)["day"] == {"spent": 1.5, "reserved": 0.0, "unknown": 0,
                                                 "estimated": False, "runs": 1}
    s.record_usage("work", key=KEY, task_id="T2", cost_usd=None, source="unavailable",
                   gated=True)
    uid = s.reserve_usage("work", key=KEY, task_id="T3", reserve=1.0, starts=starts, check=_ok)
    s.settle_usage(uid, 1.0, "estimated")
    spend = s.usage_spend(KEY, starts)["day"]
    assert spend == {"spent": 2.5, "reserved": 0.0, "unknown": 1, "estimated": True, "runs": 3}
    s.record_usage("work", key=KEY, task_id="T4", cost_usd=None, source="unavailable")
    spend = s.usage_spend(KEY, starts)["day"]
    assert spend["unknown"] == 1 and spend["runs"] == 4
    assert s.usage_spend("other", starts)["day"]["runs"] == 0


# @pt.id KIT-USAGE-006
# @pt.item Period starts and retention
# @pt.condition rows before a period start, and a row older than 40 days
# @pt.expected earlier rows are excluded from that period; rows over 40 days old are pruned
# @pt.target bridge.state.Store._spend, reserve_usage
# @pt.method unit
# @pt.legacy none
def test_period_start_and_prune(tmp_path):
    s = _store(tmp_path)
    now = time.time()
    old = s.record_usage("work", key=KEY, task_id="T1", cost_usd=1.0, source="actual")
    mid = s.record_usage("work", key="other", task_id="T2", cost_usd=2.0, source="actual")
    s.usage_db.execute("UPDATE account_usage SET ts=? WHERE id=?", (now - 41 * DAY, old))
    s.usage_db.execute("UPDATE account_usage SET ts=? WHERE id=?", (now - 3 * DAY, mid))
    s.record_usage("work", key="other", task_id="T3", cost_usd=4.0, source="actual")
    starts = {"day": now - DAY, "week": now - 7 * DAY, "long": now - 60 * DAY}
    spend = s.usage_spend("other", starts)
    assert spend["day"]["spent"] == 4.0 and spend["day"]["runs"] == 1
    assert spend["week"]["spent"] == 6.0 and spend["week"]["runs"] == 2
    assert s.usage_spend(KEY, starts)["long"]["runs"] == 1  # not pruned yet
    s.reserve_usage("work", key="third", task_id="T4", reserve=0.0, starts=starts, check=_ok)
    assert old not in [r["id"] for r in _rows(s)]
    assert s.usage_spend(KEY, starts)["long"]["runs"] == 0


# @pt.id KIT-USAGE-007
# @pt.item A refused check inserts nothing
# @pt.condition check() returns a reason
# @pt.expected LimitExceeded with the reason; no row inserted; check saw the spend
# @pt.target bridge.state.Store.reserve_usage
# @pt.method unit
# @pt.legacy none
def test_check_refusal(tmp_path):
    s = _store(tmp_path)
    starts = {"day": time.time() - DAY}
    seen = []

    def check(spend):
        seen.append(spend)
        return "daily budget reached"

    with pytest.raises(LimitExceeded, match="daily budget reached"):
        s.reserve_usage("work", key=KEY, task_id="T1", reserve=1.0, starts=starts, check=check)
    assert _rows(s) == [] and seen[0]["day"]["runs"] == 0
    assert isinstance(LimitExceeded("x"), ValueError)


# @pt.id KIT-USAGE-008
# @pt.item Two Stores share one ledger
# @pt.condition different local databases sharing one accounts.db
# @pt.expected each sees the other's reservation; the ledger is not in the local databases
# @pt.target bridge.state.Store.usage_db
# @pt.method unit
# @pt.legacy none
def test_shared_ledger(tmp_path):
    a, b = _store(tmp_path, "a.db"), _store(tmp_path, "b.db")
    starts = {"day": time.time() - DAY}
    a.reserve_usage("work", key=KEY, task_id="T1", reserve=1.0, starts=starts, check=_ok)
    assert b.usage_spend(KEY, starts)["day"]["reserved"] == 1.0
    b.reserve_usage("work", key=KEY, task_id="T2", reserve=2.0, starts=starts, check=_ok)
    assert a.usage_spend(KEY, starts)["day"]["reserved"] == 3.0
    assert a.usage_db is a.shared
    assert a.db.execute("SELECT name FROM sqlite_master WHERE name='account_usage'"
                        ).fetchone() is None


# @pt.id KIT-USAGE-009
# @pt.item Reservations are atomic under a race
# @pt.condition two threads with separate Stores reserve at once; check allows one more run
# @pt.expected exactly one succeeds and the other raises LimitExceeded
# @pt.target bridge.state.Store.reserve_usage
# @pt.method unit
# @pt.legacy none
def test_reservation_race(tmp_path):
    stores = [_store(tmp_path, "a.db"), _store(tmp_path, "b.db")]
    starts = {"day": time.time() - DAY}
    barrier = threading.Barrier(2)
    results = []

    def check(spend):
        time.sleep(0.05)  # widen the window between reading spend and inserting
        return "full" if spend["day"]["runs"] >= 1 else None

    def run(store):
        barrier.wait()
        try:
            results.append(store.reserve_usage("work", key=KEY, task_id="T", reserve=1.0,
                                               starts=starts, check=check))
        except LimitExceeded:
            results.append("refused")

    threads = [threading.Thread(target=run, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(map(str, results)).count("refused") == 1 and len(results) == 2
    assert len(_rows(stores[0])) == 1


# @pt.id KIT-USAGE-010
# @pt.item Stale reservations are charged (crash safety)
# @pt.condition reservations owned by a dead pid, by a non-matching owner_started, with and
#               without a reserved amount, and one owned by this process
# @pt.expected charged reserved_usd as estimated, or unknown when 0; own reservation stays
# @pt.target bridge.state.Store.usage_spend
# @pt.method unit
# @pt.legacy none
def test_stale_reservations(tmp_path):
    s = _store(tmp_path)
    starts = {"day": time.time() - DAY}
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead = s.reserve_usage("work", key=KEY, task_id="T1", reserve=1.5, starts=starts, check=_ok)
    stranger = s.reserve_usage("work", key=KEY, task_id="T2", reserve=0.0, starts=starts,
                               check=_ok)
    mine = s.reserve_usage("work", key=KEY, task_id="T3", reserve=0.5, starts=starts, check=_ok)
    s.usage_db.execute("UPDATE account_usage SET owner_pid=? WHERE id=?", (proc.pid, dead))
    s.usage_db.execute("UPDATE account_usage SET owner_started='never' WHERE id=?", (stranger,))
    spend = s.usage_spend(KEY, starts)["day"]
    assert spend == {"spent": 1.5, "reserved": 0.5, "unknown": 1, "estimated": True, "runs": 3}
    rows = {r["id"]: r for r in _rows(s)}
    assert (rows[dead]["state"], rows[dead]["cost_usd"], rows[dead]["source"]) == \
        ("settled", 1.5, "estimated")
    assert (rows[stranger]["state"], rows[stranger]["cost_usd"], rows[stranger]["source"]) == \
        ("settled", None, "unavailable")
    assert rows[mine]["state"] == "reserved"
    s.settle_usage(dead, 0.1, "actual")  # late settle after expiry does nothing
    assert s.usage_spend(KEY, starts)["day"]["spent"] == 1.5


# @pt.id KIT-USAGE-011
# @pt.item Ledger without a shared database
# @pt.condition a Store created with shared=None
# @pt.expected the ledger lives in the Store's own database and works
# @pt.target bridge.state.Store.usage_db
# @pt.method unit
# @pt.legacy none
def test_local_ledger(tmp_path):
    s = _store(tmp_path, shared=False)
    assert s.shared is None and s.usage_db is s.db
    starts = {"day": time.time() - DAY}
    uid = s.reserve_usage("work", key="name:work", task_id="T1", reserve=1.0, starts=starts,
                          check=_ok)
    s.settle_usage(uid, 0.4, "actual")
    assert s.usage_spend("name:work", starts)["day"]["spent"] == 0.4
    assert not (tmp_path / "accounts.db").exists()


# @pt.id KIT-USAGE-012
# @pt.item OpenCode adapter ignores malformed step costs
# @pt.condition step_finish costs 0.25, -5, NaN, inf, True, then 0.5
# @pt.expected only the finite non-negative costs are summed: cost_usd == 0.75
# @pt.target bridge.executor.OpenCodeAdapter.handle
# @pt.method unit
# @pt.legacy none
def test_opencode_cost_rejects_malformed(tmp_path):
    ad = OpenCodeAdapter(BridgeConfig(), tmp_path)
    res, ctx = RunResult(outcome="ok"), _ctx()
    for cost in (0.25, -5, float("nan"), float("inf"), True, 0.5):
        ad.handle({"type": "step_finish", "part": {"cost": cost}}, res, ctx)
    assert res.cost_usd == 0.75


# @pt.id KIT-USAGE-BILL-01-reported
# @pt.item A CLI-reported cost is an estimate, not a bill
# @pt.condition one settled row with source reported
# @pt.expected it counts as spent and sets the estimated flag
# @pt.target bridge.state.Store.usage_spend
# @pt.method unit
# @pt.legacy audit BILL-01: Claude's total_cost_usd was recorded as actual
def test_reported_cost_is_estimated(tmp_path):
    s = _store(tmp_path)
    starts = {"day": time.time() - DAY}
    s.record_usage("work", key=KEY, task_id="T1", cost_usd=0.4, source="reported")
    assert s.usage_spend(KEY, starts)["day"] == {"spent": 0.4, "reserved": 0.0, "unknown": 0,
                                                 "estimated": True, "runs": 1}
