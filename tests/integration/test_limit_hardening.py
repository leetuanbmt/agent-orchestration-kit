"""Hardening of the usage ledger and the limit gate: threads, liveness, lock scope, retries,
never-started runs, ledger write errors, unrouting and config-check warnings."""

import argparse
import asyncio
import inspect
import math
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from bridge import cli, osutil, quota
from bridge import state as state_module
from bridge.executor import RunResult
from bridge.models import Actor, TaskSpec
from bridge.orchestrator import AccountLimits, TokenPricing, UsdBudget
from bridge.service import Bridge
from bridge.state import LimitExceeded, Store
from tests.conftest import run, spec
from tests.integration import test_limit_failover as failover
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
        [("settled", 0.25, "reported")] * 2
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
    estimate = ("budget_usd counts list-price estimates (the CLI's total_cost_usd or your "
                "pricing), not your bill; on a subscription login (Pro, Max, ChatGPT) they are "
                "not charges, so use the *_pct limits")
    assert out["warnings"] == [
        "accounts.cl1.limits: budget_usd without pricing or reserve_usd: a run whose CLI "
        "reports no cost (Codex) or that fails before reporting blocks the budget until the "
        "period resets", f"accounts.cl1.limits: {estimate}"]

    out = check("{budget_usd: {day: 1.0}, reserve_usd: 0.5}",
                "{budget_usd: {day: 1.0}, pricing: {input_per_mtok: 1, output_per_mtok: 1}}")
    assert out["warnings"] == [f"accounts.cl1.limits: {estimate}", f"accounts.cl2.limits: {estimate}"]
    assert set(out) == {"ok", "source", "stages", "accounts", "agents", "roles", "warnings"}
    out = check("{enabled: false, budget_usd: {day: 1.0}}", "{five_hour_pct: 80}")
    assert "warnings" not in out


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# @pt.id KIT-HARDEN-011-connection-lock
# @pt.item Every statement and fetch on a Store connection holds the Store lock
# @pt.condition A Store with a shared database; one thread runs 200 rounds of a multi-statement tx() plus log and update, the other 200 rounds of get, account_state, quota and update; then a read from another thread while a transaction is open
# @pt.expected Both connections hand out locked cursors; no thread raises; every write is present; the read waits until the transaction ends
# @pt.target bridge.state._LockedConnection, _LockedCursor, Store.tx
# @pt.method integration
# @pt.legacy only transactions took the lock, so unlocked reads ran mid-transaction on the same connection
def test_statements_hold_store_lock(tmp_path):
    s = Store(tmp_path / "local.db", tmp_path / "accounts.db")
    for conn in (s.db, s.shared):
        assert isinstance(conn.execute("SELECT 1"), state_module._LockedCursor)
    tid = s.create(TaskSpec(**spec()), "hash-1")[0]["id"]
    s.save_quota("work", {"primary": {"used_percent": 10}}, key=KEY)
    rounds, errors = 200, []
    barrier = threading.Barrier(2)

    def writer() -> None:
        try:
            barrier.wait()
            for _ in range(rounds):
                with s.tx() as c:
                    s._event(c, tid, "test", "tx_write")
                    c.execute("UPDATE tasks SET attempts=attempts+1 WHERE id=?", (tid,))
                s.log(tid, "test", "logged")
                s.update(tid, failure="written")
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    def reader() -> None:
        try:
            barrier.wait()
            for i in range(rounds):
                assert s.get(tid)["id"] == tid
                assert s.account_state("work", key=KEY)["state"] == "available"
                assert s.quota("work", key=KEY)["rate_limits"]["primary"]["used_percent"] == 10
                s.update(tid, session_id=f"s{i}")
        except BaseException as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert s.count_events(tid, "tx_write") == rounds
    assert s.count_events(tid, "logged") == rounds
    row = s.get(tid)
    assert row["attempts"] == rounds and row["session_id"] == f"s{rounds - 1}"
    assert row["failure"] == "written"

    started, done = threading.Event(), threading.Event()

    def late_read() -> None:
        started.set()
        s.get(tid)
        done.set()

    with s.tx() as c:
        c.execute("UPDATE tasks SET failure='held' WHERE id=?", (tid,))
        other = threading.Thread(target=late_read)
        other.start()
        assert started.wait(5)
        assert not done.wait(0.3)  # blocked: the transaction holds the lock throughout
    other.join(10)
    assert done.is_set()


# @pt.id KIT-HARDEN-012-unknown-owner-start
# @pt.item A reservation with no recorded owner start time is kept while its pid lives
# @pt.condition A reservation handed to a live child process with owner_started NULL; ps reports a real start time for that pid; then the child exits
# @pt.expected While the child runs the reservation stays reserved through usage_spend and reserve_usage; once it exits it is settled as estimated
# @pt.target bridge.state.Store._dead_owners
# @pt.method integration
# @pt.legacy a NULL owner_started never matched the ps start time, so a live owner's reservation was expired
def test_unknown_owner_start_keeps_reservation(tmp_path):
    s = Store(tmp_path / "local.db", tmp_path / "accounts.db")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert state_module.process_started(proc.pid) is not None
        held = s.reserve_usage("work", key=KEY, task_id="T1", reserve=0.5, starts=_starts(),
                               check=_ok)
        _foreign(s, held, pid=proc.pid, started=None)
        assert s.usage_spend(KEY, _starts())["day"]["reserved"] == 0.5
        s.reserve_usage("work", key=KEY, task_id="T2", reserve=0.0, starts=_starts(), check=_ok)
        assert _row(s, held)["state"] == "reserved"
    finally:
        proc.kill()
        proc.wait()
    s.usage_spend(KEY, _starts())
    row = _row(s, held)
    assert (row["state"], row["cost_usd"], row["source"]) == ("settled", 0.5, "estimated")


OLD_USAGE_SCHEMA = """
CREATE TABLE account_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, name TEXT, task_id TEXT,
    ts REAL NOT NULL, state TEXT NOT NULL, reserved_usd REAL NOT NULL DEFAULT 0, cost_usd REAL,
    source TEXT, owner_pid INTEGER, owner_started TEXT);
"""


# @pt.id KIT-HARDEN-013-gated-unknown
# @pt.item Only gated ledger rows count as unknown cost for a budget
# @pt.condition An old ledger without the gated column holding an unknown-cost row; record_usage with and without gated; an expired zero reservation; then a bridge whose cl1 records an unknown-cost run without a budget, gets a budget, and records an unknown-cost network-retried attempt
# @pt.expected The migrated and ungated rows are gated=0 and never unknown; gated rows and expired reservations are; the budget stays ok after the unbudgeted run and is limited with an unknown-cost reason after the retried attempt
# @pt.target bridge.state.Store.__init__, Store._spend, Store.record_usage, Store.reserve_usage, bridge.service.Bridge._record_account
# @pt.method integration
# @pt.legacy any unknown-cost row, including runs from before the budget existed, blocked the budget
def test_only_gated_unknown_blocks(tmp_path, make_orch, fake_claude):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(OLD_USAGE_SCHEMA)
    conn.execute("INSERT INTO account_usage(key, ts, state, source) VALUES (?,?,'settled',"
                 "'unavailable')", (KEY, time.time()))
    conn.commit()
    conn.close()
    s = Store(tmp_path / "local.db", old)
    assert _row(s, 1)["gated"] == 0
    assert s.usage_spend(KEY, _starts())["day"]["unknown"] == 0
    plain = s.record_usage("work", key=KEY, task_id="T1", cost_usd=None, source="unavailable")
    assert _row(s, plain)["gated"] == 0
    assert s.usage_spend(KEY, _starts())["day"]["unknown"] == 0
    s.record_usage("work", key=KEY, task_id="T2", cost_usd=None, source="unavailable",
                   gated=True)
    assert s.usage_spend(KEY, _starts())["day"]["unknown"] == 1
    held = s.reserve_usage("work", key=KEY, task_id="T3", reserve=0.0, starts=_starts(),
                           check=_ok)
    assert _row(s, held)["gated"] == 1
    _foreign(s, held, pid=_dead_pid())
    spend = s.usage_spend(KEY, _starts())["day"]
    assert spend["unknown"] == 2 and spend["runs"] == 4

    b = make_orch(config(roles=SINGLE))
    assert b._record_account("cl1", RunResult("ok"), None, None)
    assert day_spend(b, "cl1")["unknown"] == 0
    b.orch.accounts["cl1"].limits = AccountLimits(budget_usd=UsdBudget(day=10.0))
    assert b._limit_report("cl1")["status"] == "ok"
    reserved = b._reserve("cl1", None)
    assert reserved is not None and _row(b.store, reserved)["gated"] == 1
    b._settle_open(reserved, 0.0, "actual")
    assert b._limit_report("cl1")["status"] == "ok"
    assert b._record_account("cl1", RunResult("network"), None, None)  # a retried attempt
    rows = [dict(r) for r in b.store.usage_db.execute(
        "SELECT gated, cost_usd FROM account_usage WHERE key=? ORDER BY id",
        (b._usage_key("cl1"),))]
    assert rows == [{"gated": 0, "cost_usd": None}, {"gated": 1, "cost_usd": 0.0},
                    {"gated": 1, "cost_usd": None}]
    report = b._limit_report("cl1")
    assert report["status"] == "limited" and "unknown cost" in report["reason"]


# @pt.id KIT-HARDEN-014-report-cache
# @pt.item Limit reports are reused for 2 s; the start gate always reads the ledger
# @pt.condition cl1 has a budget; usage_spend and reserve_usage are counted; _limit_report is called repeatedly, with another reserve, after the window and after a ledger write; _reserve is called twice
# @pt.expected Two calls in the window read the ledger once and return equal, independent copies; a new reserve, an aged entry or a write by this process reads again; each _reserve reads it
# @pt.target bridge.service.Bridge._limit_report, Bridge._reserve
# @pt.method integration
# @pt.legacy every routing check and listing re-read the ledger and ran ps
def test_limit_report_cached(make_orch, fake_claude, monkeypatch):
    import bridge.service as service_module
    assert service_module.LIMIT_REPORT_TTL_S == 2.0
    b = make_orch(config(cl1=BUDGET, roles=SINGLE))
    spends, reserves = [], []
    real_spend, real_reserve = b.store.usage_spend, b.store.reserve_usage
    monkeypatch.setattr(b.store, "usage_spend",
                        lambda *a, **k: spends.append(a) or real_spend(*a, **k))
    monkeypatch.setattr(b.store, "reserve_usage",
                        lambda *a, **k: reserves.append(a) or real_reserve(*a, **k))
    first = b._limit_report("cl1")
    second = b._limit_report("cl1")
    assert len(spends) == 1 and first == second and first["status"] == "ok"
    second["status"] = "changed by a caller"
    assert b._limit_report("cl1")["status"] == "ok" and len(spends) == 1
    b._limit_report("cl1", reserve=0.5)
    assert len(spends) == 2
    stamp, revision, report = b._reports[("cl1", 0.0)]
    b._reports[("cl1", 0.0)] = (stamp - 3.0, revision, report)
    b._limit_report("cl1")
    assert len(spends) == 3

    ids = [b._reserve("cl1", None) for _ in range(2)]
    assert len(reserves) == 2 and len(spends) == 3
    for usage_id in ids:
        b._settle_open(usage_id, 0.0, "actual")
    b._limit_report("cl1")  # this process wrote the ledger: read again
    assert len(spends) == 4


# @pt.id KIT-HARDEN-015-switch-count
# @pt.item max_switches counts every switch of the task
# @pt.condition Fallback enabled with max_switches 1; the task moved claude1 -> claude2, then 600 other events are logged for it and cl2 goes over its limit
# @pt.expected count_events finds the switch; auto_switch_skipped 'switch limit reached (1/1)'; the resume is refused; the task stays on claude2 and nothing else ran
# @pt.target bridge.service.Bridge._quota_fallback, bridge.state.Store.count_events
# @pt.method integration
# @pt.legacy only the last 500 events were scanned, so older switches fell out of the cap
def test_switch_cap_counts_all_events(make_orch, fake_claude, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE_WRITE", raising=False)
    b = make_orch(failover.config(cl1=failover.FIVE_HOUR, cl2=failover.FIVE_HOUR,
                                  fallback="{enabled: true, max_switches: 1}"))
    failover.seed(b, "cl1")
    t = failover.task(b, agent="claude1")
    assert run(b.execute_task(t, wait=True))["agent"] == "claude2"
    for _ in range(600):
        b.store.log(t, Actor.BRIDGE, "filler")
    assert b.store.count_events(t, "account_auto_switched") == 1
    assert b.store.count_events(t, "filler") == 600
    failover.seed(b, "cl2")
    with pytest.raises(ValueError, match="account cl2 over its configured limit"):
        run(b.resume_task(t, wait=True))
    skipped, = events(b, t, "auto_switch_skipped")
    assert skipped["detail"] == {"account": "cl2", "reason": "switch limit reached (1/1)"}
    assert b.store.count_events(t, "account_auto_switched") == 1
    assert b.store.get(t)["agent"] == "claude2"
    assert failover.homes(fake_claude) == ["cl2"]


# @pt.id KIT-HARDEN-016-token-counts
# @pt.item Malformed token counts are ignored by the cost estimate
# @pt.condition Pricing 1 USD/Mtok input, 2 USD/Mtok output; usage with negative, NaN and infinite counts next to one valid count
# @pt.expected Only the valid count is priced; all-invalid counts estimate 0.0; run_cost reports the same estimate
# @pt.target bridge.quota.estimate_cost, run_cost
# @pt.method integration
# @pt.legacy negative or non-finite counts were priced (negative or NaN cost)
def test_estimate_ignores_bad_token_counts():
    pricing = TokenPricing(input_per_mtok=1.0, output_per_mtok=2.0)
    usage = {"input_tokens": -1_000_000, "output_tokens": math.nan,
             "cache_read_input_tokens": math.inf, "cache_creation_input_tokens": 1_000_000}
    assert quota.estimate_cost(usage, pricing) == pytest.approx(1.0)
    bad = {"input_tokens": -math.inf, "output_tokens": -5, "cache_read_input_tokens": math.nan}
    assert quota.estimate_cost(bad, pricing) == 0.0
    cost, source = quota.run_cost(None, usage, pricing)
    assert cost == pytest.approx(1.0) and source == "estimated"


# @pt.id KIT-HARDEN-017-report-read-only
# @pt.item usage_report writes nothing
# @pt.condition cl1 has cooldown_s 600 and a budget; it was marked QUOTA 610 s ago with no readable reset; its ledger holds a reservation of a dead process
# @pt.expected usage_report shows state quota and the reservation as reserved, and leaves the mark, the reservation, the events and the ledger revision unchanged; the writing paths still lift the mark and settle the reservation
# @pt.target bridge.service.Bridge.usage_report, bridge.state.Store.usage_spend
# @pt.method integration
# @pt.legacy usage_report lifted QUOTA marks and expired reservations
def test_usage_report_is_read_only(make_orch, fake_claude):
    b = make_orch(config(cl1="{cooldown_s: 600, budget_usd: {day: 10.0}, reserve_usd: 0.5}",
                         roles=SINGLE))
    failover.mark_quota(b, "cl1", age_s=610)
    held = b.store.reserve_usage("cl1", key=b._usage_key("cl1"), task_id=None, reserve=0.5,
                                 starts=_starts(), check=_ok)
    _foreign(b.store, held, pid=_dead_pid())

    def snapshot() -> tuple:
        return (b.store.db.execute("SELECT MAX(id) FROM events").fetchone()[0],
                b.store.usage_revision, b.store.account_state("cl1", key=b._key("cl1")),
                _row(b.store, held))

    before = snapshot()
    entry, = b.usage_report("cl1")["accounts"]
    assert entry["state"]["state"] == "quota"
    assert entry["limits"]["spend"]["day"]["reserved"] == 0.5
    assert snapshot() == before
    assert _row(b.store, held)["state"] == "reserved"

    assert failover.state(b, "cl1") == "available"
    b._limit_report("cl1")
    assert _row(b.store, held)["state"] == "settled"


# @pt.id KIT-HARDEN-018-numeric
# @pt.item One public numeric helper for quota values
# @pt.condition quota.numeric on numbers, a bool and a string; cl1 has cooldown_s 600 and a snapshot whose resets_at is a bool; it was marked QUOTA 610 s ago
# @pt.expected numeric accepts int and float only; _account_state uses it, so the bool reset is unreadable and the cooldown lifts the mark
# @pt.target bridge.quota.numeric, bridge.service.Bridge._account_state
# @pt.method integration
# @pt.legacy _account_state repeated the isinstance checks by hand
def test_numeric_shared_helper(make_orch, fake_claude):
    assert not hasattr(quota, "_numeric")
    assert quota.numeric(1) and quota.numeric(2.5)
    assert not quota.numeric(True) and not quota.numeric("1") and not quota.numeric(None)
    assert "(int, float)" not in inspect.getsource(Bridge._account_state)
    b = make_orch(config(cl1="{cooldown_s: 600}", roles=SINGLE))
    b.store.save_quota("cl1", {"primary": {"used_percent": 50, "window_minutes": 300,
                                           "resets_at": True}}, key=b._key("cl1"))
    failover.mark_quota(b, "cl1", age_s=610)
    assert failover.state(b, "cl1") == "available"
    assert failover.cooldown_events(b) == [{"name": "cl1", "cooldown_s": 600}]


# @pt.id KIT-HARDEN-019-no-run-state
# @pt.item A run that fails in setup settles its reservation itself
# @pt.condition cl1 has a budget with reserve_usd 0.5; creating the attempt log path raises before adapter.run
# @pt.expected _run_inner takes no run_state; the reservation is settled 0.0 actual (the finally's unknown-cost settle is a no-op), the task FAILED, the agent never ran and the budget has no unknown cost
# @pt.target bridge.service.Bridge._run, Bridge._run_inner
# @pt.method integration
# @pt.legacy _run tracked adapter_called through a run_state out-parameter
def test_setup_failure_without_run_state(make_orch, fake_claude, monkeypatch):
    import bridge.service as service_module
    assert "run_state" not in inspect.signature(Bridge._run_inner).parameters
    assert "run_state" not in inspect.getsource(Bridge._run)
    b = make_orch(config(cl1=BUDGET, roles=SINGLE))

    def broken(*args, **kwargs):
        raise RuntimeError("no log directory")

    monkeypatch.setattr(service_module, "_attempt_log_path", broken)
    t = task(b, "Set x to 2")
    assert run(b.execute_task(t, wait=True))["state"] == "FAILED"
    row, = _task_rows(b, t)
    assert (row["state"], row["cost_usd"], row["source"]) == ("settled", 0.0, "actual")
    assert log(fake_claude) == []
    assert day_spend(b, "cl1")["unknown"] == 0
    assert os.getpid() == row["owner_pid"]


# @pt.id KIT-AUDIT-CFG-04
# @pt.item config-check applies the account home isolation check
# @pt.condition an account home inside the project, the global ~/.claude, and a relative path
# @pt.expected each config gets ok false with an error naming the account, and the CLI exits 1
# @pt.target bridge.cli._run, bridge.cli.main
# @pt.method integration
# @pt.legacy audit CFG-04: config-check said ok and routing failed later
def test_config_check_rejects_shared_homes(tmp_path, monkeypatch):
    import os
    root = tmp_path / "proj"
    (root / ".ai").mkdir(parents=True)
    monkeypatch.setattr(cli, "find_root", lambda *a, **k: root)
    target = root / ".ai" / "orchestrator.yaml"
    for home in (root / "homes" / "c1", Path(os.environ["HOME"]) / ".claude", "homes/c1"):
        target.write_text("version: 1\naccounts:\n"
                          f"  c1: {{provider: anthropic, home: '{home}'}}\n"
                          "agents:\n  cl: {adapter: claude-cli, account: c1, allow_write: true}\n"
                          "roles: {executor: cl}\nworkflow: {stages: [execute, test]}\n")
        out = asyncio.run(cli._run(argparse.Namespace(cmd="config-check")))
        assert out["ok"] is False and len(out["errors"]) == 1, home
        assert out["errors"][0].startswith("account c1: home must be"), out["errors"]
    monkeypatch.setattr(sys, "argv", ["agent-bridge", "config-check"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 1
