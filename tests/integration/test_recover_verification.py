"""A runner that dies during verification leaves no check running (audit REL-02, recovery)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from bridge.config import BridgeConfig
from bridge.models import State
from bridge.service import Bridge
from tests.conftest import run, spec


def running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"))


# @pt.id KIT-REL-RECOVER-001-verification
# @pt.item Recovery after the runner dies in the middle of verification
# @pt.condition a runner process starts a task whose check sleeps 60 s; the runner is SIGKILLed while VERIFYING; a new bridge starts and recovers
# @pt.expected while the check runs, the task records its pid; recover kills the check's group and parks the task PAUSED (INTERRUPTED)
# @pt.target bridge.review.run_verification(on_start), Bridge.recover
# @pt.method integration
# @pt.legacy the check kept running untracked after the runner died
def test_recover_kills_orphaned_verification(repo, fake_codex, tmp_path, monkeypatch):
    marker = tmp_path / "check.pid"
    check = f"python3 -c \"import os,time; open('{marker}','w').write(str(os.getpid())); time.sleep(60)\""
    locks = str(fake_codex / "locks")
    b = Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=locks))
    t = run(b.create_task(spec(verification_commands=[check])))["task_id"]
    runner = subprocess.Popen([sys.executable, "-c", (
        "import asyncio, sys; from bridge.config import BridgeConfig; from bridge.service import Bridge; "
        f"b = Bridge(sys.argv[1], BridgeConfig(retry_backoff_s=0, lock_dir={locks!r})); "
        "asyncio.run(b.execute_task(sys.argv[2], wait=True))"), str(repo), t],
        cwd=Path(__file__).resolve().parents[2], start_new_session=True)
    deadline = time.monotonic() + 30
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    pid = int(marker.read_text())
    row = b.store.get(t)
    assert row["state"] == State.VERIFYING and row["codex_pid"] == pid and row["codex_started"]
    os.kill(runner.pid, signal.SIGKILL)  # the runner only: the check is in its own group
    runner.wait(10)
    assert running(pid)
    fresh = Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=locks))
    assert t in fresh.recover()
    deadline = time.monotonic() + 10
    while running(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not running(pid)
    row = fresh.store.get(t)
    assert row["state"] == State.PAUSED and row["failure"].startswith("INTERRUPTED")
