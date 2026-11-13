"""An out-of-scope change an earlier attempt left behind is judged against the task's first baseline."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from bridge.models import State
from tests.conftest import run, spec
from tests.integration.test_ignored_files import ignore_build

WRAP_SH = """#!/bin/sh
case "$1" in --version|auth|login) exec {real} "$@";; esac
n=$(cat {cnt} 2>/dev/null || echo 0)
n=$((n+1))
echo $n > {cnt}
cmd=$(printf '%s\\n' "$PLAN" | awk -F'|' -v i=$n '{{print $i}}')
eval "$cmd"
exec {real} "$@"
"""

IN_SCOPE = "mkdir -p src; echo 'x = 2' > src/app.py"
LEAVE = f"{IN_SCOPE}; echo x > other_root.txt"
TOUCH = "echo '# more' >> src/app.py"
RESTORE = "rm -f other_root.txt"
CLAUDE = """
version: 1
accounts:
  cl1: {provider: anthropic, home: {home}/cl1}
agents:
  client: {adapter: client}
  claude1: {adapter: claude-cli, account: cl1, allow_write: true}
roles: {executor: claude1, reviewer: client}
workflow: {stages: [execute, test, review]}
"""


def plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str, *runs: str) -> None:
    """Wrap the fake `tool`: run k first executes the k-th shell command of `runs`."""
    wrap = tmp_path / "wrap"
    wrap.mkdir(exist_ok=True)
    real = Path(os.environ["PATH"].split(os.pathsep)[0]) / tool
    exe = wrap / tool
    exe.write_text(WRAP_SH.format(real=real, cnt=wrap / f"{tool}.count"))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrap}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PLAN", "|".join(runs))
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok,ok,ok")
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "ok,ok,ok")


def bridge(kind: str, make_bridge, make_orch):
    return make_bridge() if kind == "codex" else make_orch(CLAUDE)


def attempt(kind: str, make_bridge, make_orch, fake_claude, tmp_path, monkeypatch, *runs: str,
            ignore: bool = False):
    """Run attempt 1, then resume for each further run; return (bridge, task id, state)."""
    if ignore:
        ignore_build(make_bridge().root)
    plan(tmp_path, monkeypatch, kind, *runs)
    b = bridge(kind, make_bridge, make_orch)
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.REWORK
    for _ in runs[1:]:
        out = run(b.resume_task(t, wait=True))
    return b, t, out["state"]


# @pt.id KIT-SEC-LEFT-010-kept
# @pt.item An out-of-scope file an earlier attempt wrote is still there after the next attempt
# @pt.condition attempt 1 writes other_root.txt (outside allowed_paths) and src/app.py; attempt 2 completes with in-scope work only and leaves the file
# @pt.expected the task does not reach REVIEW; attempt 2 stores `left by an earlier attempt: outside allowed paths: other_root.txt`
# @pt.target TaskRunner._scope, TaskRunner._run_inner
# @pt.method integration
# @pt.legacy attempt 2 took a baseline that already held the file and reached REVIEW with no violation
@pytest.mark.parametrize("kind", ["codex", "claude"])
def test_kept_leftover_blocks_review(kind, make_bridge, make_orch, fake_claude, tmp_path,
                                     monkeypatch):
    b, t, state = attempt(kind, make_bridge, make_orch, fake_claude, tmp_path, monkeypatch,
                          LEAVE, TOUCH)
    assert state in (State.REWORK, State.BLOCKED)
    last = b.store.attempts(t)[-1]
    assert last["n"] == 2
    assert ("left by an earlier attempt: outside allowed paths: other_root.txt"
            in last["verification"]["scope_violations"])


# @pt.id KIT-SEC-LEFT-011-restored
# @pt.item The next attempt deletes the out-of-scope file an earlier attempt left
# @pt.condition attempt 1 writes other_root.txt and src/app.py; attempt 2 removes other_root.txt
# @pt.expected attempt 2 reaches REVIEW with no scope violation: the file is back to its task-start state
# @pt.target TaskRunner._scope
# @pt.method integration
# @pt.legacy the deletion differed from attempt 2's baseline and was flagged outside allowed paths again
@pytest.mark.parametrize("kind", ["codex", "claude"])
def test_restored_leftover_reaches_review(kind, make_bridge, make_orch, fake_claude, tmp_path,
                                          monkeypatch):
    b, t, state = attempt(kind, make_bridge, make_orch, fake_claude, tmp_path, monkeypatch,
                          LEAVE, RESTORE)
    assert state == State.REVIEW
    assert b.store.attempts(t)[-1]["verification"]["scope_violations"] == []


# @pt.id KIT-SEC-LEFT-012-single
# @pt.item A single attempt with an out-of-scope write
# @pt.condition one attempt writes other_root.txt
# @pt.expected stored scope_violations is exactly `outside allowed paths: other_root.txt` (no prefix) and the task is REWORK
# @pt.target TaskRunner._scope
# @pt.method integration
# @pt.legacy none
def test_single_attempt_unchanged(make_bridge, tmp_path, monkeypatch):
    plan(tmp_path, monkeypatch, "codex", LEAVE)
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REWORK
    assert b.store.attempts(t)[-1]["verification"]["scope_violations"] == [
        "outside allowed paths: other_root.txt"]


# @pt.id KIT-SEC-LEFT-013-ignored
# @pt.item An ignored out-of-scope file an earlier attempt wrote
# @pt.condition build/ is git-ignored; attempt 1 writes build/out.js; attempt 2 either leaves it or deletes it
# @pt.expected while the file exists attempt 2 is not REVIEW and reports `left by an earlier attempt: ignored file changed outside allowed paths: build/out.js`; once deleted attempt 2 reaches REVIEW
# @pt.target TaskRunner._scope
# @pt.method integration
# @pt.legacy the file was in attempt 2's baseline, so it was never reported again
@pytest.mark.parametrize("delete", [False, True])
def test_ignored_leftover(delete, make_bridge, make_orch, fake_claude, tmp_path, monkeypatch):
    leave = f"{IN_SCOPE}; mkdir -p build; echo 1 > build/out.js"
    b, t, state = attempt("codex", make_bridge, make_orch, fake_claude, tmp_path, monkeypatch,
                          leave, "rm -f build/out.js" if delete else TOUCH, ignore=True)
    violations = b.store.attempts(t)[-1]["verification"]["scope_violations"]
    if delete:
        assert state == State.REVIEW and violations == []
    else:
        assert state in (State.REWORK, State.BLOCKED)
        assert ("left by an earlier attempt: ignored file changed outside allowed paths: "
                "build/out.js") in violations
