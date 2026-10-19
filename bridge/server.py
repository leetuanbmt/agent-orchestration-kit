"""MCP stdio server exposing the bridge to Claude Code. Launched per project via `.mcp.json`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .service import Bridge

INSTRUCTIONS = (
    "Claude<->Codex bridge for THIS project. Claude plans and reviews; Codex implements. "
    "Flow: bridge_create_task -> bridge_execute_task -> bridge_task_status(wait_s) -> "
    "bridge_task_result -> bridge_review_task. Never approve without reading the evidence. "
    "Human-only approvals cannot be granted through this server."
)

mcp = MCPServer("codex-bridge", instructions=INSTRUCTIONS)
_bridge: Bridge | None = None


async def _safe(coro: Awaitable[Any]) -> Any:
    """Expected refusals reach Claude with their reason; real crashes stay generic."""
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
    """List accounts and profiles; quota comes from the latest Codex rollout or is UNKNOWN."""
    async def listing() -> dict[str, Any]:
        b = bridge()
        return b.list_accounts()

    return await _safe(listing())


@mcp.tool()
async def bridge_health() -> dict[str, Any]:
    """CLI status and tasks; quota comes from the latest Codex session rollout or is UNKNOWN."""
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
    account: str | None = None,
) -> dict[str, Any]:
    """Create a task. allowed_paths are project-relative paths/globs Codex may change.
    verification_commands are argv strings (no shell) the bridge itself re-runs after Codex.
    risk_level=high forces human approval before DONE. Identical live specs are deduplicated."""
    spec = {k: v for k, v in dict(
        objective=objective, allowed_paths=allowed_paths, acceptance_criteria=acceptance_criteria,
        context=context, scope=scope, excluded_paths=excluded_paths or [],
        depends_on=depends_on or [], verification_commands=verification_commands or [],
        risk_level=risk_level, timeout_s=timeout_s, max_attempts=max_attempts, sandbox=sandbox, account=account,
    ).items() if v is not None}
    return await _safe(bridge().create_task(spec))


@mcp.tool()
async def bridge_execute_task(task_id: str) -> dict[str, Any]:
    """Start Codex on a READY task in the background. Poll with bridge_task_status(wait_s).
    auto_switch may move the task to an eligible fallback account before it starts."""
    return await _safe(bridge().execute_task(task_id))


@mcp.tool()
async def bridge_task_status(task_id: str, wait_s: float = 0) -> dict[str, Any]:
    """Compact status. wait_s (<=55) blocks until the task leaves RUNNING/VERIFYING."""
    return await _safe(bridge().task_status(task_id, wait_s))


@mcp.tool()
async def bridge_task_result(task_id: str) -> dict[str, Any]:
    """Full evidence: spec, every attempt (Codex final JSON, changed files, bridge verification
    output, scope violations), and review history."""
    return await _safe(bridge().task_result(task_id))


@mcp.tool()
async def bridge_review_task(
    task_id: str, decision: Literal["approve", "rework", "block"], notes: str
) -> dict[str, Any]:
    """Claude's review of a task in REVIEW. approve closes it (DONE) unless human approval is
    required; rework sends it back (then bridge_resume_task); block stops it."""
    return await _safe(bridge().review_task(task_id, decision, notes))


@mcp.tool()
async def bridge_resume_task(task_id: str, feedback: str = "") -> dict[str, Any]:
    """Re-run a REWORK/FAILED/PAUSED task, continuing the Codex session when one exists.
    Do not resume a task PAUSED for QUOTA until the user says so, unless auto_switch is
    enabled, in which case the bridge moves it to an eligible fallback account.
    Do not resume a task PAUSED for AUTH until the user says it is resolved."""
    return await _safe(bridge().resume_task(task_id, feedback))


@mcp.tool()
async def bridge_cancel_task(task_id: str, reason: str) -> dict[str, Any]:
    """Cancel a non-terminal task; a running Codex process group is killed."""
    return await _safe(bridge().cancel_task(task_id, reason))


@mcp.tool()
async def bridge_list_tasks(state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List tasks (newest first), optionally filtered by state."""
    return await _safe(bridge().list_tasks(state, limit))


async def _serve() -> None:
    try:
        await mcp.run_stdio_async()
    finally:
        if _bridge is not None:
            await _bridge.shutdown()


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
