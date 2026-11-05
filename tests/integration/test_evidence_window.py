"""Everything that runs in the worktree during an attempt is covered by the scope check (audit
SEC-01 fixes 1-2 and SEC-04): the verification step, and processes the agent or a check leaves."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from bridge.config import BridgeConfig
from bridge.models import State
from bridge.orchestrator import parse
from bridge.review import diff, recheck, snapshot
from bridge.service import Bridge
from tests.conftest import run, spec

GATES_ONLY = """
version: 1
accounts:
  default: {provider: openai}
agents:
  codex: {adapter: codex-cli, account: default}
roles:
  executor: codex
workflow:
  stages: [execute, test]
"""


def gates_only(repo: Path, fake_codex: Path) -> Bridge:
    """No review stage: a task that passes every gate closes itself, the riskiest setup."""
    return Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=str(fake_codex / "locks")),
                  parse(GATES_ONLY))


def verified(b: Bridge, command: str) -> tuple[str, dict]:
    t = run(b.create_task(spec(verification_commands=[command])))["task_id"]
    run(b.execute_task(t, wait=True))
    return t, b.store.attempts(t)[-1]["verification"]


# @pt.id KIT-SEC-EVID-001-verification-outside
# @pt.item A verification command that writes outside the allowed paths
# @pt.condition gates-only workflow; the executor's check script writes OUTSIDE.txt at the project root
# @pt.expected the write is a scope violation (outside allowed paths: OUTSIDE.txt), listed as changed by verification; the task goes to REWORK instead of closing itself
# @pt.target bridge.review.recheck, TaskRunner._run_inner
# @pt.method integration
# @pt.legacy audit SEC-01: the scope check ran before verification, so the task closed DONE
def test_verification_write_outside_is_violation(repo, fake_codex, monkeypatch):
    payload = "import pathlib\npathlib.Path('OUTSIDE.txt').write_text('x')"
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;src/check.py={payload}")
    b = gates_only(repo, fake_codex)
    t, ver = verified(b, "python3 src/check.py")
    assert "outside allowed paths: OUTSIDE.txt" in ver["scope_violations"]
    assert ver["changed_by_verification"] == ["OUTSIDE.txt"]
    assert b.store.get(t)["state"] == State.REWORK


# @pt.id KIT-SEC-EVID-002-verification-protected
# @pt.item A verification command that rewrites a protected file
# @pt.condition gates-only workflow; the check script rewrites .env
# @pt.expected protected file changed: .env is a violation and the task does not close
# @pt.target bridge.review.recheck, TaskRunner._run_inner
# @pt.method integration
# @pt.legacy audit SEC-01: .env was rewritten and the task closed DONE
def test_verification_write_protected_is_violation(repo, fake_codex, monkeypatch):
    payload = "import pathlib\npathlib.Path('.env').write_text('TAMPERED=1')"
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;src/check.py={payload}")
    b = gates_only(repo, fake_codex)
    t, ver = verified(b, "python3 src/check.py")
    assert "protected file changed: .env" in ver["scope_violations"]
    assert b.store.get(t)["state"] != State.DONE


# @pt.id KIT-SEC-EVID-003-agent-leftover
# @pt.item A process the agent leaves behind is killed with the run
# @pt.condition the agent CLI starts a background `sleep 2; echo late > LATE_OUTSIDE.txt` in its process group and exits, the child with its output sent to /dev/null or still holding the agent's stdout and stderr
# @pt.expected the task closes promptly and, after the child would have written, LATE_OUTSIDE.txt does not exist
# @pt.target bridge.osutil.end_group via Adapter.run
# @pt.method integration
# @pt.legacy audit SEC-04: the child wrote after the snapshot and after DONE
@pytest.mark.parametrize("redirect", [" >/dev/null 2>&1", ""])
def test_agent_leftover_is_killed(repo, fake_codex, tmp_path, monkeypatch, redirect):
    wrap = tmp_path / "wrap"
    wrap.mkdir()
    fakes = Path(os.environ["PATH"].split(os.pathsep)[0])
    codex = wrap / "codex"
    codex.write_text("#!/bin/sh\n"
                     f"( sleep 2; echo late > LATE_OUTSIDE.txt ){redirect} &\n"
                     f"exec {fakes / 'codex'} \"$@\"\n")
    codex.chmod(codex.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrap}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    started = time.monotonic()
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.DONE and time.monotonic() - started < 15
    time.sleep(3)
    assert not (repo / "LATE_OUTSIDE.txt").exists()


# @pt.id KIT-SEC-EVID-004-verification-leftover
# @pt.item A background process a check leaves behind is killed with the check
# @pt.condition a verification command starts `sleep 2; echo late > LATE.txt` in the background, keeps its stdout open and exits 0
# @pt.expected the check passes promptly (no wait for the child's pipe) and LATE.txt is never written
# @pt.target bridge.review.run_verification
# @pt.method integration
# @pt.legacy the child outlived verification and could write after the second look
def test_verification_leftover_is_killed(repo, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = gates_only(repo, fake_codex)
    cmd = ("python3 -c \"import subprocess; "
           "subprocess.Popen(['sh', '-c', 'sleep 2; echo late > LATE.txt'])\"")
    started = time.monotonic()
    t, ver = verified(b, cmd)
    assert ver["checks"][0]["passed"] and time.monotonic() - started < 20
    time.sleep(3)
    assert not (repo / "LATE.txt").exists()
    assert b.store.get(t)["state"] == State.DONE


# @pt.id KIT-SEC-EVID-005-cache-noise
# @pt.item Test caches a check creates are not scope violations
# @pt.condition the check creates .pytest_cache/v/cache and src/__pycache__/app.cpython-312.pyc
# @pt.expected no scope violation; both files are still listed under changed_by_verification; the task closes
# @pt.target bridge.review.VERIFICATION_NOISE
# @pt.method integration
# @pt.legacy none
def test_cache_noise_is_not_violation(repo, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = gates_only(repo, fake_codex)
    cmd = ("python3 -c \"import pathlib; "
           "[p.parent.mkdir(parents=True, exist_ok=True) or p.write_text('x') for p in "
           "(pathlib.Path('.pytest_cache/v/cache'), pathlib.Path('src/__pycache__/app.cpython-312.pyc'))]\"")
    t, ver = verified(b, cmd)
    assert ver["scope_violations"] == []
    assert set(ver["changed_by_verification"]) == {".pytest_cache/v/cache",
                                                   "src/__pycache__/app.cpython-312.pyc"}
    assert b.store.get(t)["state"] == State.DONE


# @pt.id KIT-UNIT-047-recheck
# @pt.item The second look at the worktree after verification
# @pt.condition a file dirty before the attempt, an agent change, then a check that reverts the agent change, deletes a tracked file, edits the dirty file and adds a cache file
# @pt.expected the result keeps the agent's file, adds the deletion and the dirty-file edit, lists the cache file as changed by verification but not in files
# @pt.target bridge.review.recheck
# @pt.method unit
# @pt.legacy none
def test_recheck_cases(repo):
    cfg = BridgeConfig()
    (repo / "notes.txt").write_text("mine\n")            # dirty before the attempt
    before = snapshot(repo, cfg.protected_paths)
    (repo / "src" / "app.py").write_text("x = 2\n")      # the agent's change
    changes = diff(repo, before, cfg.protected_paths)
    assert changes["files"] == ["src/app.py"]
    (repo / "src" / "app.py").write_text("x = 1\n")      # the check reverts it
    (repo / ".gitignore").unlink()                       # deletes a tracked file
    (repo / "notes.txt").write_text("changed\n")         # edits the dirty file
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "m.pyc").write_text("x")
    final = recheck(repo, before, changes, cfg)
    assert final["files"] == [".gitignore", "notes.txt", "src/app.py"]
    assert set(final["changed_by_verification"]) == {".gitignore", "notes.txt", "__pycache__/m.pyc"}
    assert final["head_changed"] is False
