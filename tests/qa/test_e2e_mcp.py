"""E2E: the MCP server exactly as Claude Code starts it from .mcp.json, over stdio."""

from __future__ import annotations

import asyncio
import json
import subprocess
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.qa.conftest import BIN, Sandbox

DOCUMENTED = {
    "bridge_health", "bridge_list_agents", "bridge_list_accounts", "bridge_account_usage",
    "bridge_create_task", "bridge_execute_task", "bridge_task_status", "bridge_task_result",
    "bridge_review_task", "bridge_resume_task", "bridge_cancel_task", "bridge_list_tasks",
    "bridge_plan", "bridge_workflow_status", "bridge_run_workflow",
}
SPEC = {"objective": "Set x to 2 in src/app.py", "allowed_paths": ["src/**"],
        "acceptance_criteria": ["src/app.py contains x = 2"]}


@asynccontextmanager
async def session(box: Sandbox, extra: dict[str, str] | None = None):
    server = json.loads((box.root / ".mcp.json").read_text())["mcpServers"]["agent-bridge"]
    assert server["command"] == "agent-bridge" and server["args"] == ["serve"]
    params = StdioServerParameters(command=str(BIN / "agent-bridge"), args=server["args"],
                                   cwd=box.root,
                                   env={**box.env, "BRIDGE_PROJECT_ROOT": str(box.root),
                                        **(extra or {})})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        yield client


async def call(client: ClientSession, name: str, **args: Any) -> tuple[bool, Any]:
    res = await client.call_tool(name, args)
    if res.structured_content is not None and not res.is_error:
        data = res.structured_content
        return False, data.get("result", data) if set(data) == {"result"} else data
    text = res.content[0].text if res.content else ""
    try:
        data = json.loads(text)
    except ValueError:
        data = text
    return bool(res.is_error), data


# @pt.id QA-E2E-060-mcp-tools
# @pt.item The MCP surface Claude Code sees
# @pt.condition the server started from the project's .mcp.json
# @pt.expected exactly the 15 documented tools; none can approve, reassign or manage accounts
# @pt.target bridge.server
# @pt.method e2e
# @pt.legacy none
def test_mcp_tool_surface(sandbox: Sandbox) -> None:
    async def main() -> set[str]:
        async with session(sandbox) as client:
            return {t.name for t in (await client.list_tools()).tools}
    names = asyncio.run(main())
    assert names == DOCUMENTED
    assert not any(word in n for n in names for word in ("approve", "reassign", "login", "account_add"))


# @pt.id QA-E2E-061-mcp-lifecycle
# @pt.item A full task through MCP, as the client role does it
# @pt.condition create, execute (background), status with wait, result, review approve
# @pt.expected REVIEW after the wait with the patch and checks in the result; DONE after approve; the CLI sees the same task
# @pt.target bridge.server tools
# @pt.method e2e
# @pt.legacy none
def test_mcp_lifecycle(sandbox: Sandbox) -> None:
    sandbox.env["FAKE_CODEX_WRITE"] = "src/app.py=x = 2\n"

    async def main() -> str:
        async with session(sandbox) as client:
            err, created = await call(client, "bridge_create_task", **SPEC)
            assert not err, created
            task = created["task_id"]
            err, started = await call(client, "bridge_execute_task", task_id=task)
            assert not err and started["state"] in {"RUNNING", "VERIFYING", "REVIEW"}, started
            err, status = await call(client, "bridge_task_status", task_id=task, wait_s=20)
            assert status["state"] == "REVIEW", status
            err, result = await call(client, "bridge_task_result", task_id=task)
            assert "+x = 2" in result["attempts"][-1]["changes"]["patch"]
            err, done = await call(client, "bridge_review_task", task_id=task,
                                   decision="approve", notes="read the patch")
            assert not err and done["state"] == "DONE", done
            return task
    task = asyncio.run(main())
    assert sandbox.ok("status", task)["state"] == "DONE"


# @pt.id QA-E2E-062-mcp-errors
# @pt.item Bad requests through MCP
# @pt.condition a protected path, an unknown task, an invalid review decision, a high-risk task approved by the client
# @pt.expected each bad call is an error result with a readable message and no task is created; the high-risk task stays APPROVED waiting for a human
# @pt.target bridge.server tools
# @pt.method e2e
# @pt.legacy none
def test_mcp_errors_and_human_gate(sandbox: Sandbox) -> None:
    async def main() -> None:
        async with session(sandbox) as client:
            err, out = await call(client, "bridge_create_task", **{**SPEC, "allowed_paths": [".env"]})
            assert err and "protected" in str(out), out
            err, out = await call(client, "bridge_task_status", task_id="T9999")
            assert err and "T9999" in str(out), out
            err, listed = await call(client, "bridge_list_tasks")
            assert listed == [], listed
            err, created = await call(client, "bridge_create_task", **SPEC, risk_level="high")
            task = created["task_id"]
            await call(client, "bridge_execute_task", task_id=task)
            await call(client, "bridge_task_status", task_id=task, wait_s=20)
            err, out = await call(client, "bridge_review_task", task_id=task,
                                  decision="ship-it", notes="x")
            assert err, out
            err, out = await call(client, "bridge_review_task", task_id=task,
                                  decision="approve", notes="ok")
            assert out["state"] == "APPROVED" and out["needs_human_approval"], out
    asyncio.run(main())


# @pt.id QA-E2E-063-mcp-and-cli-together
# @pt.item The MCP server and a terminal share one project
# @pt.condition the server is running; the user creates and runs a task from the CLI meanwhile
# @pt.expected the MCP list and status show the CLI's task and its REVIEW state without restarting the server
# @pt.target bridge.server with bridge.cli on one store
# @pt.method e2e
# @pt.legacy none
def test_mcp_sees_cli_work(sandbox: Sandbox) -> None:
    async def main() -> None:
        async with session(sandbox) as client:
            task = sandbox.create("From the terminal")
            sandbox.ok("execute", task)
            err, status = await call(client, "bridge_task_status", task_id=task)
            assert not err and status["state"] == "REVIEW", status
            err, listed = await call(client, "bridge_list_tasks")
            assert [t["task_id"] for t in listed] == [task]
    asyncio.run(main())


# @pt.id QA-E2E-064-no-recursion
# @pt.item An agent the bridge started cannot start the bridge
# @pt.condition AGENT_BRIDGE_AGENT=1 in the environment, as every agent process gets
# @pt.expected `serve` and other CLI commands refuse to run and exit non-zero
# @pt.target bridge.cli guard
# @pt.method e2e
# @pt.legacy none
def test_refuses_inside_agent(sandbox: Sandbox) -> None:
    proc = subprocess.run([str(BIN / "agent-bridge"), "serve"], cwd=sandbox.root,
                          env={**sandbox.env, "AGENT_BRIDGE_AGENT": "1"}, input="",
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
    res = sandbox.cli("list", env={"AGENT_BRIDGE_AGENT": "1"})
    assert res.code != 0 and res.err.strip()
