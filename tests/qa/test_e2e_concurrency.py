"""E2E: several bridge processes on one machine, parallel commands, crashes and recovery."""

from __future__ import annotations

import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.qa.conftest import Sandbox, alive

TWO_CODEX = """
version: 1
accounts:
  cx1: {provider: openai, home: {home}/cx1, max_concurrency: 1}
  cx2: {provider: openai, home: {home}/cx2, max_concurrency: 1}
agents:
  codex1: {adapter: codex-cli, account: cx1}
  codex2: {adapter: codex-cli, account: cx2}
  claude: {adapter: client}
roles: {executor: {agents: [codex1, codex2]}, reviewer: claude}
workflow: {stages: [execute, test, review]}
"""


def wait_running(box: Sandbox, task: str, timeout: float = 15) -> None:
    assert box.wait_state(task, {"RUNNING"}, timeout)["state"] == "RUNNING"


# @pt.id QA-E2E-050-same-task-twice
# @pt.item Two terminals start the same task at the same moment
# @pt.condition two `execute` processes on one READY task while the agent takes 3 s
# @pt.expected exactly one runs the agent and reaches REVIEW; the other exits 1 with a clear message; one attempt is recorded
# @pt.target Store.transition guard via bridge.cli execute
# @pt.method e2e
# @pt.legacy none
def test_same_task_started_twice(sandbox: Sandbox) -> None:
    task = sandbox.create()
    env = {"FAKE_CODEX_SCENARIO": "slow", "FAKE_CODEX_SLOW": "3"}
    procs = [sandbox.spawn("execute", task, env=env) for _ in range(2)]
    results = [(p.wait(60), p.stderr.read()) for p in procs]
    codes = sorted(code for code, _ in results)
    assert codes == [0, 1], results
    loser = next(err for code, err in results if code == 1)
    assert "Traceback" not in loser and loser.strip(), loser
    assert len(sandbox.calls()) == 1
    assert sandbox.ok("status", task)["state"] == "REVIEW"
    assert len(sandbox.ok("result", task)["attempts"]) == 1


# @pt.id QA-E2E-051-concurrency-across-bridges
# @pt.item An account's concurrency limit holds across bridges in two worktrees
# @pt.condition a second git worktree with its own bridge; cx1 and cx2 allow one run each (machine-wide lock dir); worktree A runs a slow task pinned to codex1; worktree B then starts a task pinned to codex1 and an unpinned one; a second task in worktree A while its first runs
# @pt.expected B's pinned task is refused as busy and stays READY; B's unpinned task is routed to codex2; A refuses a second writer in its own worktree; A's task finishes normally
# @pt.target AccountLock, the one-writer rule and routing via bridge.cli execute
# @pt.method e2e
# @pt.legacy none
def test_concurrency_limit_across_bridges(sandbox: Sandbox) -> None:
    import json
    from tests.qa.conftest import git
    sandbox.orchestrator(TWO_CODEX)
    (sandbox.root / ".ai" / "config.json").write_text(json.dumps({"retry_backoff_s": 0}))
    tree = sandbox.base / "tree"
    git(sandbox.root, "worktree", "add", "-q", str(tree))
    (tree / ".ai").mkdir(exist_ok=True)
    for name in ("orchestrator.yaml", "config.json"):
        (tree / ".ai" / name).write_text((sandbox.root / ".ai" / name).read_text())
    first = sandbox.create("Slow one", agent="codex1")
    runner = sandbox.spawn("execute", first, env={"FAKE_CODEX_SCENARIO": "slow",
                                                  "FAKE_CODEX_SLOW": "8"})
    wait_running(sandbox, first)
    second_here = sandbox.create("Second writer here", agent="codex2")
    res = sandbox.cli("execute", second_here)
    assert res.code == 1 and "already writing in this worktree" in res.err, res.err
    pinned = sandbox.ok("create", str(sandbox.spec("Also on cx1", agent="codex1")), cwd=tree)
    res = sandbox.cli("execute", pinned["task_id"], cwd=tree)
    assert res.code == 1 and "busy" in res.err, res.err
    assert sandbox.ok("status", pinned["task_id"], cwd=tree)["state"] == "READY"
    free = sandbox.ok("create", str(sandbox.spec("Anywhere")), cwd=tree)["task_id"]
    out = sandbox.ok("execute", free, cwd=tree, env={"FAKE_CODEX_SCENARIO": "ok"})
    assert out["agent"] == "codex2" and out["state"] == "REVIEW", out
    assert runner.wait(60) == 0
    assert sandbox.ok("status", first)["state"] == "REVIEW"


# @pt.id QA-E2E-052-parallel-creates
# @pt.item Many terminals creating tasks at once
# @pt.condition 20 `create` processes in parallel with distinct specs
# @pt.expected all succeed with 20 distinct ids; no database-locked error
# @pt.target Store.create via bridge.cli create
# @pt.method e2e
# @pt.legacy none
def test_parallel_creates(sandbox: Sandbox) -> None:
    specs = [sandbox.spec(f"Parallel task {i}") for i in range(20)]
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda s: sandbox.cli("create", str(s)), specs))
    assert all(r.code == 0 for r in results), [r.err for r in results if r.code]
    assert len({r.json["task_id"] for r in results}) == 20


# @pt.id QA-E2E-053-runner-killed
# @pt.item The terminal running a task is killed (kill -9) mid-run
# @pt.condition `execute` on a hanging agent is SIGKILLed; the user then runs `list` and a write command
# @pt.expected the next writing command parks the task PAUSED (INTERRUPTED) and kills the orphaned agent; resume reaches REVIEW on the same session
# @pt.target Bridge.recover via bridge.cli
# @pt.method e2e
# @pt.legacy none
def test_runner_killed_then_recovered(sandbox: Sandbox) -> None:
    task = sandbox.create()
    runner = sandbox.spawn("execute", task, env={"FAKE_CODEX_SCENARIO": "hang"})
    state = Path(sandbox.env["FAKE_CODEX_DIR"])
    deadline = time.monotonic() + 15
    while not (state / "self.pid").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    agent = int((state / "self.pid").read_text())
    session = sandbox.wait_state(task, {"RUNNING"})["session_id"]
    os.kill(runner.pid, signal.SIGKILL)
    runner.wait(10)
    assert sandbox.ok("status", task)["state"] == "RUNNING"  # read-only views do not recover
    sandbox.create("Any write command recovers")
    row = sandbox.ok("status", task)
    assert row["state"] == "PAUSED" and row["failure"].startswith("INTERRUPTED"), row
    deadline = time.monotonic() + 10
    while alive(agent) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not alive(agent), "orphaned agent still running"
    out = sandbox.ok("resume", task)
    assert out["state"] == "REVIEW" and out["session_id"] == session


# @pt.id QA-E2E-054-cancel-during-verification
# @pt.item Cancelling while the bridge runs the verification commands
# @pt.condition a verification command that sleeps 30 s; cancel from a second terminal once VERIFYING
# @pt.expected CANCELLED within the cancel wait; the verification process is killed; the runner exits
# @pt.target run_verification cancel via bridge.cli cancel
# @pt.method e2e
# @pt.legacy none
def test_cancel_during_verification(sandbox: Sandbox) -> None:
    marker = sandbox.root / "src" / "verify.pid"  # checks may write only in the worktree
    cmd = (f"python3 -c \"import os,time; open('{marker}','w').write(str(os.getpid())); "
           "time.sleep(30)\"")
    task = sandbox.create(verification_commands=[cmd])
    runner = sandbox.spawn("execute", task)
    assert sandbox.wait_state(task, {"VERIFYING"}, 15)["state"] == "VERIFYING"
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    started = time.monotonic()
    assert sandbox.ok("cancel", task, "--reason", "stop")["state"] == "CANCELLED"
    assert time.monotonic() - started < 20
    runner.wait(20)
    pid = int(marker.read_text())
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid)


# @pt.id QA-E2E-055-two-projects
# @pt.item One person, two projects
# @pt.condition tasks in two repositories on the same machine
# @pt.expected `tasks` lists both projects with their own tasks; each project's `list` shows only its own
# @pt.target bridge.cli tasks/list
# @pt.method e2e
# @pt.legacy none
def test_two_projects(sandbox: Sandbox) -> None:
    from tests.qa.conftest import make_project
    other = make_project(sandbox.base / "other")
    assert sandbox.cli("init", cwd=other).code == 0
    mine = sandbox.create("Mine")
    theirs = sandbox.ok("create", str(sandbox.spec("Theirs")), cwd=other)["task_id"]
    assert [t["task_id"] for t in sandbox.ok("list")] == [mine]
    assert [t["objective"] for t in sandbox.ok("list", cwd=other)] == ["Theirs"]
    projects = {p["project"]: p for p in sandbox.ok("tasks")["projects"]}
    assert set(projects) == {"proj", "other"}
    assert [t["task_id"] for t in projects["other"]["tasks"]] == [theirs]
