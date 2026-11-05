"""Regression tests for runs that are stopped from outside: a signal to the CLI runner, a cancel
from another process, an agent that gives up without changes, and the default executor prompt."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bridge.accounts import AccountLock
from bridge.executor import build_prompt
from bridge.models import State
from tests.conftest import run, spec


def _cli_runner(repo: Path, fake_codex: Path, *args: str) -> subprocess.Popen:
    """`python -m bridge <args>` in its own process, sharing the test's lock dir and HOME."""
    path = repo / ".ai" / "config.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    config["lock_dir"] = str(fake_codex.parent / "locks")
    path.write_text(json.dumps(config))
    env = {**os.environ, "BRIDGE_PROJECT_ROOT": str(repo)}
    return subprocess.Popen([sys.executable, "-m", "bridge", *args], env=env,
                            cwd=Path(__file__).parents[2], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _wait_for(check, timeout: float = 30) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _cleanup(runner: subprocess.Popen, pids: list[int]) -> None:
    """Kill only what this test started, so a failing run leaves no orphans behind."""
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
    if runner.poll() is None:
        runner.kill()
    runner.wait(timeout=10)


# @pt.id KIT-INT-040-signal-stops-cli-runner
# @pt.item A signal to a foreground `execute` stops its agent and parks the task
# @pt.condition codex hangs; the CLI runner gets SIGTERM or SIGALRM (a caller's timeout)
# @pt.expected the codex process group is gone, the task is PAUSED/INTERRUPTED without any
#              recover() call, the attempt budget is given back, the runner exits 128+signal,
#              and a later resume reaches REVIEW
# @pt.target bridge.cli._run, bridge.service.Bridge._run_inner
# @pt.method integration
# @pt.legacy none (new kit)
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGALRM])
def test_signal_to_cli_runner_kills_agent_and_parks_task(make_bridge, fake_codex, repo,
                                                         monkeypatch, sig) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "hang,ok")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    runner = _cli_runner(repo, fake_codex, "execute", t)
    pids: list[int] = []
    try:
        assert _wait_for(lambda: (fake_codex / "child.pid").exists()), "fake codex never hung"
        pids = [int((fake_codex / "self.pid").read_text())]
        child = int((fake_codex / "child.pid").read_text())
        runner.send_signal(sig)
        assert runner.wait(timeout=30) == 128 + sig
        assert _wait_for(lambda: _gone(pids[0]) and _gone(child), 5)
        row = b.store.get(t)
        assert row["state"] == State.PAUSED and row["failure"].startswith("INTERRUPTED"), row
        assert row["attempts"] == 0 and row["session_id"]
    finally:
        _cleanup(runner, pids)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    assert run(b.resume_task(t, wait=True))["state"] == State.REVIEW


# @pt.id KIT-INT-041-cancel-stops-other-process
# @pt.item Cancel from another process stops that process's verification and frees the account
# @pt.condition a CLI `execute` is running a long verification command; a second bridge cancels
# @pt.expected cancel returns CANCELLED only after the runner let go: the verification process is
#              dead, the account lock can be taken, the remaining checks never ran
# @pt.target bridge.service.Bridge.cancel_task, bridge.review.run_verification
# @pt.method integration
# @pt.legacy none (new kit)
def test_cancel_stops_verification_in_another_process(make_bridge, fake_codex, repo,
                                                      monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    pidfile, second = fake_codex / "verify.pid", fake_codex / "second.ran"
    slow = (f"python3 -c \"import os,pathlib,time; pathlib.Path('{pidfile}')"
            f".write_text(str(os.getpid())); time.sleep(120)\"")
    after = f"python3 -c \"import pathlib; pathlib.Path('{second}').write_text('x')\""
    b = make_bridge()
    t = run(b.create_task(spec(verification_commands=[slow, after])))["task_id"]
    runner = _cli_runner(repo, fake_codex, "execute", t)
    pids: list[int] = []
    try:
        assert _wait_for(lambda: pidfile.exists() and pidfile.read_text()), "check never started"
        pids = [int(pidfile.read_text())]
        assert b.store.get(t)["state"] == State.VERIFYING
        out = run(make_bridge().cancel_task(t, "stop it"))
        assert out["state"] == State.CANCELLED
        assert _gone(pids[0])
        lock = AccountLock(b._account(b.orch.agent(b._agent_of(b.store.get(t))).account),
                           str(fake_codex.parent / "locks"))
        lock.acquire()  # raises ConflictError while the runner still holds the account
        lock.release()
        assert runner.wait(timeout=15) == 0
        assert not second.exists()
        ver = b.store.attempts(t)[-1]["verification"]
        assert not ver["passed"] and ver["interrupted"] and ver["checks"][0]["error"] == "cancelled"
    finally:
        _cleanup(runner, pids)


# @pt.id KIT-INT-042-blocked-without-changes-skips-verification
# @pt.item No verification run when the agent gave up and changed nothing
# @pt.condition codex returns status "blocked"; with and without a file change
# @pt.expected no change: checks skipped with a recorded reason, task goes to REWORK;
#              with a change: the checks still run
# @pt.target bridge.service.Bridge._run_inner
# @pt.method integration
# @pt.legacy none (new kit)
@pytest.mark.parametrize("write", ["", "src/app.py=x = 3\n"])
def test_blocked_without_changes_skips_verification(make_bridge, fake_codex, monkeypatch,
                                                    write) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "blocked")
    monkeypatch.setenv("FAKE_CODEX_WRITE", write)
    marker = fake_codex / "check.ran"
    check = f"python3 -c \"import pathlib; pathlib.Path('{marker}').write_text('x')\""
    b = make_bridge()
    t = run(b.create_task(spec(verification_commands=[check])))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REWORK
    ver = b.store.attempts(t)[-1]["verification"]
    assert ver["agent_status"] == "blocked" and not ver["passed"]
    if write:
        assert marker.exists() and len(ver["checks"]) == 1 and "skipped" not in ver
    else:
        assert not marker.exists() and ver["checks"] == [] and "blocked" in ver["skipped"]


# @pt.id KIT-INT-043-ignored-build-output-in-scope
# @pt.item Git-ignored build output does not count as an out-of-scope change
# @pt.condition codex writes build/out.bin (git-ignored, outside allowed_paths) and src/app.py
# @pt.expected REVIEW; build/out.bin is not in the recorded changes
# @pt.target bridge.review.snapshot, bridge.review.scope_violations
# @pt.method integration
# @pt.legacy none (new kit)
def test_ignored_build_output_is_not_a_scope_violation(make_bridge, fake_codex, repo,
                                                        monkeypatch) -> None:
    with (repo / ".gitignore").open("a") as f:
        f.write("build/\n")
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qam", "ignore build"], check=True)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;build/out.bin=binary")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REVIEW
    assert b.store.attempts(t)[-1]["changes"]["files"] == ["src/app.py"]


# @pt.id KIT-UNIT-044-prompt-blocked-wording
# @pt.item The executor prompt reports "blocked" only after the in-scope work is done
# @pt.condition default executor prompt
# @pt.expected no "stop and return blocked" instruction; the prompt says to finish every in-scope
#              step first, keep partial work, list out-of-scope items last, and to follow the
#              Context section on how to handle blockers
# @pt.target bridge.executor.build_prompt
# @pt.method unit
# @pt.legacy none (new kit)
def test_prompt_reports_blocked_only_after_in_scope_work() -> None:
    p = build_prompt("T0001", {**spec(), "context": "Finish every other step first."}, [])
    assert "stop and return" not in p
    assert "every in-scope step" in p and "keep" in p and "last" in p
    assert "Context section" in p
