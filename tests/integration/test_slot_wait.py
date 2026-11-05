"""A workflow waits, with backoff, for an account slot another bridge process holds."""

import asyncio
import threading
import time

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
# @pt.expected The workflow ends WAITING with a slot_wait_expired event; the task stays READY, 0 attempts
# @pt.target Bridge._drive
# @pt.method integration
# @pt.legacy a busy slot ended the workflow WAITING at once
def test_slot_wait_expires(make_orch, fake_codex, fake_claude, monkeypatch):
    b, wid, held = _setup(make_orch, fake_codex, monkeypatch, 1)
    try:
        wf = run(b.run_workflow(wid, wait=True))
    finally:
        held.release()
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
