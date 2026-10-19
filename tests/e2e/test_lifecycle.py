"""End-to-end lifecycle through the real MCP stdio transport and the CLI, fake codex underneath."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from bridge.models import State
from tests.conftest import run, spec

KIT = Path(__file__).parents[2]
TOOLS = {"bridge_health", "bridge_create_task", "bridge_execute_task", "bridge_task_status",
         "bridge_task_result", "bridge_review_task", "bridge_resume_task", "bridge_cancel_task",
         "bridge_list_tasks"}


def _server(repo: Path) -> StdioServerParameters:
    (repo / ".ai").mkdir(exist_ok=True)
    path = repo / ".ai" / "config.json"
    config = json.loads(path.read_text()) if path.exists() else {}
    config.update(retry_backoff_s=0, lock_dir=str(repo.parent / "locks"))
    path.write_text(json.dumps(config))
    return StdioServerParameters(command=sys.executable, args=["-m", "bridge.server"],
                                 env={**os.environ, "BRIDGE_PROJECT_ROOT": str(repo)}, cwd=str(KIT))


async def _call(c: Client, name: str, **args):  # noqa: ANN202
    r = await c.call_tool(name, args)
    if r.is_error:
        return {"error": r.content[0].text}  # type: ignore[union-attr]
    return r.structured_content.get("result", r.structured_content)


# @pt.id KIT-E2E-001-full-lifecycle-over-mcp
# @pt.item Claude task -> Bridge (MCP) -> Codex -> verification -> rework -> review -> DONE
# @pt.condition MCP stdio session; attempt 1 leaves the check failing, rework fixes it
# @pt.expected all 9 tools listed; REWORK then REVIEW via resumed session; approve closes the
#              task as DONE with gates passed; a second approve and a bad path return tool errors
# @pt.target bridge.server (all tools)
# @pt.method e2e
# @pt.legacy none (new kit)
def test_full_lifecycle_over_mcp(repo, fake_codex, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok,ok")
    writes = fake_codex.parent / "writes.txt"
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"@{writes}")

    async def scenario():
        async with Client(_server(repo)) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert TOOLS <= names
            h = await _call(c, "bridge_health")
            assert h["quota"] == {"default": "UNKNOWN"} and h["codex"]["logged_in"]
            bad = await _call(c, "bridge_create_task", objective="x", allowed_paths=["../etc"],
                              acceptance_criteria=["y"])
            assert "traversal" in bad["error"]
            t = await _call(c, "bridge_create_task", **spec())
            tid = t["task_id"]
            assert (await _call(c, "bridge_execute_task", task_id=tid))["state"] == "RUNNING"
            st = await _call(c, "bridge_task_status", task_id=tid, wait_s=30)
            assert st["state"] == "REWORK"  # fake wrote nothing: the bridge's check failed
            writes.write_text("src/app.py=x = 2\n")
            await _call(c, "bridge_resume_task", task_id=tid, feedback="set x = 2")
            st = await _call(c, "bridge_task_status", task_id=tid, wait_s=30)
            assert st["state"] == "REVIEW", st
            res = await _call(c, "bridge_task_result", task_id=tid)
            assert res["attempts"][-1]["verification"]["passed"]
            done = await _call(c, "bridge_review_task", task_id=tid, decision="approve",
                               notes="diff is the one-line change; check passes")
            assert done["state"] == "DONE"
            again = await _call(c, "bridge_review_task", task_id=tid, decision="approve",
                                notes="x")
            assert "error" in again
            listed = await _call(c, "bridge_list_tasks", state="DONE")
            assert [x["task_id"] for x in listed] == [tid]

    run(scenario())
    assert (repo / "src" / "app.py").read_text() == "x = 2\n"


# @pt.id KIT-E2E-002-human-approval-gate
# @pt.item High-risk work cannot be closed by Claude or by a non-interactive process
# @pt.condition risk_level=high task approved by Claude; `codex-bridge approve` run without TTY
# @pt.expected task stays APPROVED with a human-approval hint; CLI approve exits 2 and changes
#              nothing; the interactive human path (human_approve) closes it as DONE
# @pt.target bridge.service.Bridge.review_task, bridge.cli._human_confirm
# @pt.method e2e
# @pt.legacy none (new kit)
def test_high_risk_needs_human(make_bridge, repo, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_bridge()
    t = run(b.create_task(spec(risk_level="high")))["task_id"]
    run(b.execute_task(t, wait=True))
    out = run(b.review_task(t, "approve", "looks right"))
    assert out["state"] == State.APPROVED and "Human approval required" in out["next"]
    assert f"BRIDGE_PROJECT_ROOT={repo}" in out["next"]
    assert f"--project {KIT}" in out["next"]
    assert f"codex-bridge approve {t}" in out["next"]
    _server(repo)
    cli = subprocess.run([sys.executable, "-m", "bridge", "approve", t], cwd=KIT,
                         env={**os.environ, "BRIDGE_PROJECT_ROOT": str(repo)},
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert cli.returncode == 2 and "interactive terminal" in cli.stderr
    assert b.store.get(t)["state"] == State.APPROVED
    assert b.human_approve(t)["state"] == State.DONE
    actors = [(e["actor"], e["to_state"]) for e in b.store.events(t) if e["to_state"] == "DONE"]
    assert actors == [("human", "DONE")]


# @pt.id KIT-E2E-003-sensitive-change-escalates
# @pt.item A low-risk task that touches a sensitive path needs a human
# @pt.condition allowed_paths covers migrations/, codex edits a migration
# @pt.expected Claude's approve stops at APPROVED (needs_human set by the bridge, not the spec)
# @pt.target bridge.review.touches_sensitive
# @pt.method e2e
# @pt.legacy none (new kit)
def test_sensitive_change_escalates(make_bridge, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CODEX_WRITE", "migrations/001.sql=drop table x;")
    b = make_bridge()
    t = run(b.create_task(spec(allowed_paths=["migrations/**"], verification_commands=[])))
    run(b.execute_task(t["task_id"], wait=True))
    out = run(b.review_task(t["task_id"], "approve", "ok"))
    assert out["state"] == State.APPROVED and out["needs_human_approval"]


# @pt.id KIT-E2E-004-gates-before-approval
# @pt.item Claude cannot approve unverified work
# @pt.condition approve on a task still in REWORK; rework decision then resume
# @pt.expected approve refused outside REVIEW; rework decision moves REVIEW -> REWORK and its
#              notes become the default resume feedback
# @pt.target bridge.service.Bridge.review_task, Bridge.resume_task
# @pt.method e2e
# @pt.legacy none (new kit)
def test_review_gates(make_bridge, fake_codex, monkeypatch) -> None:
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))  # no write -> REWORK
    with pytest.raises(ValueError):
        run(b.review_task(t, "approve", "lgtm"))
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    run(b.resume_task(t, wait=True))
    assert run(b.review_task(t, "rework", "also add a comment"))["state"] == State.REWORK
    run(b.resume_task(t, wait=True))
    last = json.loads((fake_codex / "log.jsonl").read_text().splitlines()[-1])
    assert "also add a comment" in last["prompt"]
