---
name: agent-orchestration
description: Plan a requirement, hand it to the configured executor agent through the project-local agent-bridge MCP server, monitor, verify, review and report. Use for any implementation, test-writing or fix work in a project that has the agent-bridge server in .mcp.json.
---

# Agent orchestration

Roles (planner, executor, reviewer) map to agents in `.ai/orchestrator.yaml`; you are the
`client` agent. Rules: `.claude/rules/orchestration.md`, `delegation.md`, `security.md`. Follow
the steps in order; skip none.

## 1. Analyse
Read the requirement and only the code it touches. Name the acceptance test for "done".
If two readings produce different work, ask the user before planning.

## 2. Check the bridge
`bridge_health` once per session: `ok: false` → stop and tell the user (a CLI missing or not
logged in). `bridge_list_agents` once: note who plans, executes and reviews, and the stages.
`bridge_list_accounts` only when the user asked about accounts or quota.

## 3. Plan and decompose
- Planner is `client` (or no plan stage): split the work yourself per
  `.claude/rules/delegation.md` and create each task with `bridge_create_task`.
- Planner is an agent: `bridge_plan(objective, context)`, then
  `bridge_workflow_status(workflow_id, wait_s=55)` until it leaves PLANNING. FAILED → read
  `detail.error`, fix the objective or context, plan again. READY → read every planned task.
Show the user the task list when there is more than one task or any high-risk task.

## 4. Execute
- Single tasks: `bridge_execute_task` on READY tasks (a `duplicate: true` reply means it already
  exists: reuse it). Only one writing task per worktree runs at a time.
- Workflow: `bridge_run_workflow(workflow_id)`; it executes, tests, has the reviewer agent
  review, and reworks within budget, task by task in dependency order.

## 5. Monitor
`bridge_task_status(task_id, wait_s=55)` (or `bridge_workflow_status`) until nothing is
RUNNING/VERIFYING and `review_pending` is false (a reviewer agent is still deciding).
- PAUSED QUOTA → if `bridge_list_agents` shows quota fallback is configured (read the config;
  never edit it), `bridge_resume_task` once: the bridge moves the task to a fallback agent or
  logs `auto_switch_skipped`. Otherwise, or if it pauses again, stop, report, wait for the user.
- PAUSED AUTH/MISSING_BINARY → stop, report the reason, wait for the user.
- PAUSED NETWORK/INTERRUPTED → `bridge_resume_task` once; if it pauses again, report.
- FAILED (timeout/crash) → read the stderr tail in `bridge_task_result`, then resume with
  feedback or block.
- Workflow WAITING → `detail.waiting` and the task states say what needs attention.

## 6. Inspect and verify
`bridge_task_result`: scope violations, bridge checks, stat, patch, the agent's final notes and
the review history (`reviewer` names the agent, or `bridge` for gates-only workflows). Re-run
nothing by hand that the bridge already ran; read the patch.

## 7. Review
- You are the reviewer: `bridge_review_task` per `.claude/rules/delegation.md`.
- A reviewer agent decided: check its notes against the evidence before you report the task as
  done. A task left in REVIEW after `agent_review_failed` is yours to review.

## 8. Rework
`bridge_resume_task(task_id)` uses the latest review notes as feedback and continues the same
session on the same agent. `max_rework` rounds maximum; then block and report.

## 9. Complete
Approved low/medium-risk tasks close as DONE automatically. APPROVED high-risk tasks: give the
user the exact approve command from the review response; never run it yourself.

## 10. Report
Per task: id, agent, final state, files changed, verification result, who reviewed, open risks.
State plainly what was not verified (for example a real-account run that needs a human login).
