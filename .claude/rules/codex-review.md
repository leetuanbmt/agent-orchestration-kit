# Review: deciding on Codex output

Read `bridge_task_result` for the latest attempt, in this order:
1. `verification.scope_violations` and `changes.head_changed`: any entry is an automatic
   rework or block; never approve around it.
2. `verification.checks`: the bridge's own runs. A pass claimed only in Codex's `final` is not
   a pass.
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

At most two rework rounds per task (the attempt budget enforces it). Review only the task's
diff, not the whole repository, unless the diff shows a reason to look wider.
