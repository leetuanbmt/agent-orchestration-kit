# Delegation: writing a task

One task = one reviewable change with objectively checkable completion. Split anything that
would touch unrelated areas, exceed ~400 changed lines, or need more than one decision.

Every `bridge_create_task` call must give:
- `objective`: the outcome in one or two sentences.
- `context`: facts Codex cannot discover cheaply: relevant files and symbols, conventions,
  verified tool behaviour, constraints (no network in the sandbox, no new dependencies unless
  the task says so, how to run tests inside the sandbox).
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
  deployment, publishing); `account` only when the user chose one.

Execution: `bridge_execute_task`, then `bridge_task_status(wait_s=55)` in a loop. Do not poll
faster and do not read the result before the task leaves RUNNING/VERIFYING.

Do not edit files a running task may touch: the bridge attributes every change in the
worktree during a run to Codex.

Before creating a task, check the spec against itself: no criterion or scope item may need a
change to a protected path or a path outside `allowed_paths`. When a task must be rebuilt,
rewrite the whole spec rather than patching one field, so stale instructions do not survive.
Tell Codex not to set `TMPDIR` and not to leave scratch files: any stray file is a scope
violation.
