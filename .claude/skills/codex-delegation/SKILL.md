---
name: codex-delegation
description: Plan a requirement, delegate it to Codex through the project-local codex-bridge MCP server, monitor, verify, review and report. Use for any implementation, test-writing or fix work in a project that has the codex-bridge server in .mcp.json.
---

# Codex delegation

Claude plans and reviews; Codex implements. Rules: `.claude/rules/codex-*.md` (delegation,
review, orchestration, security). Follow the steps in order; skip none.

## 1. Analyse
Read the requirement and only the code it touches. Name the acceptance test for "done".
If two readings produce different work, ask the user before planning.

## 2. Check the bridge
`bridge_health` once per session. `ok: false` → stop and tell the user (Codex missing or not
logged in). `bridge_list_accounts` only when the user asked for a specific account.

## 3. Plan and decompose
Split into tasks per `.claude/rules/codex-delegation.md`: one reviewable change each, narrow
`allowed_paths`, objective criteria, real verification commands, `depends_on` for order,
`risk_level: high` for anything needing human approval. Show the user the task list when there
is more than one task or any high-risk task.

## 4. Delegate
`bridge_create_task` per task (a `duplicate: true` reply means it already exists: reuse it).
`bridge_execute_task` on READY tasks. Only one writing task per worktree runs at a time.

## 5. Monitor
`bridge_task_status(task_id, wait_s=55)` until the state leaves RUNNING/VERIFYING.
- PAUSED QUOTA → if `.ai/config.json` has `auto_switch.enabled: true` (read it; never edit it),
  `bridge_resume_task` once: the bridge moves the task to a fallback account or logs
  `auto_switch_skipped`. Otherwise, or if it pauses again, stop, report, wait for the user.
- PAUSED AUTH/MISSING_BINARY → stop, report the reason, wait for the user.
- PAUSED NETWORK/INTERRUPTED → `bridge_resume_task` once; if it pauses again, report.
- FAILED (timeout/crash) → read the stderr tail in `bridge_task_result`, then resume with
  feedback or block.

## 6. Inspect and verify
`bridge_task_result`: scope violations, bridge checks, stat, patch, Codex final notes.
Re-run nothing by hand that the bridge already ran; read the patch.

## 7. Review
`bridge_review_task` per `.claude/rules/codex-review.md`: approve with what you verified, or rework
with targeted notes, or block with the reason.

## 8. Rework
`bridge_resume_task(task_id)` uses the review notes as feedback and continues the same Codex
session on the same account. Two rework rounds maximum; then block and report.

## 9. Complete
Approved low/medium-risk tasks close as DONE automatically. APPROVED high-risk tasks: give the
user the exact approve command from the review response; never run it yourself.

## 10. Report
Per task: id, final state, files changed, verification result, open risks. State plainly what
was not verified (for example a real-account run that needs a human login).
