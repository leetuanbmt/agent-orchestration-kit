"""Ignored agent changes gate scope; reviewed verification artifacts remain report-only."""

from __future__ import annotations

from bridge.models import State
from bridge.review import diff, snapshot
from tests.conftest import git, run, spec
from tests.integration.test_evidence_window import gates_only


def ignore_build(repo) -> None:
    (repo / ".gitignore").write_text(".env\nbuild/\n.mypy_cache/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore build")


# @pt.id KIT-SEC-IGN-001-gates-only
# @pt.item An ignored-path write in a workflow nobody reviews
# @pt.condition gates-only workflow; the agent writes build/sitecustomize.py (git-ignored, outside allowed_paths) and a .mypy_cache file
# @pt.expected build/sitecustomize.py is listed as an ignored change and is a violation, the cache file is listed but is not; the task goes to REWORK instead of DONE
# @pt.target bridge.review.snapshot, bridge.review.scope_violations
# @pt.method integration
# @pt.legacy audit SEC-03: the write was invisible and the task closed itself
def test_ignored_write_gates_without_review(repo, fake_codex, monkeypatch):
    ignore_build(repo)
    monkeypatch.setenv("FAKE_CODEX_WRITE",
                       "src/app.py=x = 2\n;build/sitecustomize.py=print('pwn');.mypy_cache/x=1")
    b = gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    a = b.store.attempts(t)[-1]
    assert set(a["changes"]["ignored_files"]) == {"build/sitecustomize.py", ".mypy_cache/x"}
    assert a["verification"]["scope_violations"] == [
        "ignored file changed outside allowed paths: build/sitecustomize.py"]
    assert b.store.get(t)["state"] == State.REWORK


# @pt.id KIT-SEC-IGN-002-reviewed
# @pt.item An ignored-path write in a workflow with a reviewer
# @pt.condition the default workflow (client reviewer); the agent writes build/out.js
# @pt.expected it is listed in changes.ignored_files and is a scope violation; the task reaches REWORK
# @pt.target bridge.review.diff, TaskRunner._run_inner
# @pt.method integration
# @pt.legacy SEC-08: reviewed workflows only reported agent ignored writes
def test_ignored_write_gates_with_review(make_bridge, repo, monkeypatch):
    ignore_build(repo)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;build/out.js=1")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REWORK
    a = b.store.attempts(t)[-1]
    assert a["changes"]["ignored_files"] == ["build/out.js"]
    assert a["verification"]["scope_violations"] == [
        "ignored file changed outside allowed paths: build/out.js"]


# @pt.id KIT-SEC-IGN-004-reviewed-allowed
# @pt.item An allowed ignored-path write with a reviewer
# @pt.condition the agent writes build/out.js; build/** is allowed in the reviewed workflow
# @pt.expected the ignored write is reported without scope violations; the task reaches REVIEW
# @pt.target bridge.review.scope_violations, TaskRunner._run_inner
# @pt.method integration
# @pt.legacy none
def test_allowed_ignored_write_with_review(make_bridge, repo, monkeypatch):
    ignore_build(repo)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;build/out.js=1")
    b = make_bridge()
    t = run(b.create_task(spec(allowed_paths=["src/**", "build/**"])))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REVIEW
    a = b.store.attempts(t)[-1]
    assert a["changes"]["ignored_files"] == ["build/out.js"]
    assert a["verification"]["scope_violations"] == []


# @pt.id KIT-SEC-IGN-005-reviewed-verification
# @pt.item An ignored non-noise artifact created only by verification with a reviewer
# @pt.condition the agent changes src/app.py; a verification command creates build/report.txt
# @pt.expected the artifact is reported in changed_by_verification without scope violations; the task reaches REVIEW
# @pt.target bridge.review.recheck, bridge.review.scope_violations, TaskRunner._run_inner
# @pt.method integration
# @pt.legacy SEC-08: verification artifacts must remain report-only with review
def test_verification_ignored_write_reported_with_review(make_bridge, repo, monkeypatch):
    ignore_build(repo)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_bridge()
    command = ('python3 -c "from pathlib import Path; Path(\'build\').mkdir(); '
               'Path(\'build/report.txt\').write_text(\'report\')"')
    t = run(b.create_task(spec(verification_commands=[command])))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REVIEW
    a = b.store.attempts(t)[-1]
    assert a["changes"]["ignored_files"] == []
    assert a["verification"]["changed_by_verification"] == ["build/report.txt"]
    assert a["verification"]["scope_violations"] == []
    assert (repo / "build/report.txt").read_text() == "report"


# @pt.id KIT-SEC-IGN-003-protected-ignored
# @pt.item A nested secret file in an ignored location
# @pt.condition the default workflow; the agent writes pkg/.env (git-ignored and protected)
# @pt.expected protected file changed: pkg/.env, and the task is BLOCKED even though a reviewer exists
# @pt.target bridge.review.scope_violations
# @pt.method integration
# @pt.legacy nested ignored secrets were never looked at
def test_nested_ignored_secret_blocks(make_bridge, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;pkg/.env=TOKEN=x")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.BLOCKED and "protected file changed: pkg/.env" in out["failure"]


# @pt.id KIT-UNIT-050-ignored-diff
# @pt.item The snapshot notices added, modified and deleted ignored files
# @pt.condition an ignored file exists before; then it is rewritten with a new size, another is added and a third deleted
# @pt.expected all three are in ignored_files and none in files
# @pt.target bridge.review.snapshot, bridge.review.diff
# @pt.method unit
# @pt.legacy none
def test_ignored_diff(repo):
    ignore_build(repo)
    (repo / "build").mkdir()
    (repo / "build" / "keep.txt").write_text("a")
    (repo / "build" / "gone.txt").write_text("b")
    before = snapshot(repo, [])
    (repo / "build" / "keep.txt").write_text("changed")
    (repo / "build" / "new.txt").write_text("c")
    (repo / "build" / "gone.txt").unlink()
    d = diff(repo, before, [])
    assert d["ignored_files"] == ["build/gone.txt", "build/keep.txt", "build/new.txt"]
    assert d["files"] == []
