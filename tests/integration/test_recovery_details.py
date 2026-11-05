"""Recovery details (audit REL-04, REL-05, REL-07)."""

from __future__ import annotations

import sqlite3
import subprocess

import pytest

import bridge.service as service_module
from bridge.models import Actor, State
from bridge.security import process_started
from bridge.state import ConflictError
from tests.conftest import run, spec


# @pt.id KIT-REL-REC-001-reused-pid
# @pt.item Recovery does not trust a bare pid
# @pt.condition a RUNNING task whose runner_pid belongs to a live process; its recorded start time differs from that process's, then matches it
# @pt.expected with a different start time the task is recovered (PAUSED, INTERRUPTED); with the matching start time it is left alone
# @pt.target Bridge.recover, Bridge._runner_alive
# @pt.method integration
# @pt.legacy audit REL-04: a reused pid kept a dead run's task RUNNING forever
def test_reused_pid_is_recovered(make_bridge):
    b = make_bridge()
    other = subprocess.Popen(["sleep", "30"])
    try:
        for started, recovered in (("Mon Jan  1 00:00:00 2001", True),
                                   (process_started(other.pid), False)):
            t = run(b.create_task(spec(objective=f"Run {started}")))["task_id"]
            b.store.transition(t, State.RUNNING, Actor.CLIENT, expect={State.READY},
                               runner_pid=other.pid, runner_started=started)
            assert (t in make_bridge().recover()) is recovered
            if not recovered:
                assert b.store.get(t)["state"] == State.RUNNING
            else:
                assert b.store.get(t)["failure"].startswith("INTERRUPTED")
    finally:
        other.kill()
        other.wait()


# @pt.id KIT-REL-REC-002-rescue-retry
# @pt.item A bridge error on a busy database still takes the task out of RUNNING
# @pt.condition the attempt setup raises; logging always fails and the first rescue transition fails with "database is locked"
# @pt.expected the rescue is retried and the task ends FAILED with BRIDGE_ERROR, not stuck in RUNNING
# @pt.target TaskRunner._rescue
# @pt.method integration
# @pt.legacy audit REL-05: the failing log call skipped the rescue and the task stayed RUNNING in a live server
def test_rescue_retries_on_busy_database(make_bridge, monkeypatch):
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]

    def broken(*args, **kwargs):
        raise RuntimeError("setup failed")

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    real, failures = b.store.transition, []

    def flaky(task_id, dst, *args, **kwargs):
        if dst == State.FAILED and not failures:
            failures.append(dst)
            raise sqlite3.OperationalError("database is locked")
        return real(task_id, dst, *args, **kwargs)

    monkeypatch.setattr(service_module, "snapshot", broken)
    monkeypatch.setattr(b.store, "log", locked)
    monkeypatch.setattr(b.store, "transition", flaky)
    run(b.execute_task(t, wait=True))
    row = b.store.get(t)
    assert failures and row["state"] == State.FAILED
    assert row["failure"].startswith("BRIDGE_ERROR")


# @pt.id KIT-REL-REC-003-one-active-task
# @pt.item One active task per worktree, read-only included
# @pt.condition a read-only task is RUNNING; a second read-only task and a writing task try to start
# @pt.expected both are refused with ConflictError naming the running task
# @pt.target Store.transition
# @pt.method integration
# @pt.legacy audit REL-07: only writer beside writer was refused
def test_one_active_task_per_worktree(make_bridge):
    b = make_bridge()
    first = run(b.create_task(spec(objective="Read", sandbox="read-only")))["task_id"]
    b.store.transition(first, State.RUNNING, Actor.CLIENT, expect={State.READY})
    for extra in ({"sandbox": "read-only"}, {}):
        t = run(b.create_task(spec(objective=f"Other {extra}", **extra)))["task_id"]
        with pytest.raises(ConflictError, match=f"task {first} is already running in this worktree"):
            # account_limit high enough that only the worktree rule can refuse
            b.store.transition(t, State.RUNNING, Actor.CLIENT, expect={State.READY},
                               account_limit=5)
