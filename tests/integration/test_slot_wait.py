"""A workflow waits, with backoff, for an account slot another bridge process holds."""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from bridge.accounts import AccountLock
from bridge.models import State
from tests.conftest import run
from tests.integration.test_orchestration import CONFIG_A, WRITE, events


def _setup(make_orch, fake_codex, monkeypatch, slot_wait_s):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    monkeypatch.setattr("bridge.service.SLOT_WAIT_FIRST_S", 0.2)
    monkeypatch.setattr("bridge.service.SLOT_WAIT_MAX_S", 0.2)
    b = make_orch(CONFIG_A + f"routing: {{slot_wait_s: {slot_wait_s}}}\n")
    wid = run(b.plan("Set x to 2", wait=True))["workflow_id"]
    # CONFIG_A's only executor runs on cx1: another bridge process holds its slot.
    held = AccountLock(b._account("cx1"), str(fake_codex / "locks"))
    held.acquire()
    return b, wid, held


# @pt.id KIT-FLOW-SLOT-WAIT-1
# @pt.item Workflow waits for a busy account slot
# @pt.condition The executor's account is held by another bridge and released after ~1 s
# @pt.expected run_workflow ends DONE without a second call; one slot_wait event, one attempt
# @pt.target Bridge._drive, RoutingConfig.slot_wait_s
# @pt.method integration
# @pt.legacy a busy slot ended the workflow WAITING at once
def test_slot_wait_then_run(make_orch, fake_codex, fake_claude, monkeypatch):
    b, wid, held = _setup(make_orch, fake_codex, monkeypatch, 60)
    timer = threading.Timer(1.0, held.release)
    timer.start()
    try:
        wf = run(b.run_workflow(wid, wait=True))
    finally:
        timer.join()
    task = wf["tasks"][0]
    assert wf["state"] == "DONE" and task["state"] == State.DONE
    wait, = events(b, task["task_id"], "slot_wait")
    assert wait["detail"]["workflow_id"] == wid and wait["detail"]["wait_s"] == 60
    assert "busy" in wait["detail"]["reason"]
    assert not events(b, task["task_id"], "slot_wait_expired")
    assert len(b.store.attempts(task["task_id"])) == 1


# @pt.id KIT-FLOW-SLOT-WAIT-2
# @pt.item slot_wait_s: 0 keeps the old behaviour
# @pt.condition The executor's account is held by another bridge; routing.slot_wait_s is 0
# @pt.expected The workflow ends WAITING with the busy reason in detail.waiting; no slot_wait event
# @pt.target Bridge._drive, RoutingConfig.slot_wait_s
# @pt.method integration
# @pt.legacy none
def test_slot_wait_disabled(make_orch, fake_codex, fake_claude, monkeypatch):
    b, wid, held = _setup(make_orch, fake_codex, monkeypatch, 0)
    try:
        wf = run(b.run_workflow(wid, wait=True))
    finally:
        held.release()
    tid = wf["tasks"][0]["task_id"]
    assert wf["state"] == "WAITING"
    assert "busy" in wf["detail"]["waiting"][tid]
    assert not events(b, tid, "slot_wait")
    assert b.store.attempts(tid) == []


# @pt.id KIT-FLOW-SLOT-WAIT-3
# @pt.item Slot wait expiry
# @pt.condition routing.slot_wait_s is 1 and the account is never released
# @pt.expected The workflow ends WAITING with a slot_wait_expired event in under 2.5 s; the task stays READY, 0 attempts
# @pt.target Bridge._drive
# @pt.method integration
# @pt.legacy a busy slot ended the workflow WAITING at once
def test_slot_wait_expires(make_orch, fake_codex, fake_claude, monkeypatch):
    b, wid, held = _setup(make_orch, fake_codex, monkeypatch, 1)
    started = time.monotonic()
    try:
        wf = run(b.run_workflow(wid, wait=True))
    finally:
        held.release()
    # The first retry is capped by the deadline too (audit FUN-05: it waited 5 s regardless).
    assert time.monotonic() - started < 2.5
    task = wf["tasks"][0]
    assert wf["state"] == "WAITING" and task["state"] == State.READY
    assert "busy" in wf["detail"]["waiting"][task["task_id"]]
    assert len(events(b, task["task_id"], "slot_wait")) == 1
    assert len(events(b, task["task_id"], "slot_wait_expired")) == 1
    assert b.store.attempts(task["task_id"]) == []


# @pt.id KIT-FLOW-SLOT-WAIT-4
# @pt.item Shutdown during a slot wait
# @pt.condition A background workflow waits for a busy account when the bridge shuts down
# @pt.expected The workflow lists waiting_for_slot while waiting; shutdown returns in under 3 s
# @pt.target Bridge._drive, Bridge.shutdown
# @pt.method integration
# @pt.legacy a busy slot ended the workflow WAITING at once
def test_slot_wait_shutdown(make_orch, fake_codex, fake_claude, monkeypatch):
    b, wid, held = _setup(make_orch, fake_codex, monkeypatch, 60)
    # Long backoff: shutdown must not wait for the next try.
    monkeypatch.setattr("bridge.service.SLOT_WAIT_FIRST_S", 30.0)

    async def scenario():
        wf = await b.run_workflow(wid)
        tid = wf["tasks"][0]["task_id"]
        await asyncio.sleep(0.5)
        wf = b._workflow(wid)
        assert wf["state"] == "RUNNING"
        slots = wf["detail"]["waiting_for_slot"]
        assert [s["task_id"] for s in slots] == [tid]
        assert "busy" in slots[0]["reason"] and slots[0]["until"] > time.time()
        start = time.monotonic()
        await b.shutdown()
        return time.monotonic() - start

    try:
        took = run(scenario())
    finally:
        held.release()
    assert took < 3
    assert b.store.workflow(wid)["state"] != "RUNNING"


# @pt.id KIT-FLOW-SLOT-WAIT-5
# @pt.item Cancel during a slot wait
# @pt.condition A background workflow waits for a busy account and its task is cancelled
# @pt.expected The wait ends: the workflow is no longer RUNNING and the task is CANCELLED
# @pt.target Bridge._drive, Bridge.cancel_task
# @pt.method integration
# @pt.legacy a busy slot ended the workflow WAITING at once
def test_slot_wait_cancel(make_orch, fake_codex, fake_claude, monkeypatch):
    b, wid, held = _setup(make_orch, fake_codex, monkeypatch, 60)
    monkeypatch.setattr("bridge.service.SLOT_WAIT_FIRST_S", 30.0)

    async def scenario():
        wf = await b.run_workflow(wid)
        tid = wf["tasks"][0]["task_id"]
        drive = b._workflows[wid]
        await asyncio.sleep(0.5)
        assert b._workflow(wid)["detail"]["waiting_for_slot"][0]["task_id"] == tid
        await b.cancel_task(tid, "test")
        await asyncio.wait_for(drive, 3)
        return tid

    try:
        tid = run(scenario())
    finally:
        held.release()
    wf = b._workflow(wid)
    assert wf["state"] != "RUNNING"
    assert b.store.get(tid)["state"] == State.CANCELLED
    assert "waiting_for_slot" not in (wf["detail"] or {})


def _cli_workflow_runner(repo: Path, fake_codex: Path, orch_text: str,
                         wid: str) -> subprocess.Popen:
    """`python -m bridge run-workflow <wid>` in its own process group, sharing the repo, HOME and
    the lock dir the test's bridge (and its held AccountLock) uses."""
    ai = repo / ".ai"
    ai.mkdir(exist_ok=True)
    (ai / "orchestrator.yaml").write_text(
        orch_text.replace("{home}", str(fake_codex.parent / "homes")))
    path = ai / "config.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    config.update(lock_dir=str(fake_codex / "locks"), retry_backoff_s=0)
    path.write_text(json.dumps(config))
    env = {**os.environ, "BRIDGE_PROJECT_ROOT": str(repo)}
    return subprocess.Popen([sys.executable, "-m", "bridge", "run-workflow", wid], env=env,
                            cwd=Path(__file__).parents[2], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def _wait_for(check, timeout: float = 30) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL the runner's whole process group (it has its own session) and reap it."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    proc.wait(timeout=10)


# @pt.id KIT-FLOW-SLOT-WAIT-6
# @pt.item A workflow killed while waiting for a slot is recovered
# @pt.condition A CLI run-workflow process waits for a busy account slot and is SIGKILLed
# @pt.expected recover() parks the workflow WAITING with an interrupted error and no
#              waiting_for_slot; the task stays READY with 0 attempts; a second run_workflow
#              ends DONE with exactly one attempt
# @pt.target Bridge.recover, Bridge._drive
# @pt.method integration
# @pt.legacy a busy slot ended the workflow WAITING at once
def test_slot_wait_crash_recovery(make_orch, fake_codex, fake_claude, repo, monkeypatch):
    orch_text = CONFIG_A + "routing: {slot_wait_s: 600}\n"
    b, wid, held = _setup(make_orch, fake_codex, monkeypatch, 600)
    runner = None
    try:
        runner = _cli_workflow_runner(repo, fake_codex, orch_text, wid)

        def waiting() -> bool:
            wf = b.store.workflow(wid)
            return (wf["runner_pid"] == runner.pid
                    and "waiting_for_slot" in (wf["detail"] or {}))

        assert _wait_for(waiting), b.store.workflow(wid)
        slots = b.store.workflow(wid)["detail"]["waiting_for_slot"]
        tid = b.store.workflow_tasks(wid)[0]["id"]
        assert [s["task_id"] for s in slots] == [tid]
        _kill(runner)
        tasks = b.store.workflow_tasks(wid)
        assert all(t["state"] != State.RUNNING for t in tasks)
        row = b.store.get(tid)
        assert row["state"] == State.READY and row["attempts"] == 0
        assert b.store.attempts(tid) == []

        assert b.recover() == [wid]
        wf = b.store.workflow(wid)
        detail = wf["detail"] or {}
        assert wf["state"] == "WAITING"
        assert "interrupted" in detail.get("error", "")
        assert "waiting_for_slot" not in detail

        held.release()
        wf = run(b.run_workflow(wid, wait=True))
        task = wf["tasks"][0]
        assert wf["state"] == "DONE" and task["state"] == State.DONE
        assert len(b.store.attempts(task["task_id"])) == 1
    finally:
        if runner is not None:
            _kill(runner)
        held.release()
