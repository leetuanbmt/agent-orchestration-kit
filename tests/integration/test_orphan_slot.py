"""Account slots follow orphan agents and unlock explicitly on normal release."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bridge import osutil
from bridge.accounts import Account, AccountLock
from bridge.models import State
from bridge.state import ConflictError
from tests.conftest import run, spec

pytestmark = pytest.mark.skipif(osutil.WINDOWS, reason="POSIX descriptor inheritance and flock")


def wait_for(predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail("condition did not become true within 30 seconds")


def kill_group(pid):
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


# @pt.id T0117-orphan-slot
# @pt.item Account slot survives runner death
# @pt.condition A runner is SIGKILLed while its fake codex hangs on a single-slot account
# @pt.expected Slot remains busy until the orphan agent group dies
# @pt.target AccountLock, Runner, Adapter.run
# @pt.method integration
# @pt.legacy Runner death freed the slot while its agent remained alive
def test_orphan_holds_slot(make_bridge, fake_codex, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "hang")
    b = make_bridge()
    assert b._slots("default") == 1
    tid = run(b.create_task(spec()))["task_id"]
    config_path = repo / ".ai" / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    config["lock_dir"] = b.cfg.lock_dir
    config_path.write_text(json.dumps(config))
    probe = AccountLock(b._account("default"), b.cfg.lock_dir, 1)
    runner = subprocess.Popen(
        [sys.executable, "-m", "bridge", "execute", tid],
        cwd=Path(__file__).parents[2], env={**os.environ, "BRIDGE_PROJECT_ROOT": str(repo)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    agent_pid = None
    try:
        wait_for(lambda: (fake_codex / "self.pid").exists())
        agent_pid = int((fake_codex / "self.pid").read_text())
        runner.kill()
        runner.wait(timeout=10)
        os.kill(agent_pid, 0)
        assert probe.free() is False
        with pytest.raises(ConflictError):
            probe.acquire()
        kill_group(agent_pid)
        wait_for(probe.free)
        assert probe.free() is True
    finally:
        if runner.poll() is None:
            runner.kill()
        runner.wait(timeout=10)
        if agent_pid is None and (fake_codex / "self.pid").exists():
            agent_pid = int((fake_codex / "self.pid").read_text())
        if agent_pid is not None:
            kill_group(agent_pid)
        probe.release()


# @pt.id T0117-detached-slot
# @pt.item Normal completion releases an inherited slot
# @pt.condition Fake codex completes after leaving a setsid child with inherited descriptors
# @pt.expected Run reaches REVIEW and slot is free while the detached child remains alive
# @pt.target AccountLock.release, Runner, Adapter.run
# @pt.method integration
# @pt.legacy Closing the runner descriptor alone would retain the inherited lock
def test_completed_run_unlocks_detached_child(make_bridge, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "detached")
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_bridge()
    tid = run(b.create_task(spec()))["task_id"]
    try:
        result = run(b.execute_task(tid, wait=True))
        assert result["state"] == State.REVIEW
        pid = int((fake_codex / "detached.pid").read_text())
        os.kill(pid, 0)
        assert os.getpgid(pid) == pid
        assert AccountLock(b._account("default"), b.cfg.lock_dir, 1).free() is True
    finally:
        if (fake_codex / "detached.pid").exists():
            kill_group(int((fake_codex / "detached.pid").read_text()))


# @pt.id T0117-explicit-unlock
# @pt.item Release unlocks a shared open file description
# @pt.condition A live sleep subprocess inherits the acquired lock descriptor
# @pt.expected Slot is busy before release and free after release while sleep still runs
# @pt.target AccountLock.fileno, AccountLock.release
# @pt.method unit
# @pt.legacy release only closed the runner descriptor
def test_release_unlocks_inherited_fd(tmp_path):
    lock = AccountLock(Account("default", None), str(tmp_path / "locks"))
    probe = AccountLock(Account("default", None), str(tmp_path / "locks"))
    assert lock.fileno() is None
    lock.acquire()
    child = None
    try:
        fd = lock.fileno()
        assert fd is not None
        child = subprocess.Popen(["sleep", "600"], pass_fds=(fd,))
        assert probe.free() is False
        lock.release()
        assert lock.fileno() is None
        assert child.poll() is None
        assert probe.free() is True
    finally:
        lock.release()
        if child is not None:
            child.kill()
            child.wait(timeout=10)
