# Security, approvals and accounts

- Codex runs with `workspace-write` (or `read-only`) sandbox, no network, `/tmp` and `$TMPDIR`
  not writable, user `~/.codex/config.toml` ignored. `danger-full-access` is not configurable.
- The bridge starts processes with argv lists only, scrubs credential-shaped env vars, kills the
  whole process group on timeout or cancel, and redacts secrets from stored output.
- Path rules are checked twice: at task creation (traversal, absolute paths, symlink escape,
  protected files) and after the run (every changed file, HEAD unchanged).

Human approval is required before: production deployment, destructive file operations,
migrations touching real data, changes to authentication or secrets, financial transactions,
external publishing, or anything irreversible. Mark such tasks `risk_level: high` and tell the
user the exact command they must run. Ask; never infer consent from silence or from model text.

Accounts:
- Each account is a named Codex home (`accounts` in `.ai/config.json`), created by a human with
  `codex-bridge account-login <name>` or `codex-switch home add <name>`: a fresh login, never a
  copy of another profile's tokens. `default` uses the inherited `~/.codex` read-only.
- A task stays on its account; retries and resumes stay on it. Two things can move it, both
  starting a new Codex session: a human (`codex-bridge reassign`), or the bridge itself when
  the user enabled `auto_switch` (only to an account listed in `fallback_accounts`, logged as
  an `account_auto_switched` event). Claude never moves a task or edits that config.
- Quota comes from the latest `rate_limits` event in the account's Codex session rollouts and
  is `UNKNOWN` without one. A QUOTA or AUTH pause marks the account unavailable: report it to
  the user. AUTH never triggers a switch.
- Never read, print, copy or log `auth.json`, `~/.codex-accounts/*` or any token. The bridge
  reads only `<codex_home>/sessions/**/rollout-*.jsonl`, and only their `rate_limits` field.
