"""MCP stdio server exposing the bridge to the MCP client. Launched per project via `.mcp.json`."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .executor import AGENT_ENV
from .service import Bridge

INSTRUCTIONS = (
    "Multi-agent orchestration bridge for THIS project. Roles (planner, executor, reviewer) map "
    "to agents configured in .ai/orchestrator.yaml; bridge_list_agents shows them. "
    "Task flow: bridge_create_task -> bridge_execute_task -> bridge_task_status(wait_s) -> "
    "bridge_task_result -> bridge_review_task (when you are the reviewer). Planner-agent flow: "
    "bridge_plan -> bridge_workflow_status(wait_s) -> bridge_run_workflow. Never approve without "
    "reading the evidence. Human-only approvals cannot be granted through this server."
)

mcp = MCPServer("agent-bridge", instructions=INSTRUCTIONS)
_bridge: Bridge | None = None


async def _safe(coro: Awaitable[Any]) -> Any:
    """Expected refusals reach the client with their reason; real crashes stay generic."""
    try:
        return await coro
    except (ValueError, KeyError, RuntimeError) as e:  # validation, transition, not found, conflict
        raise ToolError(str(e).strip("'\"")) from e


def bridge() -> Bridge:
    global _bridge
    if _bridge is None:
        _bridge = Bridge()
        _bridge.recover()
    return _bridge


@mcp.tool()
async def bridge_list_accounts() -> dict[str, Any]:
    """List accounts, login state and quota (Codex rollouts or the last run's reported limits;
    otherwise UNKNOWN)."""
    async def listing() -> dict[str, Any]:
        b = bridge()
        return b.list_accounts()

    return await _safe(listing())


@mcp.tool()
async def bridge_list_agents() -> dict[str, Any]:
    """Configured agents (adapter, provider, account, model, read/write), their roles, whether
    each can take work now, and the workflow stages."""
    async def listing() -> dict[str, Any]:
        return bridge().list_agents()

    return await _safe(listing())


@mcp.tool()
async def bridge_health() -> dict[str, Any]:
    """Adapter CLIs, quota per account, task counts and the orchestrator source."""
    return await _safe(bridge().health())


@mcp.tool()
async def bridge_create_task(
    objective: str,
    allowed_paths: list[str],
    acceptance_criteria: list[str],
    context: str = "",
    scope: str = "",
    excluded_paths: list[str] | None = None,
    depends_on: list[str] | None = None,
    verification_commands: list[str] | None = None,
    risk_level: Literal["low", "medium", "high"] = "low",
    timeout_s: int | None = None,
    max_attempts: int | None = None,
    sandbox: Literal["read-only", "workspace-write"] | None = None,
    agent: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Create a task. allowed_paths are project-relative paths/globs the executor may change.
    verification_commands are argv strings (no shell) the bridge itself re-runs afterwards.
    agent pins an executor agent; account pins the first executor on that account; with
    neither, the first eligible executor is chosen at start. risk_level=high forces human
    approval before DONE. Identical live specs are deduplicated."""
    spec = {k: v for k, v in dict(
        objective=objective, allowed_paths=allowed_paths, acceptance_criteria=acceptance_criteria,
        context=context, scope=scope, excluded_paths=excluded_paths or [],
        depends_on=depends_on or [], verification_commands=verification_commands or [],
        risk_level=risk_level, timeout_s=timeout_s, max_attempts=max_attempts, sandbox=sandbox,
        agent=agent, account=account,
    ).items() if v is not None}
    return await _safe(bridge().create_task(spec))


@mcp.tool()
async def bridge_execute_task(task_id: str) -> dict[str, Any]:
    """Start the executor agent on a READY task in the background. Poll with
    bridge_task_status(wait_s). An enabled quota fallback may move the task to another agent
    before it starts."""
    return await _safe(bridge().execute_task(task_id))


@mcp.tool()
async def bridge_task_status(task_id: str, wait_s: float = 0) -> dict[str, Any]:
    """Compact status. wait_s (<=55) blocks until the task leaves RUNNING/VERIFYING and any
    reviewer agent has decided (review_pending false)."""
    return await _safe(bridge().task_status(task_id, wait_s))


@mcp.tool()
async def bridge_task_result(task_id: str) -> dict[str, Any]:
    """Full evidence: spec, every attempt (agent final JSON, changed files, bridge verification
    output, scope violations), and review history (client, reviewer agent or bridge)."""
    return await _safe(bridge().task_result(task_id))


@mcp.tool()
async def bridge_review_task(
    task_id: str, decision: Literal["approve", "rework", "block"], notes: str
) -> dict[str, Any]:
    """The client's review of a task in REVIEW (also the override when a reviewer agent
    failed). approve closes it (DONE) unless human approval is required; rework sends it back
    (then bridge_resume_task); block stops it."""
    return await _safe(bridge().review_task(task_id, decision, notes))


@mcp.tool()
async def bridge_resume_task(task_id: str, feedback: str = "") -> dict[str, Any]:
    """Re-run a REWORK/FAILED/PAUSED task, continuing the agent session when one exists.
    Do not resume a task PAUSED for QUOTA until the user says so, unless quota fallback is
    enabled, in which case the bridge moves it to an eligible fallback agent.
    Do not resume a task PAUSED for AUTH until the user says it is resolved."""
    return await _safe(bridge().resume_task(task_id, feedback))


@mcp.tool()
async def bridge_cancel_task(task_id: str, reason: str) -> dict[str, Any]:
    """Cancel a non-terminal task; a running agent process group is killed."""
    return await _safe(bridge().cancel_task(task_id, reason))


@mcp.tool()
async def bridge_list_tasks(state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List tasks (newest first), optionally filtered by state."""
    return await _safe(bridge().list_tasks(state, limit))


@mcp.tool()
async def bridge_plan(objective: str, context: str = "") -> dict[str, Any]:
    """Ask the planner agent to split an objective into tasks (needs the plan stage and a
    planner that is not the client). Returns a workflow in PLANNING; poll
    bridge_workflow_status, then bridge_run_workflow."""
    return await _safe(bridge().plan(objective, context))


@mcp.tool()
async def bridge_workflow_status(workflow_id: str, wait_s: float = 0) -> dict[str, Any]:
    """Workflow state and its tasks. wait_s (<=55) blocks while it is PLANNING or RUNNING."""
    return await _safe(bridge().workflow_status(workflow_id, wait_s))


@mcp.tool()
async def bridge_run_workflow(workflow_id: str) -> dict[str, Any]:
    """Drive a planned workflow in the background: execute, test and review each task in
    dependency order until it is DONE, INCOMPLETE or WAITING for a decision."""
    return await _safe(bridge().run_workflow(workflow_id))


async def _serve() -> None:
    try:
        await mcp.run_stdio_async()
    finally:
        if _bridge is not None:
            await _bridge.shutdown()


def main() -> None:
    if os.environ.get(AGENT_ENV):
        sys.exit("refused: the bridge cannot run inside an agent it started")
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
