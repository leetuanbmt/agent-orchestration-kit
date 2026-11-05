"""Routing sees account slots held by other bridge processes (machine-wide slot locks)."""

import pytest

from bridge.accounts import AccountLock
from bridge.models import State
from bridge.state import ConflictError
from tests.conftest import run, spec
from tests.integration.test_accounts_routing import calls
from tests.integration.test_orchestration import CONFIG_C, WRITE, events


# @pt.id KIT-ACCT-204-route-skips-foreign-busy
# @pt.item Routing skips an account whose slots another bridge process holds
# @pt.condition Config C; the only cx1 slot is held through a separate AccountLock (as another bridge would)
# @pt.expected list_agents reports codex1 unavailable (busy in another bridge process); an unpinned task routes to codex2/cx2, runs with the cx2 home and reaches REVIEW or DONE
# @pt.target Bridge._unavailable, Bridge._route, AccountLock.free
# @pt.method integration
# @pt.legacy only this project's active tasks counted: the task bound to cx1 and failed with ConflictError
def test_route_skips_foreign_busy(make_orch, fake_codex, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", WRITE)
    b = make_orch(CONFIG_C)
    held = AccountLock(b._account("cx1"), b.cfg.lock_dir, 1)
    held.acquire()
    try:
        agents = {a["agent"]: a for a in b.list_agents()["agents"]}
        assert agents["codex1"]["available"] is False
        assert "busy in another bridge process" in agents["codex1"]["unavailable_reason"]
        tid = run(b.create_task(spec()))["task_id"]
        result = run(b.execute_task(tid, wait=True))
        assert result["state"] in {State.REVIEW, State.DONE}
        row = b.store.get(tid)
        assert row["agent"] == "codex2" and row["account"] == "cx2"
        routed, = events(b, tid, "agent_routed")
        assert routed["detail"]["agent"] == "codex2"
        assert calls(fake_codex)[-1]["codex_home"].endswith("/cx2")
    finally:
        held.release()


# @pt.id KIT-ACCT-205-unbind-on-lost-race
# @pt.item A fresh routing is undone when the slot is lost between the check and the lock
# @pt.condition Config C; AccountLock.free always reports a free slot while cx1 and cx2 are both held by other AccountLock instances
# @pt.expected execute_task raises ConflictError; the unpinned task is unbound (agent/account None), still READY with 0 attempts, with one agent_unrouted event; a task pinned to codex1 keeps its agent and gets no agent_unrouted event
# @pt.target Bridge.execute_task, Bridge._route, Bridge._unroute
# @pt.method integration
# @pt.legacy the task stayed bound to the busy account after the ConflictError
def test_unbind_on_lost_race(make_orch, fake_codex, fake_claude, monkeypatch):
    b = make_orch(CONFIG_C)
    monkeypatch.setattr(AccountLock, "free", lambda self: True)
    held = [AccountLock(b._account(name), b.cfg.lock_dir, 1) for name in ("cx1", "cx2")]
    for lock in held:
        lock.acquire()
    try:
        tid = run(b.create_task(spec()))["task_id"]
        with pytest.raises(ConflictError, match="busy in another bridge process"):
            run(b.execute_task(tid, wait=True))
        row = b.store.get(tid)
        assert row["agent"] is None and row["account"] is None
        assert row["attempts"] == 0 and row["state"] == State.READY
        routed, = events(b, tid, "agent_routed")
        unrouted, = events(b, tid, "agent_unrouted")
        assert unrouted["detail"]["agent"] == routed["detail"]["agent"] == "codex1"
        assert unrouted["detail"]["account"] == routed["detail"]["account"] == "cx1"
        assert "busy in another bridge process" in unrouted["detail"]["reason"]

        pinned = run(b.create_task(spec(agent="codex1", objective="Pinned")))["task_id"]
        with pytest.raises(ConflictError, match="busy in another bridge process"):
            run(b.execute_task(pinned, wait=True))
        row = b.store.get(pinned)
        assert row["agent"] == "codex1" and row["account"] == "cx1"
        assert row["attempts"] == 0 and row["state"] == State.READY
        assert not events(b, pinned, "agent_unrouted")
        assert not calls(fake_codex)
    finally:
        for lock in held:
            lock.release()


# @pt.id KIT-ACCT-209-free-probe
# @pt.item AccountLock.free probes slots without holding or deleting anything
# @pt.condition missing lock directory; free slot; slot held by another instance in this process; after release; instance holding its own slot
# @pt.expected True without creating the directory; True; False; True again; the lock file is never deleted
# @pt.target AccountLock.free
# @pt.method unit
# @pt.legacy none
def test_free_probe(make_orch, fake_codex, fake_claude):
    b = make_orch(CONFIG_C)
    account = b._account("cx1")
    missing = fake_codex / "nolocks" / "deeper"
    assert AccountLock(account, str(missing), 1).free() is True
    assert not missing.exists() and not missing.parent.exists()

    lock_dir = fake_codex / "probe-locks"
    lock_dir.mkdir(parents=True)
    probe = AccountLock(account, str(lock_dir), 1)
    assert probe.free() is True
    holder = AccountLock(account, str(lock_dir), 1)
    holder.acquire()
    try:
        assert probe.free() is False
        assert holder.free() is False  # its own slot counts as held
        assert probe.path.exists()
    finally:
        holder.release()
    assert probe.free() is True
    assert probe.path.exists()
    assert probe._file is None

    two = AccountLock(account, str(lock_dir), 2)
    holder.acquire()
    try:
        assert two.free() is True  # the second slot is still open
        other = AccountLock(account, str(lock_dir), 2)
        other.acquire()
        try:
            assert two.free() is False
        finally:
            other.release()
    finally:
        holder.release()
