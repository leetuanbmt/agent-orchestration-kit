"""Hardening of the usage ledger and the limit gate: threads, liveness, lock scope, retries,
never-started runs, ledger write errors, unrouting and config-check warnings."""

import argparse
import asyncio
import sqlite3
import threading
import time

import pytest

from bridge import cli, osutil
from bridge import state as state_module
from bridge.state import LimitExceeded, Store
from tests.conftest import run
from tests.integration.test_account_limits import SINGLE, WRITE, config, day_spend, task
from tests.integration.test_orchestration import events, log

KEY = "acct-key"
DAY = 86400.0
BUDGET = "{budget_usd: {day: 10.0}, reserve_usd: 0.5}"


def _starts() -> dict[str, float]:
    return {"day": time.time() - DAY}


def _ok(_spend):
    return None


def _foreign(store: Store, usage_id: int, pid: int = 424242, started: str = "then") -> None:
    """Hand a reservation to another (pretend) process."""
    store.usage_db.execute("UPDATE account_usage SET owner_pid=?, owner_started=? WHERE id=?",
                           (pid, started, usage_id))


def _row(store: Store, usage_id: int) -> dict:
    return dict(store.usage_db.execute("SELECT * FROM account_usage WHERE id=?",
                                       (usage_id,)).fetchone())


def _task_rows(b, task_id: str) -> list[dict]:
    return [dict(r) for r in b.store.usage_db.execute(
        "SELECT * FROM account_usage WHERE task_id=? ORDER BY id", (task_id,))]


# @pt.id KIT-HARDEN-001-threads
# @pt.item Store transactions are safe from two threads on one connection
# @pt.condition A Store without a shared database; two threads each run 200 rounds of record_usage, usage_spend and a tx() write (log) at the same time
# @pt.expected No thread raises; the ledger has 400 rows and the events table 400 hammer events
# @pt.target bridge.state.Store.tx, Store._usage_tx
# @pt.method integration
# @pt.legacy none
def test_threads_share_store(tmp_path):
    s = Store(tmp_path / "local.db", None)
    rounds, errors = 200, []
    barrier = threading.Barrier(2)

    def hammer(n: int) -> None:
        try:
            barrier.wait()
            for _ in range(rounds):
                s.record_usage("work", key=KEY, task_id=f"T{n}", cost_usd=0.01, source="actual")
                s.usage_spend(KEY, _starts())
                s.log(None, "test", "hammer")
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert s.usage_db.execute("SELECT COUNT(*) FROM account_usage").fetchone()[0] == 2 * rounds
    assert s.db.execute("SELECT COUNT(*) FROM events WHERE kind='hammer'").fetchone()[0] \
        == 2 * rounds
    assert s.usage_spend(KEY, _starts())["day"]["runs"] == 2 * rounds


# @pt.id KIT-HARDEN-002-ps-failure
# @pt.item A failed ps never expires a live reservation
# @pt.condition A reservation owned by another process whose pid is alive; process_started returns None (ps failed), then a different start time
# @pt.expected With None the reservation stays reserved through usage_spend and reserve_usage; with a different start time it is settled as estimated
# @pt.target bridge.state.Store._dead_owners
# @pt.method integration
# @pt.legacy a ps failure counted as a dead owner and released the reservation
def test_ps_failure_keeps_reservation(tmp_path, monkeypatch):
    s = Store(tmp_path / "local.db", tmp_path / "accounts.db")
    held = s.reserve_usage("work", key=KEY, task_id="T1", reserve=0.5, starts=_starts(),
                           check=_ok)
    _foreign(s, held)
    monkeypatch.setattr(osutil, "alive", lambda pid: True)
    monkeypatch.setattr(state_module, "process_started", lambda pid: None)
    assert s.usage_spend(KEY, _starts())["day"]["reserved"] == 0.5
    s.reserve_usage("work", key=KEY, task_id="T2", reserve=0.0, starts=_starts(), check=_ok)
    assert _row(s, held)["state"] == "reserved"

    monkeypatch.setattr(state_module, "process_started", lambda pid: "another start")
    s.usage_spend(KEY, _starts())
    row = _row(s, held)
    assert (row["state"], row["cost_usd"], row["source"]) == ("settled", 0.5, "estimated")


# @pt.id KIT-HARDEN-003-read-off-lock
# @pt.item Reading spend takes no write lock
# @pt.condition Another sqlite3 connection holds BEGIN IMMEDIATE on the ledger database; the only open reservation belongs to this process
# @pt.expected usage_spend returns the spend in well under the 10 s busy timeout
# @pt.target bridge.state.Store.usage_spend
# @pt.method integration
# @pt.legacy usage_spend opened BEGIN IMMEDIATE and waited for the other writer
def test_usage_spend_reads_while_locked(tmp_path):
    shared = tmp_path / "accounts.db"
    s = Store(tmp_path / "local.db", shared)
    s.reserve_usage("work", key=KEY, task_id="T1", reserve=0.5, starts=_starts(), check=_ok)
    s.record_usage("work", key=KEY, task_id="T0", cost_usd=0.25, source="actual")
    other = sqlite3.connect(shared, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        spend = s.usage_spend(KEY, _starts())["day"]
        assert time.monotonic() - started < 2.0
        assert spend["spent"] == 0.25 and spend["reserved"] == 0.5 and spend["runs"] == 2
    finally:
        other.execute("ROLLBACK")
        other.close()


# @pt.id KIT-HARDEN-004-no-ps-under-lock
# @pt.item No ps subprocess runs while the ledger write lock is held
# @pt.condition A foreign reservation whose pid is alive; process_started is replaced by a probe that opens a second connection and tries BEGIN IMMEDIATE without waiting
# @pt.expected The probe gets the write lock every time (in usage_spend and reserve_usage), and the stale reservation is still settled
# @pt.target bridge.state.Store._dead_owners, Store.usage_spend, Store.reserve_usage
# @pt.method integration
# @pt.legacy ps ran inside the BEGIN IMMEDIATE transaction
def test_no_ps_under_write_lock(tmp_path, monkeypatch):
    shared = tmp_path / "accounts.db"
    s = Store(tmp_path / "local.db", shared)
    calls = []

    def probe(pid):
        other = sqlite3.connect(shared, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")  # raises "database is locked" under the lock
            other.execute("ROLLBACK")
        finally:
            other.close()
        calls.append(pid)
        return "another start"

    monkeypatch.setattr(osutil, "alive", lambda pid: True)
    monkeypatch.setattr(state_module, "process_started", probe)
    first = s.reserve_usage("work", key=KEY, task_id="T1", reserve=0.5, starts=_starts(),
                            check=_ok)
    _foreign(s, first)
    assert s.usage_spend(KEY, _starts())["day"]["spent"] == 0.5
    assert _row(s, first)["state"] == "settled"

    second = s.reserve_usage("work", key=KEY, task_id="T2", reserve=0.5, starts=_starts(),
                             check=_ok)
    _foreign(s, second, pid=434343)
    s.reserve_usage("work", key=KEY, task_id="T3", reserve=0.0, starts=_starts(), check=_ok)
    assert _row(s, second)["state"] == "settled"
    assert calls == [424242, 434343]


# @pt.id KIT-HARDEN-005-retries-charged
# @pt.item Network-retried attempts are charged
# @pt.condition cl1 has budget_usd.day 10 and reserve_usd 0.5; the CLI fails with a network error then succeeds; each attempt reports total_cost_usd 0.25
# @pt.expected Both attempts are recorded (two settled rows of 0.25 actual), one retry event, no limit_blocked, the task finishes DONE
# @pt.target bridge.service.Bridge._run_inner
# @pt.method integration
# @pt.legacy only the last attempt of a retried run was charged
def test_network_retries_are_charged(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "network,ok")
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.25")
    b = make_orch(config(cl1=BUDGET, roles=SINGLE))
    t = task(b, "Set x to 2")
    assert run(b.execute_task(t, wait=True))["state"] == "DONE"
    rows = _task_rows(b, t)
    assert [(r["state"], r["cost_usd"], r["source"]) for r in rows] == \
        [("settled", 0.25, "actual")] * 2
    assert len(events(b, t, "retry")) == 1 and len(events(b, t, "usage_recorded")) == 2
    assert events(b, t, "limit_blocked") == []
    assert len(log(fake_claude)) == 2


# @pt.id KIT-HARDEN-010-retry-over-limit
# @pt.item A network retry is skipped when the failed attempt pushed the account over its limit
# @pt.condition cl1 has budget_usd.day 1.0 and reserve_usd 0.5; the CLI fails with a network error reporting total_cost_usd 0.6 (0.6 spent + 0.5 reserved > 1.0), then would succeed
# @pt.expected limit_blocked with retry=true and the budget reason; no retry event; the task pauses NETWORK; the agent ran once; the ledger charges 0.6 once (the reservation is released at 0.0)
# @pt.target bridge.service.Bridge._run_inner
# @pt.method integration
# @pt.legacy the retry ran regardless of the budget
def test_network_retry_skipped_over_limit(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "network,ok")
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.6")
    b = make_orch(config(cl1="{budget_usd: {day: 1.0}, reserve_usd: 0.5}", roles=SINGLE))
    t = task(b, "Set x to 2")
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == "PAUSED"
    assert b.store.get(t)["failure"].startswith("NETWORK")
    blocked, = events(b, t, "limit_blocked")
    assert blocked["detail"]["account"] == "cl1" and blocked["detail"]["retry"] is True
    assert blocked["detail"]["reason"].startswith("budget_usd.day")
    assert events(b, t, "retry") == []
    assert len(log(fake_claude)) == 1
    day = day_spend(b, "cl1")
    assert day["spent"] == pytest.approx(0.6) and day["reserved"] == 0 and day["unknown"] == 0
    assert sorted(r["cost_usd"] for r in _task_rows(b, t)) == [0.0, 0.6]


# @pt.id KIT-HARDEN-006-never-started
# @pt.item A run that fails before the agent starts costs nothing
# @pt.condition cl1 has a budget with reserve_usd 0.5; the attempt setup (snapshot) raises before adapter.run; then a second task whose cost recording raises after adapter.run
# @pt.expected The first reservation is settled 0.0 actual, the task FAILED and the agent never ran; the second is settled with unknown cost (fail closed)
# @pt.target bridge.service.Bridge._run, Bridge._run_inner
# @pt.method integration
# @pt.legacy a never-started run was charged as unknown cost and blocked the budget
def test_never_started_run_is_free(make_orch, fake_claude, monkeypatch):
    import bridge.service as service_module
    b = make_orch(config(cl1=BUDGET, roles=SINGLE))

    def broken(*args, **kwargs):
        raise RuntimeError("setup failed")

    with monkeypatch.context() as m:
        m.setattr(service_module, "snapshot", broken)
        t = task(b, "Set x to 2")
        assert run(b.execute_task(t, wait=True))["state"] == "FAILED"
    row, = _task_rows(b, t)
    assert (row["state"], row["cost_usd"], row["source"]) == ("settled", 0.0, "actual")
    assert log(fake_claude) == []

    with monkeypatch.context() as m:
        m.setattr(b, "_record_account", broken)
        t2 = task(b, "Set x to 2 again")
        assert run(b.execute_task(t2, wait=True))["state"] == "FAILED"
    row, = _task_rows(b, t2)
    assert (row["state"], row["cost_usd"], row["source"]) == ("settled", None, "unavailable")
    assert len(log(fake_claude)) == 1


# @pt.id KIT-HARDEN-007-ledger-error
# @pt.item A ledger write error does not change the run's outcome
# @pt.condition cl1 has a budget; settle_usage raises sqlite3.OperationalError
# @pt.expected The task finishes DONE; usage_record_failed names the account and the error; no usage_recorded event
# @pt.target bridge.service.Bridge._record_account
# @pt.method integration
# @pt.legacy the error escaped _record_account and the run was marked FAILED
def test_ledger_error_keeps_outcome(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.25")
    b = make_orch(config(cl1=BUDGET, roles=SINGLE))

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(b.store, "settle_usage", locked)
    t = task(b, "Set x to 2")
    assert run(b.execute_task(t, wait=True))["state"] == "DONE"
    failed, = events(b, t, "usage_record_failed")
    assert failed["detail"] == {"account": "cl1", "error": "database is locked"}
    assert events(b, t, "usage_recorded") == []
    assert events(b, t, "error") == []


# @pt.id KIT-HARDEN-008-unroute
# @pt.item A freshly routed task refused by the limit gate is unrouted
# @pt.condition An unpinned task routes to claude1; the start gate (_reserve) then raises LimitExceeded (another bridge used the budget meanwhile)
# @pt.expected execute_task raises LimitExceeded (a ValueError) with the limit message; the task has no agent or account, stays READY with 0 attempts, and logs agent_unrouted and limit_blocked
# @pt.target bridge.service.Bridge.execute_task, Bridge._start, Bridge._unroute
# @pt.method integration
# @pt.legacy the task stayed bound to the limited account
def test_limit_refusal_unroutes(make_orch, fake_claude, monkeypatch):
    b = make_orch(config(cl1=BUDGET, roles=SINGLE))

    def refuse(name, task_id):
        raise LimitExceeded("budget_usd.day reached")

    monkeypatch.setattr(b, "_reserve", refuse)
    t = task(b, "Set x to 2")
    with pytest.raises(LimitExceeded, match="account cl1 over its configured limit") as error:
        run(b.execute_task(t, wait=True))
    assert isinstance(error.value, ValueError)
    row = b.store.get(t)
    assert row["agent"] is None and row["account"] is None
    assert row["state"] == "READY" and row["attempts"] == 0
    unrouted, = events(b, t, "agent_unrouted")
    assert unrouted["detail"]["agent"] == "claude1" and unrouted["detail"]["account"] == "cl1"
    assert "over its configured limit" in unrouted["detail"]["reason"]
    assert len(events(b, t, "limit_blocked")) == 1
    assert log(fake_claude) == []


# @pt.id KIT-HARDEN-009-config-warnings
# @pt.item config-check warns about budgets that fail closed
# @pt.condition cl1 has budget_usd without pricing or reserve_usd; then cl1 has reserve_usd, cl2 pricing and a disabled budget-only limit elsewhere
# @pt.expected The first config gets exactly one warning naming accounts.cl1.limits; the second has no warnings key; the existing keys are unchanged
# @pt.target bridge.cli._run, bridge.cli._limit_warnings
# @pt.method integration
# @pt.legacy none
def test_config_check_warnings(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    target = root / ".ai" / "orchestrator.yaml"
    homes = str(tmp_path / "homes")

    def check(cl1: str, cl2: str = "") -> dict:
        target.write_text(config(cl1=cl1, cl2=cl2).replace("{home}", homes))
        return asyncio.run(cli._run(argparse.Namespace(cmd="config-check")))

    out = check("{budget_usd: {day: 1.0}}")
    assert out["ok"] is True and out["accounts"] == ["cl1", "cl2"]
    assert out["warnings"] == [
        "accounts.cl1.limits: budget_usd without pricing or reserve_usd: a run whose CLI "
        "reports no cost (Codex) or that fails before reporting blocks the budget until the "
        "period resets"]

    out = check("{budget_usd: {day: 1.0}, reserve_usd: 0.5}",
                "{budget_usd: {day: 1.0}, pricing: {input_per_mtok: 1, output_per_mtok: 1}}")
    assert "warnings" not in out
    assert set(out) == {"ok", "source", "stages", "accounts", "agents", "roles"}
    out = check("{enabled: false, budget_usd: {day: 1.0}}", "{five_hour_pct: 80}")
    assert "warnings" not in out
