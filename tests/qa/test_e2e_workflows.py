"""E2E: planner workflows, reviewer agents, shutdown mid-run, storage commands and the TUI entry."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from tests.qa.conftest import Sandbox, alive
from tests.qa.test_e2e_mcp import SPEC, call, session

AGENTS = """
version: 1
accounts:
  cx1: {{provider: openai, home: {{home}}/cx1}}
agents:
  planner: {{adapter: codex-cli, account: cx1}}
  codex: {{adapter: codex-cli, account: cx1}}
  reviewer: {{adapter: codex-cli, account: cx1}}
  claude: {{adapter: client}}
roles: {{planner: planner, executor: codex, reviewer: {reviewer}}}
workflow: {{stages: [plan, execute, test, review]}}
"""
TWO_STEPS = json.dumps([
    {"objective": "Step one", "allowed_paths": ["src/**"], "acceptance_criteria": ["a"],
     "verification_commands": [], "depends_on": [], "risk_level": "low"},
    {"objective": "Step two", "allowed_paths": ["src/**"], "acceptance_criteria": ["b"],
     "verification_commands": [], "depends_on": [0], "risk_level": "low"}])


def workflow(box: Sandbox, wid: str, until: set[str], timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = box.ok("workflow-status", wid)
        if status["state"] in until or time.monotonic() > deadline:
            return status
        time.sleep(0.3)


# @pt.id QA-E2E-090-planner-workflow
# @pt.item Plan an objective and drive the workflow, the client reviewing
# @pt.condition a Codex planner returns two dependent tasks; run-workflow with the client as reviewer
# @pt.expected the workflow is READY with both tasks (step two in BACKLOG); run-workflow runs step one to REVIEW and stops WAITING; after the review, run-workflow finishes step two
# @pt.target bridge.cli plan/run-workflow/workflow-status
# @pt.method e2e
# @pt.legacy none


def test_planner_workflow(sandbox: Sandbox) -> None:
    sandbox.orchestrator(AGENTS.format(reviewer="claude"))
    env = {"FAKE_CODEX_PLAN": TWO_STEPS}
    planned = sandbox.ok("plan", "Do two steps", env=env)
    wid = planned["workflow_id"]
    planned = workflow(sandbox, wid, {"READY", "FAILED"})
    assert planned["state"] == "READY", planned
    states = [t["state"] for t in planned["tasks"]]
    assert states == ["READY", "BACKLOG"], states
    sandbox.ok("run-workflow", wid, env=env)
    first = workflow(sandbox, wid, {"WAITING", "DONE", "INCOMPLETE"})
    assert first["state"] == "WAITING" and first["tasks"][0]["state"] == "REVIEW", first
    step_one, step_two = (t["task_id"] for t in first["tasks"])
    sandbox.ok("review", step_one, "--decision", "approve", "--notes", "ok")
    sandbox.ok("run-workflow", wid, env=env)
    final = workflow(sandbox, wid, {"WAITING", "DONE", "INCOMPLETE"})
    assert final["tasks"][1]["state"] == "REVIEW", final
    sandbox.ok("review", step_two, "--decision", "approve", "--notes", "ok")
    assert sandbox.ok("status", step_two)["state"] == "DONE"


# @pt.id QA-E2E-091-bad-plan
# @pt.item A planner that returns an invalid plan
# @pt.condition the plan's second task depends on a later task, and another plan asks for a protected path
# @pt.expected the workflow is FAILED with the reason and no task is created
# @pt.target Bridge._plan validation via bridge.cli plan
# @pt.method e2e
# @pt.legacy none
def test_bad_plan_creates_nothing(sandbox: Sandbox) -> None:
    sandbox.orchestrator(AGENTS.format(reviewer="claude"))
    forward = json.loads(TWO_STEPS)
    forward[0]["depends_on"] = [1]
    protected = json.loads(TWO_STEPS)[:1]
    protected[0]["allowed_paths"] = [".env"]
    for plan, needle in ((forward, "depends_on"), (protected, "protected")):
        wid = sandbox.ok("plan", "Bad plan", env={"FAKE_CODEX_PLAN": json.dumps(plan)})["workflow_id"]
        status = workflow(sandbox, wid, {"READY", "FAILED"})
        assert status["state"] == "FAILED" and needle in json.dumps(status["detail"]), status
    assert sandbox.ok("list") == []


# @pt.id QA-E2E-092-reviewer-agent
# @pt.item A reviewer agent decides after the bridge's checks
# @pt.condition a Codex reviewer; first it approves, then on another task it asks for rework
# @pt.expected the approved task ends DONE with a review recorded as the agent's; the other goes to REWORK with the reviewer's notes
# @pt.target TaskRunner._review_stage via bridge.cli execute
# @pt.method e2e
# @pt.legacy none
def test_reviewer_agent(sandbox: Sandbox) -> None:
    sandbox.orchestrator(AGENTS.format(reviewer="reviewer"))
    good = sandbox.create("Good change")
    out = sandbox.ok("execute", good, env={"FAKE_CODEX_DECISION": "approve:clean diff"})
    assert out["state"] == "DONE", out
    review = sandbox.ok("result", good)["reviews"][-1]
    assert review["decision"] == "approve" and review["reviewer"] == "reviewer"
    bad = sandbox.create("Bad change")
    out = sandbox.ok("execute", bad, env={"FAKE_CODEX_DECISION": "rework:missing test"})
    assert out["state"] == "REWORK", out
    assert sandbox.ok("result", bad)["reviews"][-1]["notes"] == "missing test"


# @pt.id QA-E2E-093-session-closed-mid-run
# @pt.item The Claude Code session (MCP server) closes while a task runs
# @pt.condition a task started through MCP on a hanging agent; the client disconnects
# @pt.expected the task is PAUSED (INTERRUPTED) with its attempt given back, the agent is killed, and a resume from the CLI reaches REVIEW
# @pt.target Bridge.shutdown via bridge.server
# @pt.method e2e
# @pt.legacy none
def test_session_closed_mid_run(sandbox: Sandbox) -> None:
    state = Path(sandbox.env["FAKE_CODEX_DIR"])

    async def main() -> str:
        async with session(sandbox, {"FAKE_CODEX_SCENARIO": "hang"}) as client:
            err, created = await call(client, "bridge_create_task", **SPEC)
            task = created["task_id"]
            await call(client, "bridge_execute_task", task_id=task)
            deadline = time.monotonic() + 15
            while not (state / "self.pid").exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            return task
    task = asyncio.run(main())
    agent = int((state / "self.pid").read_text())
    row = sandbox.wait_state(task, {"PAUSED", "FAILED"}, 20)
    assert row["state"] == "PAUSED" and row["failure"].startswith("INTERRUPTED"), row
    assert row["attempts"] == 0
    deadline = time.monotonic() + 10
    while alive(agent) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not alive(agent)
    assert sandbox.ok("resume", task)["state"] == "REVIEW"


def storage_processes() -> list[dict]:
    from bridge import storage
    return storage.running_processes()


# @pt.id QA-E2E-094-storage-commands
# @pt.item Storage housekeeping a user can run
# @pt.condition storage status, prune (preview, then apply after a project folder was deleted), gc preview
# @pt.expected status names the central store; prune lists the deleted folder and removes it only with --apply, which refuses with the pids while any agent-bridge process runs; gc previews without deleting
# @pt.target bridge.cli storage
# @pt.method e2e
# @pt.legacy none


def test_storage_commands(sandbox: Sandbox) -> None:
    import shutil

    from tests.qa.conftest import make_project
    sandbox.ok("execute", sandbox.create())
    status = sandbox.ok("storage", "status")
    assert json.dumps(status).count(".agent-bridge") >= 1, status
    gone = make_project(sandbox.base / "gone")
    assert sandbox.cli("init", cwd=gone).code == 0
    sandbox.ok("create", str(sandbox.spec("Short lived")), cwd=gone)
    shutil.rmtree(gone)
    preview = sandbox.ok("storage", "prune")
    assert preview["applied"] is False and str(gone) in preview["removed"], preview
    applied = sandbox.cli("storage", "prune", "--apply")
    others = storage_processes()
    if others:  # any agent-bridge process on the machine (e.g. an open Claude Code session)
        assert applied.code == 1 and "bridge processes are running" in applied.err, applied.err
        assert str(gone) in sandbox.ok("storage", "prune")["removed"]  # nothing was changed
    else:
        assert applied.code == 0 and str(gone) in applied.json["removed"], applied.err
        assert sandbox.ok("storage", "prune")["removed"] == []
    gc = sandbox.ok("storage", "gc", "--logs-older-than", "1")
    assert gc.get("applied") is False, gc


# @pt.id QA-E2E-095-tui-without-terminal
# @pt.item Running the dashboard command without a terminal
# @pt.condition `agent-bridge` with no subcommand and stdout piped
# @pt.expected a short usage text instead of a broken screen, no traceback
# @pt.target bridge.cli main
# @pt.method e2e
# @pt.legacy none
def test_tui_without_terminal(sandbox: Sandbox) -> None:
    res = sandbox.cli()
    assert "Traceback" not in res.err and ("usage" in (res.out + res.err).lower()), (res.out, res.err)
