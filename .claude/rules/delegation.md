# Delegation and review

## Writing a task

One task = one reviewable change with objectively checkable completion. Split anything that
would touch unrelated areas, exceed ~400 changed lines, or need more than one decision.

Every `bridge_create_task` call must give:
- `objective`: the outcome in one or two sentences.
- `context`: facts the executor cannot discover cheaply: relevant files and symbols,
  conventions, verified tool behaviour, constraints (no network in the sandbox, no new
  dependencies unless the task says so, how to run tests inside the sandbox).
- `scope`: the design to implement, interfaces other tasks rely on, what not to change.
- `allowed_paths`: the narrowest project-relative paths/globs (`*` stays in one directory,
  `**` crosses directories). Protected files (`.env*`, keys, `.git/`, `.ai/`, `.mcp.json`,
  `.claude/settings*.json`) can never be allowed.
- `excluded_paths`: files that judge the work (security checks, gates) unless the task is
  about them.
- `acceptance_criteria`: observable outcomes, including which tests must exist and the
  project's test-metadata convention.
- `verification_commands`: argv strings without shell syntax, executables from
  `verify_allowlist`; prefer the project's real test command. These are the gate.
- `depends_on` for ordering; `risk_level` (`high` for auth, secrets, money, data migrations,
  deployment, publishing); `agent` or `account` only when the user chose one.

A planner agent (`bridge_plan`) produces the same fields; the bridge validates every planned
task before creating any.

Execution: `bridge_execute_task`, then `bridge_task_status(wait_s=55)` in a loop (or
`bridge_workflow_status(wait_s=55)` for a workflow). Do not poll faster and do not read the
result before the task leaves RUNNING/VERIFYING.

Do not edit files a running task may touch: the bridge attributes every change in the worktree
during a run to the executor.

Before creating a task, check the spec against itself: no criterion or scope item may need a
change to a protected path or a path outside `allowed_paths`. When a task must be rebuilt,
rewrite the whole spec rather than patching one field, so stale instructions do not survive.
Tell the executor not to set `TMPDIR` and not to leave scratch files: any stray file is a scope
violation.

## Reviewing (when you are the reviewer, or overriding a reviewer agent)

Read `bridge_task_result` for the latest attempt, in this order:
1. `verification.scope_violations` and `changes.head_changed`: any entry is an automatic
   rework or block; never approve around it.
2. `verification.checks`: the bridge's own runs. A pass claimed only in the agent's `final` is
   not a pass.
3. `changes.stat` then `changes.patch`: the actual diff. Read every hunk of files that enforce
   rules (security, permissions, state transitions, quality gates, money paths); skim the rest
   against the acceptance criteria. If the patch is truncated, read the files directly.
4. Acceptance criteria one by one, plus consistency with the surrounding code and no weakened
   checks, no silenced tests, no secrets in code or logs.

Decide:
- `approve` only when every criterion is met with evidence. Notes say what was verified.
- `rework` with targeted, actionable notes (file, behaviour, expected result). The notes become
  the next prompt; do not restate the whole task.
- `block` when the spec is wrong or the task needs a user decision; tell the user why.

At most `max_rework` rework rounds per task (the attempt budget enforces it). Review only the
task's diff, not the whole repository, unless the diff shows a reason to look wider.
