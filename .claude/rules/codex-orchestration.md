# Orchestration: task lifecycle

States: `BACKLOG → READY → RUNNING → VERIFYING → REVIEW → APPROVED → DONE`, plus `REWORK`,
`FAILED`, `PAUSED`, `BLOCKED`, `CANCELLED`. Terminal: `DONE`, `CANCELLED`, `BLOCKED`.

| Move | Who | Trigger |
|---|---|---|
| BACKLOG → READY | bridge | every `depends_on` task is DONE |
| READY / REWORK / FAILED / PAUSED → RUNNING | Claude | `bridge_execute_task` / `bridge_resume_task` |
| RUNNING → VERIFYING | bridge | Codex exited cleanly |
| RUNNING → FAILED | bridge | timeout or crash (BLOCKED when the attempt budget is spent) |
| RUNNING → PAUSED | bridge | quota, auth, network, missing CLI, interrupted runner |
| VERIFYING → REVIEW | bridge | scope clean, every verification command passed, Codex status `completed` |
| VERIFYING → REWORK | bridge | any check failed (BLOCKED when the budget is spent) |
| REVIEW → APPROVED / REWORK / BLOCKED | Claude | `bridge_review_task` |
| APPROVED → DONE | bridge, or human for high risk | quality gates re-checked |
| any non-terminal → CANCELLED | Claude | `bridge_cancel_task` |

- The bridge, not Claude, decides REVIEW vs REWORK: it re-runs the verification commands itself.
- `risk_level: high`, or a change under a sensitive path (migrations, auth, deploy, CI, secrets),
  stops at APPROVED until a human runs `codex-bridge approve <id>` in a terminal.
- Environment failures (PAUSED) do not consume the attempt budget; code failures do.
- A runner that dies mid-run is detected on the next bridge start: the task becomes PAUSED
  (`INTERRUPTED`) and keeps its Codex session id, so `bridge_resume_task` continues it.
- One writer per git worktree; tasks on the same account never run at the same time.
  Parallel writes need separate repositories or worktrees, each with its own bridge state.
