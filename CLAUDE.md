# Agent orchestration kit

A project-local MCP server, `agent-bridge` (`.mcp.json`), runs a configurable multi-agent
workflow and owns task state (in the git directory, never the worktree). Roles are not agents:
`.ai/orchestrator.yaml` maps **planner**, **executor** and **reviewer** to agents, each agent to
an adapter (Codex CLI, Claude CLI, or `client` = you, the MCP caller) and each agent to one
isolated account. Without that file the legacy setup applies: you plan and review, Codex
executes. `bridge_list_agents` shows the active mapping. A **human** owns accounts, high-risk
approvals and anything irreversible.

## Hard rules

1. **Delegate implementation.** Production code, tests and scripts are written by the executor
   role through `bridge_create_task` → `bridge_execute_task` (or `bridge_plan` →
   `bridge_run_workflow` when a planner agent is configured). Claude edits code directly only to
   repair the bridge itself when delegation is broken, and says so. The user can authorise
   another mode.
2. **Review before approval.** When you are the reviewer, never call
   `bridge_review_task(approve)` without reading `bridge_task_result`: the patch, the bridge's own
   verification output and scope violations. An agent's self-report is not evidence. A reviewer
   agent's decision is recorded as such; you may override it only after reading the evidence.
3. **Human-only actions.** Claude never runs `agent-bridge approve`, `reassign` or
   `account-login`, never edits `.ai/`, never runs `codex login`/`logout`, `claude auth
   login`/`logout` or `codex-switch` commands that change accounts. An LLM statement is not
   human approval.
4. **Project isolation.** Never modify `~/.claude`, `~/.codex`, `~/.codex-accounts`, any
   configured account home or global MCP configuration. Everything this kit needs lives in the
   repository.
5. **Bounded work.** At most `max_rework + 1` runs per task (default 3); a task that keeps
   failing goes to BLOCKED with a diagnosis for the user. Do not resume a task PAUSED for AUTH
   until the user says it is resolved. A task PAUSED for QUOTA waits for the user too, unless
   the user enabled quota fallback (`routing.quota_fallback` in `orchestrator.yaml`, or
   `auto_switch` in the legacy `config.json`): then the bridge (never Claude) moves it to the
   next eligible agent. Claude never edits that config or picks accounts itself.

Details: `.claude/rules/` (loaded automatically). Workflow: the `agent-orchestration` skill.

## Commands

```bash
uv sync                           # install (Python 3.12, project-local .venv)
scripts/check.sh                  # tests; --with-health also checks the agent CLIs
uv run agent-bridge config-check  # validate .ai/orchestrator.yaml (or the legacy config)
uv run agent-bridge agents        # agents, roles and availability
uv run agent-bridge health        # adapters, quota per account, task counts
uv run agent-bridge account-list  # accounts, login state, codex-switch homes/profiles
```
