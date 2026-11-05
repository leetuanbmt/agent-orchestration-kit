"""E2E: the task lifecycle driven through the real CLI, verified through results, events and files."""

from __future__ import annotations

import json
import time
from pathlib import Path

from tests.qa.conftest import Sandbox, alive

WRITE = "src/app.py=x = 2\n"
CHECK = "python3 -c \"assert 'x = 2' in open('src/app.py').read()\""


# @pt.id QA-E2E-001-happy-path
# @pt.item Create, execute, verify, review and close a task from the CLI
# @pt.condition fresh project after `agent-bridge init`; the executor writes x = 2; one verification command
# @pt.expected READY -> REVIEW -> DONE; the file changed, the patch and the bridge's own check are in the result, the review is recorded
# @pt.target bridge.cli create/execute/result/review/list
# @pt.method e2e
# @pt.legacy none
def test_happy_path(sandbox: Sandbox) -> None:
    sandbox.env["FAKE_CODEX_WRITE"] = WRITE
    task = sandbox.create(verification_commands=[CHECK])
    assert sandbox.ok("status", task)["state"] == "READY"
    assert sandbox.ok("execute", task)["state"] == "REVIEW"
    assert (sandbox.root / "src/app.py").read_text() == "x = 2\n"
    result = sandbox.ok("result", task)
    attempt = result["attempts"][-1]
    assert attempt["changes"]["files"] == ["src/app.py"]
    assert "+x = 2" in attempt["changes"]["patch"]
    assert attempt["verification"]["passed"] is True
    assert attempt["verification"]["checks"][0]["exit_code"] == 0
    done = sandbox.ok("review", task, "--decision", "approve", "--notes", "diff and check read")
    assert done["state"] == "DONE"
    assert [e["to_state"] for e in sandbox.events(task) if e["kind"] == "transition"][-1] == "DONE"
    assert sandbox.ok("result", task)["reviews"][-1]["notes"] == "diff and check read"
    assert [t["task_id"] for t in sandbox.ok("list")] == [task]


# @pt.id QA-E2E-002-rework-loop
# @pt.item Rework continues the same agent session
# @pt.condition a task in REVIEW is sent back with notes, then resumed from the CLI
# @pt.expected REWORK, then the resume reuses the session (codex exec resume) with the notes in the prompt, then REVIEW and DONE
# @pt.target bridge.cli review/resume
# @pt.method e2e
# @pt.legacy none
def test_rework_resumes_same_session(sandbox: Sandbox) -> None:
    sandbox.env["FAKE_CODEX_WRITE"] = WRITE
    task = sandbox.create()
    first = sandbox.ok("execute", task)
    assert sandbox.ok("review", task, "--decision", "rework",
                      "--notes", "add a docstring")["state"] == "REWORK"
    again = sandbox.ok("resume", task)
    assert again["state"] == "REVIEW" and again["session_id"] == first["session_id"]
    last = sandbox.calls()[-1]
    assert last["argv"][:2] == ["exec", "resume"] and "add a docstring" in last["prompt"]
    assert sandbox.ok("review", task, "--decision", "approve", "--notes", "ok")["state"] == "DONE"


# @pt.id QA-E2E-003-block
# @pt.item A blocked task is terminal
# @pt.condition review --decision block, then resume and execute
# @pt.expected BLOCKED with the notes in failure; resume and execute fail with a clear message; no further agent run
# @pt.target bridge.cli review/resume/execute
# @pt.method e2e
# @pt.legacy none
def test_block_is_terminal(sandbox: Sandbox) -> None:
    task = sandbox.create()
    sandbox.ok("execute", task)
    blocked = sandbox.ok("review", task, "--decision", "block", "--notes", "wrong approach")
    assert blocked["state"] == "BLOCKED" and "wrong approach" in blocked["failure"]
    runs = len(sandbox.calls())
    for args in (("resume", task), ("execute", task)):
        res = sandbox.cli(*args)
        assert res.code == 1 and "BLOCKED" in res.err, res.err
    assert len(sandbox.calls()) == runs


# @pt.id QA-E2E-004-dedup
# @pt.item Creating the same spec twice
# @pt.condition the identical spec file is submitted twice, then a spec that differs by one criterion
# @pt.expected the second call returns the same task with duplicate=true; the changed spec gets a new id
# @pt.target bridge.cli create
# @pt.method e2e
# @pt.legacy none
def test_duplicate_create(sandbox: Sandbox) -> None:
    spec = sandbox.spec()
    first, second = sandbox.ok("create", str(spec)), sandbox.ok("create", str(spec))
    assert second["task_id"] == first["task_id"] and second["duplicate"] is True
    other = sandbox.create(acceptance_criteria=["something else"])
    assert other != first["task_id"]


# @pt.id QA-E2E-005-dependencies
# @pt.item depends_on keeps a task in BACKLOG until its dependency is DONE
# @pt.condition T2 depends on T1; T2 is executed early, then T1 is completed
# @pt.expected T2 is BACKLOG and execute refuses naming the dependency; after T1 is DONE, T2 is READY and runs
# @pt.target bridge.cli create/execute
# @pt.method e2e
# @pt.legacy none
def test_dependencies(sandbox: Sandbox) -> None:
    first = sandbox.create()
    second = sandbox.create("Second step", depends_on=[first])
    assert sandbox.ok("status", second)["state"] == "BACKLOG"
    res = sandbox.cli("execute", second)
    assert res.code == 1 and first in res.err
    sandbox.ok("execute", first)
    sandbox.ok("review", first, "--decision", "approve", "--notes", "ok")
    assert sandbox.ok("status", second)["state"] == "READY"
    assert sandbox.ok("execute", second)["state"] == "REVIEW"


# @pt.id QA-E2E-006-attempt-budget
# @pt.item A task that keeps failing its checks ends BLOCKED
# @pt.condition max_attempts 2 and a verification command that always fails
# @pt.expected attempt 1 -> REWORK naming the failed check; attempt 2 -> BLOCKED; a third resume is refused without running the agent
# @pt.target bridge.cli execute/resume
# @pt.method e2e
# @pt.legacy none
def test_attempt_budget(sandbox: Sandbox) -> None:
    task = sandbox.create(max_attempts=2, verification_commands=["python3 -c \"raise SystemExit(3)\""])
    first = sandbox.ok("execute", task)
    assert first["state"] == "REWORK" and "check failed" in first["failure"]
    assert sandbox.ok("resume", task)["state"] == "BLOCKED"
    runs = len(sandbox.calls())
    assert sandbox.cli("resume", task).code == 1
    assert len(sandbox.calls()) == runs


# @pt.id QA-E2E-007-high-risk
# @pt.item A high-risk task waits for a human after approval
# @pt.condition risk_level high; the client approves the review
# @pt.expected APPROVED (not DONE), needs_human_approval true, and the next step names `agent-bridge approve <id>`; the human approval itself is not run (human-only)
# @pt.target bridge.cli review
# @pt.method e2e
# @pt.legacy none
def test_high_risk_waits_for_human(sandbox: Sandbox) -> None:
    task = sandbox.create(risk_level="high")
    sandbox.ok("execute", task)
    out = sandbox.ok("review", task, "--decision", "approve", "--notes", "ok")
    assert out["state"] == "APPROVED" and out["needs_human_approval"] is True
    assert f"agent-bridge approve {task}" in out["next"]


# @pt.id QA-E2E-008-sensitive-path
# @pt.item A change under a sensitive path needs a human
# @pt.condition a low-risk task allowed to write migrations/ writes a migration file
# @pt.expected after approval the task stops at APPROVED with needs_human_approval
# @pt.target bridge.review.touches_sensitive via the CLI
# @pt.method e2e
# @pt.legacy none
def test_sensitive_path_needs_human(sandbox: Sandbox) -> None:
    sandbox.env["FAKE_CODEX_WRITE"] = "migrations/0001.sql=create table t(x int);"
    task = sandbox.create("Add a migration", allowed_paths=["migrations/**"])
    assert sandbox.ok("execute", task)["state"] == "REVIEW"
    out = sandbox.ok("review", task, "--decision", "approve", "--notes", "ok")
    assert out["state"] == "APPROVED" and out["needs_human_approval"] is True


# @pt.id QA-E2E-009-cancel-running
# @pt.item Cancelling a running task from another terminal
# @pt.condition terminal A runs `execute` on a hanging agent; terminal B runs `cancel`
# @pt.expected B returns CANCELLED within the cancel wait; A exits; the agent and its child process are gone
# @pt.target bridge.cli cancel, Bridge.cancel_task
# @pt.method e2e
# @pt.legacy none
def test_cancel_running_from_other_process(sandbox: Sandbox) -> None:
    task = sandbox.create()
    runner = sandbox.spawn("execute", task, env={"FAKE_CODEX_SCENARIO": "hang"})
    child = Path(sandbox.env["FAKE_CODEX_DIR"]) / "child.pid"
    deadline = time.monotonic() + 15
    while not child.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert sandbox.ok("status", task)["state"] == "RUNNING"
    started = time.monotonic()
    out = sandbox.ok("cancel", task, "--reason", "user changed their mind")
    assert out["state"] == "CANCELLED" and time.monotonic() - started < 20
    runner.wait(20)
    pids = [int((child.parent / name).read_text()) for name in ("child.pid", "self.pid")]
    deadline = time.monotonic() + 10
    while any(alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not any(alive(p) for p in pids)
    assert sandbox.ok("status", task)["failure"] == "CANCELLED: user changed their mind"


# @pt.id QA-E2E-010-failed-check
# @pt.item Failing verification sends the task to REWORK with the check output
# @pt.condition the agent completes but the verification command exits 1 with a message
# @pt.expected REWORK; failure names the check; the check output is in the result
# @pt.target bridge.cli execute/result
# @pt.method e2e
# @pt.legacy none
def test_failed_check(sandbox: Sandbox) -> None:
    task = sandbox.create(verification_commands=[CHECK])  # nothing writes x = 2
    out = sandbox.ok("execute", task)
    assert out["state"] == "REWORK" and "check failed" in out["failure"]
    check = sandbox.ok("result", task)["attempts"][-1]["verification"]["checks"][0]
    assert check["passed"] is False and "AssertionError" in check["output_tail"]


# @pt.id QA-E2E-011-list-views
# @pt.item Task listings for a person in a terminal
# @pt.condition two tasks, one in REVIEW; `list --state REVIEW`, `list --limit 1`, `tasks` across projects
# @pt.expected the state filter and limit apply; `tasks` groups this project with its counts
# @pt.target bridge.cli list/tasks
# @pt.method e2e
# @pt.legacy none
def test_list_views(sandbox: Sandbox) -> None:
    review = sandbox.create()
    sandbox.ok("execute", review)
    other = sandbox.create("Other thing")
    assert [t["task_id"] for t in sandbox.ok("list", "--state", "REVIEW")] == [review]
    assert len(sandbox.ok("list", "--limit", "1")) == 1
    projects = sandbox.ok("tasks")["projects"]
    assert len(projects) == 1 and {t["task_id"] for t in projects[0]["tasks"]} == {review, other}
    assert json.dumps(projects[0]["counts"])
