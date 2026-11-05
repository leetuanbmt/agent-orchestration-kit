"""An attempt that ends early cannot leave out-of-scope changes behind (QA-BUG-009)."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bridge.config import BridgeConfig
from bridge.models import State, TransitionError
from bridge.service import Bridge
from tests.conftest import run, spec
from tests.integration.test_evidence_window import gates_only


def writes_then(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    """A codex that writes `path` itself before the fake runs its scenario (quota, crash...)."""
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    fakes = Path(os.environ["PATH"].split(os.pathsep)[0])
    exe = wrap / "codex"
    exe.write_text(f'#!/bin/sh\n[ -n "$WRITE_FIRST" ] && mkdir -p "$(dirname {path})" && '
                   f'echo x > {path}\nexec {fakes / "codex"} "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrap}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("WRITE_FIRST", "1")


# @pt.id KIT-SEC-LEFT-001-quota
# @pt.item An attempt that writes out of scope and then hits its quota
# @pt.condition gates-only workflow; the agent writes OUTSIDE.txt and the run ends with a usage limit
# @pt.expected the task is BLOCKED with SCOPE naming the file instead of PAUSED; resume is refused, so the file can never become the next attempt's baseline
# @pt.target TaskRunner._run_inner, bridge.models.leftover_failure
# @pt.method integration
# @pt.legacy QA-BUG-009: the task paused, and after account-clear and resume it closed DONE with OUTSIDE.txt in place and no violation
def test_quota_after_outside_write_blocks(repo, fake_codex, tmp_path, monkeypatch):
    writes_then(tmp_path, monkeypatch, "OUTSIDE.txt")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "quota,ok")
    b = gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.BLOCKED
    assert out["failure"].startswith("SCOPE: an interrupted attempt left changes outside the task")
    assert "outside allowed paths: OUTSIDE.txt" in out["failure"]
    b.clear_account("default")
    with pytest.raises(TransitionError):
        run(b.resume_task(t, wait=True))


# @pt.id KIT-SEC-LEFT-002-crash-protected
# @pt.item A crash after touching a protected file
# @pt.condition the agent rewrites .ai/config.json and crashes
# @pt.expected BLOCKED with the protected violation, not FAILED
# @pt.target TaskRunner._run_inner
# @pt.method integration
# @pt.legacy the crash was retried on top of the tampered config
def test_crash_after_protected_write_blocks(make_bridge, repo, tmp_path, monkeypatch):
    writes_then(tmp_path, monkeypatch, ".ai/config.json")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "crash")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.BLOCKED and "protected file changed: .ai/config.json" in out["failure"]


# @pt.id KIT-SEC-LEFT-003-in-scope
# @pt.item An early end with only in-scope changes still pauses
# @pt.condition the agent writes src/partial.py (allowed) and hits its quota
# @pt.expected PAUSED with QUOTA and the attempt given back, as before
# @pt.target TaskRunner._run_inner
# @pt.method integration
# @pt.legacy none
def test_quota_after_in_scope_write_pauses(make_bridge, tmp_path, monkeypatch):
    writes_then(tmp_path, monkeypatch, "src/partial.py")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "quota")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.PAUSED and out["failure"].startswith("QUOTA") and out["attempts"] == 0


# @pt.id KIT-SEC-LEFT-004-recover
# @pt.item A runner killed after its agent wrote out of scope
# @pt.condition a runner process starts a task whose agent writes OUTSIDE.txt and then hangs; the runner and the agent are killed; a new bridge recovers
# @pt.expected recover diffs against the attempt's stored baseline and BLOCKS the task with SCOPE instead of pausing it
# @pt.target Bridge.recover, Bridge._leftover, Store.set_baseline
# @pt.method integration
# @pt.legacy recover paused the task and a resume took OUTSIDE.txt as its baseline
def test_recover_blocks_leftover(repo, fake_codex, tmp_path, monkeypatch):
    writes_then(tmp_path, monkeypatch, "OUTSIDE.txt")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "hang")
    locks = str(fake_codex / "locks")
    b = Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=locks))
    t = run(b.create_task(spec()))["task_id"]
    runner = subprocess.Popen([sys.executable, "-c", (
        "import asyncio, sys; from bridge.config import BridgeConfig; from bridge.service import Bridge; "
        f"b = Bridge(sys.argv[1], BridgeConfig(retry_backoff_s=0, lock_dir={locks!r})); "
        "asyncio.run(b.execute_task(sys.argv[2], wait=True))"), str(repo), t],
        cwd=Path(__file__).resolve().parents[2], start_new_session=True)
    agent = fake_codex / "self.pid"
    deadline = time.monotonic() + 30
    while not agent.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert (repo / "OUTSIDE.txt").exists() and b.store.attempts(t)[-1]["baseline"]
    os.killpg(runner.pid, signal.SIGKILL)  # the runner and, in its group, nothing else
    runner.wait(10)
    fresh = Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=locks))
    assert t in fresh.recover()
    row = fresh.store.get(t)
    assert row["state"] == State.BLOCKED and "outside allowed paths: OUTSIDE.txt" in row["failure"]
