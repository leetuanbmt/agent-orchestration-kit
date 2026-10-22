# Orchestration: roles, routing and task lifecycle

Roles map to agents in `.ai/orchestrator.yaml` (legacy without it: `client` plans and reviews,
one Codex agent per `config.json` account executes). Each agent has one adapter and one account;
an account has its own auth home and `max_concurrency`.

- **Routing.** A task pinned with `agent` (or `account`) runs there. An unpinned task gets the
  first eligible executor in role order when it first starts: enabled, CLI installed, can write,
  account available and under its concurrency limit. It then keeps that agent and session.
  `strategy: explicit` requires every task to name its agent.
- **Stages.** `plan` (planner agent), `execute`, `test` (the bridge re-runs the verification
  commands), `review` (reviewer). Without `review`, the bridge approves work that passed every
  gate; without `test`, only the scope check gates it.
- **Reviewer.** A `client` reviewer is you (`bridge_review_task`). A reviewer agent runs
  read-only after verification and its decision is applied as actor `agent`; if it fails, the
  task stays in REVIEW and you decide.

States: `BACKLOG → READY → RUNNING → VERIFYING → REVIEW → APPROVED → DONE`, plus `REWORK`,
`FAILED`, `PAUSED`, `BLOCKED`, `CANCELLED`. Terminal: `DONE`, `CANCELLED`, `BLOCKED`.

| Move | Who | Trigger |
|---|---|---|
| BACKLOG → READY | bridge | every `depends_on` task is DONE |
| READY / REWORK / FAILED / PAUSED → RUNNING | client (or the workflow driver) | `bridge_execute_task` / `bridge_resume_task` / `bridge_run_workflow` |
| RUNNING → VERIFYING | bridge | the agent exited cleanly |
| RUNNING → FAILED | bridge | timeout or crash (BLOCKED when the attempt budget is spent) |
| RUNNING → PAUSED | bridge | quota, auth, network, missing CLI, interrupted runner |
| VERIFYING → REVIEW | bridge | scope clean, every verification command passed, agent status `completed` |
| VERIFYING → REWORK | bridge | any check failed (BLOCKED when the budget is spent) |
| REVIEW → APPROVED / REWORK / BLOCKED | client or reviewer agent | `bridge_review_task` / reviewer run |
| REVIEW → APPROVED | bridge | no review stage and every gate passed |
| APPROVED → DONE | bridge, or human for high risk | quality gates re-checked |
| any non-terminal → CANCELLED | client | `bridge_cancel_task` |

- The bridge, not any agent, decides REVIEW vs REWORK: it re-runs the verification commands.
- `risk_level: high`, or a change under a sensitive path (migrations, auth, deploy, CI, secrets),
  stops at APPROVED until a human runs `agent-bridge approve <id>` in a terminal.
- Environment failures (PAUSED) do not consume the attempt budget; code failures do.
- A runner that dies mid-run is detected on the next bridge start: the task becomes PAUSED
  (`INTERRUPTED`) and keeps its session id, so `bridge_resume_task` continues it.
- One writer per git worktree; an account runs at most `max_concurrency` agents at once.
  Parallel writes need separate worktrees, each with its own bridge state.
- Workflows (`bridge_plan`): PLANNING → READY (or FAILED, with nothing created) →
  RUNNING → DONE, INCOMPLETE (some task BLOCKED or CANCELLED) or WAITING (a review, a human
  approval or an account needs attention). Run `bridge_run_workflow` again after resolving.
