"""Reviewer/planner agent processes and interrupted attempts are recovered (F-04, F-02)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import bridge.runner as runner_module
from bridge.executor import RunResult
from bridge.models import Actor, State
from bridge.review import snapshot
from bridge.security import process_started
from tests.conftest import run, spec

DEAD_STARTED = "Mon Jan  1 00:00:00 2001"
ORCH = """
version: 1
accounts:
  cx1: {provider: openai, home: {home}/cx1}
agents:
  codex: {adapter: codex-cli, account: cx1}
  reviewer: {adapter: codex-cli, account: cx1}
roles: {executor: codex, reviewer: reviewer}
workflow: {stages: [execute, test, review]}
"""


def set_state(b, tid: str, state: str) -> None:
    with b.store.tx() as c:
        c.execute("UPDATE tasks SET state=? WHERE id=?", (state, tid))


def child() -> subprocess.Popen:
    """A live agent-like process in its own group, reaped in the background once it dies."""
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    threading.Thread(target=p.wait, daemon=True).start()
    return p


def review_task(b, p: subprocess.Popen, runner_pid: int, runner_started: str) -> str:
    t = run(b.create_task(spec()))["task_id"]
    set_state(b, t, State.REVIEW)
    b.store.update(t, codex_pid=p.pid, codex_started=process_started(p.pid),
                   runner_pid=runner_pid, runner_started=runner_started)
    return t


# @pt.id KIT-REL-CONSULT-001-record-and-clear
# @pt.item A reviewer consult is tracked on its task
# @pt.condition Bridge._consult runs a reviewer whose adapter reports a pid through on_start, as the review stage passes it
# @pt.expected while it runs the task holds codex_pid and this process as runner_pid; after it ends all four fields are cleared
# @pt.target Bridge._consult, TaskRunner._review_stage
# @pt.method integration
# @pt.legacy the reviewer child had no recorded pid, so recovery could not find it
def test_review_consult_records_and_clears_pid(make_orch, monkeypatch):
    import os

    b = make_orch(ORCH)
    t = run(b.create_task(spec()))["task_id"]
    set_state(b, t, State.REVIEW)
    seen = {}

    class Fake:
        def available(self):
            return "/usr/bin/true"

        async def run(self, req, should_cancel=None, on_start=None):
            on_start(os.getpid())
            seen.update(b.store.get(t))
            return RunResult("ok", final={"decision": "approve", "notes": "ok"})

    monkeypatch.setattr(b, "_adapter", lambda name: Fake())
    monkeypatch.setattr(b, "_pick", lambda role, need, *a, **k: "reviewer")
    monkeypatch.setattr(b.store, "attempts", lambda tid: [{}])
    monkeypatch.setattr(runner_module, "build_review_prompt", lambda *a, **k: "review")
    run(b.runner._review_stage(t))
    assert seen["codex_pid"] == os.getpid() and seen["runner_pid"] == os.getpid()
    assert seen["codex_started"] and seen["runner_started"]
    row = b.store.get(t)
    assert (row["codex_pid"], row["codex_started"], row["runner_pid"],
            row["runner_started"]) == (None, None, None, None)


# @pt.id KIT-REL-CONSULT-002-kill-orphan-reviewer
# @pt.item recover() kills a reviewer whose bridge died
# @pt.condition a REVIEW task records a dead runner and a live child process with its real start time
# @pt.expected the child is killed, the four fields are cleared, reviewer_orphan_killed is logged and the task stays in REVIEW
# @pt.target Bridge.recover
# @pt.method integration
# @pt.legacy the orphaned reviewer kept running and held its slot
def test_recover_kills_orphan_reviewer(make_bridge):
    b = make_bridge()
    p = child()
    try:
        t = review_task(b, p, 2**22 + 12345, DEAD_STARTED)
        make_bridge().recover()
        p.wait(timeout=10)
        row = b.store.get(t)
        assert row["state"] == State.REVIEW
        assert (row["codex_pid"], row["codex_started"], row["runner_pid"],
                row["runner_started"]) == (None, None, None, None)
        events = [e for e in b.store.events(t) if e["kind"] == "reviewer_orphan_killed"]
        assert events and events[-1]["detail"]["killed"] is True
    finally:
        p.kill()


# @pt.id KIT-REL-CONSULT-003-live-runner
# @pt.item recover() leaves a reviewer whose runner is alive
# @pt.condition a REVIEW task records a live runner process (matching start time) and a live child
# @pt.expected the child keeps running and the task fields are untouched
# @pt.target Bridge.recover
# @pt.method integration
# @pt.legacy none
def test_recover_leaves_live_runner_reviewer(make_bridge):
    b = make_bridge()
    p, owner = child(), child()
    try:
        t = review_task(b, p, owner.pid, process_started(owner.pid))
        make_bridge().recover()
        assert p.poll() is None
        row = b.store.get(t)
        assert row["codex_pid"] == p.pid and row["runner_pid"] == owner.pid
    finally:
        p.kill()
        owner.kill()


# @pt.id KIT-REL-CONSULT-004-kill-orphan-planner
# @pt.item recover() kills a planner whose bridge died
# @pt.condition a PLANNING workflow records a dead owner and a live agent child with its real start time
# @pt.expected the child is killed, the workflow is FAILED and the agent columns are cleared
# @pt.target Bridge.recover
# @pt.method integration
# @pt.legacy the planner child survived its dead bridge
def test_recover_kills_orphan_planner(make_bridge):
    b = make_bridge()
    p = child()
    try:
        wf = b.store.create_workflow("plan it", None, "PLANNING")
        with b.store.tx() as c:
            c.execute("UPDATE workflows SET runner_pid=?, runner_started=? WHERE id=?",
                      (2**22 + 12345, DEAD_STARTED, wf["id"]))
        b.store.set_workflow_agent(wf["id"], p.pid, process_started(p.pid))
        make_bridge().recover()
        p.wait(timeout=10)
        row = b.store.workflow(wf["id"])
        assert row["state"] == "FAILED"
        assert row["agent_pid"] is None and row["agent_started"] is None
    finally:
        p.kill()


# @pt.id KIT-REL-CONSULT-005-interrupted-changes
# @pt.item An interrupted attempt records what it changed
# @pt.condition a RUNNING task whose runner died after its attempt wrote migrations/001.sql
# @pt.expected the attempt stores changes.files with that file and the task needs_human
# @pt.target Bridge.recover, Bridge._leftover
# @pt.method integration
# @pt.legacy interrupted attempts stored no changes, so later approvals missed them
def test_interrupted_attempt_records_changes(make_bridge, repo):
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    b.store.transition(t, State.RUNNING, Actor.CLIENT, expect={State.READY},
                       runner_pid=2**22 + 12345, runner_started=DEAD_STARTED)
    b.store.start_attempt(t, 1, "execute")
    b.store.set_baseline(t, 1, snapshot(repo, b.cfg.protected_paths))
    (Path(repo) / "migrations").mkdir()
    (Path(repo) / "migrations" / "001.sql").write_text("drop table x;\n")
    make_bridge().recover()
    attempt = b.store.attempts(t)[0]
    assert attempt["outcome"] == "interrupted"
    assert "migrations/001.sql" in attempt["changes"]["files"]
    assert b.store.get(t)["needs_human"] == 1
