"""Git failures never turn into empty, passing evidence (audit REL-01)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from bridge.models import State
from bridge.review import EvidenceError, diff, snapshot
from tests.conftest import run, spec
from tests.integration.test_evidence_window import gates_only


def broken_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: Path) -> None:
    """A `git` first on PATH whose `status` fails (exit 128) while `marker` exists."""
    real = shutil.which("git")
    wrap = tmp_path / "gitwrap"
    wrap.mkdir()
    exe = wrap / "git"
    exe.write_text("#!/bin/sh\n"
                   f'if [ -e "{marker}" ] && [ "$3" = status ]; then\n'
                   '  echo "fatal: index file corrupt" >&2; exit 128\nfi\n'
                   f'exec "{real}" "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrap}{os.pathsep}{os.environ['PATH']}")


# @pt.id KIT-UNIT-048-git-failure-raises
# @pt.item A failing git command is an error, not an empty result
# @pt.condition `git status` exits 128 during diff; a repository without any commit
# @pt.expected diff raises EvidenceError naming the command and git's message; a repository with no HEAD still snapshots with head None
# @pt.target bridge.review._git, bridge.review.diff, bridge.review.snapshot
# @pt.method unit
# @pt.legacy audit REL-01: the failure returned "" and the change was silently missed
def test_git_failure_raises(repo, tmp_path, monkeypatch):
    before = snapshot(repo, [])
    (repo / "OUTSIDE.txt").write_text("x")
    marker = tmp_path / "fail"
    marker.touch()
    broken_git(tmp_path, monkeypatch, marker)
    with pytest.raises(EvidenceError, match="git status failed with exit 128: fatal: index file corrupt"):
        diff(repo, before, [])
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    subprocess.run(["git", "-C", str(fresh), "init", "-q"], check=True)
    marker.unlink()
    assert snapshot(fresh, [])["head"] is None


# @pt.id KIT-REL-EVID-001-after-run
# @pt.item Git fails after the agent ran
# @pt.condition gates-only workflow; the agent's run creates the marker that makes `git status` fail
# @pt.expected the task is PAUSED with EVIDENCE_ERROR and its attempt given back; it never reaches DONE or REVIEW; the attempt is closed as evidence_error
# @pt.target TaskRunner._run, TaskRunner._park_without_evidence
# @pt.method integration
# @pt.legacy the empty evidence passed every gate and the task closed itself
def test_git_failure_after_run_pauses(repo, fake_codex, tmp_path, monkeypatch):
    marker = repo / "src" / "BREAK_GIT"
    broken_git(tmp_path, monkeypatch, marker)
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;{marker.relative_to(repo)}=1")
    b = gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    row = b.store.get(t)
    assert row["state"] == State.PAUSED and row["failure"].startswith("EVIDENCE_ERROR: git status")
    assert row["attempts"] == 0
    assert b.store.attempts(t)[-1]["outcome"] == "evidence_error"


# @pt.id KIT-REL-EVID-002-before-run
# @pt.item Git fails before the agent starts
# @pt.condition `git status` fails from the start
# @pt.expected the agent never runs and the task is PAUSED with EVIDENCE_ERROR, attempt given back
# @pt.target TaskRunner._run_inner setup
# @pt.method integration
# @pt.legacy the run started with an empty snapshot
def test_git_failure_before_run_pauses(repo, fake_codex, tmp_path, monkeypatch):
    marker = tmp_path / "fail"
    marker.touch()
    broken_git(tmp_path, monkeypatch, marker)
    b = gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    row = b.store.get(t)
    assert row["state"] == State.PAUSED and row["failure"].startswith("EVIDENCE_ERROR")
    assert row["attempts"] == 0
    assert not (fake_codex / "log.jsonl").exists()
