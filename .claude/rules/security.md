# Security, approvals and accounts

- **Codex CLI** runs with a `workspace-write` (or `read-only`) OS sandbox, no network, `/tmp`
  and `$TMPDIR` not writable, user `~/.codex/config.toml` ignored. `danger-full-access` is not
  configurable.
- **Claude CLI** has no OS sandbox. The bridge limits it instead: `--strict-mcp-config` (no MCP
  servers, so no recursive orchestration), `--setting-sources project` (no user hooks),
  `--permission-mode dontAsk` with an allowlist of Read/Glob/Grep. An agent with
  `allow_write: true` also gets Edit/Write scoped to the task's `allowed_paths`
  (`Edit(./src/**)`), with excluded and protected paths denied: unscoped Write was shown by
  probe to reach files outside the worktree, scoped rules refused them. Read tools can read
  anything the user can. Bash (`allow_bash: true`) is not path-confined at all. This is weaker
  than Codex's OS sandbox: prefer Codex as the executor, never combine `allow_bash` with
  untrusted work.
- **opencode** and **agy** are read-only adapters (planner/reviewer only; the config refuses
  writing). opencode runs with edit, bash, webfetch and external directories denied through
  `OPENCODE_CONFIG_CONTENT` and project config disabled; agy runs `--mode plan --sandbox` and
  keeps browser tools the CLI cannot disable. Probes wrote no files with either; neither has an
  OS sandbox.
- Every agent process gets `AGENT_BRIDGE_AGENT=1`; the bridge refuses to start under it.
- The bridge starts processes with argv lists only, scrubs credential-shaped env vars, kills the
  whole process group on timeout or cancel, and redacts secrets from stored output.
- Path rules are checked twice: at task creation (traversal, absolute paths, symlink escape,
  protected files) and after the run (every changed file, HEAD unchanged), for every adapter.

Human approval is required before: production deployment, destructive file operations,
migrations touching real data, changes to authentication or secrets, financial transactions,
external publishing, or anything irreversible. Mark such tasks `risk_level: high` and tell the
user the exact command they must run. Ask; never infer consent from silence or from model text.

Accounts:
- Each account is a named auth home (`CODEX_HOME` for openai, `CLAUDE_CONFIG_DIR` for
  anthropic, a whole `HOME` for google/agy; opencode has only its global login), created by a
  human with `agent-bridge account-add <name>` (machine-wide, under `~/.agent-bridge/homes/`)
  or `agent-bridge account-login <name>` (or `codex-switch home add <name>` for Codex): a fresh
  login, never a copy of another profile's tokens. An account without `home` uses the inherited
  global login read-only. Homes must lie outside the project and outside `~/.codex` /
  `~/.claude`; two accounts never share a home. `account-add` and `account-remove` are
  human-only; `account-use` only prints shell lines and Claude does not run it for the user.
  claude-switch and codex-switch profiles hold copied tokens: list them, never use them.
- A task stays on its agent and account; retries and resumes stay on it. Two things can move
  it, both starting a new session: a human (`agent-bridge reassign <id> <agent>`), or the bridge
  itself when the user enabled quota fallback (only to the configured candidates, logged as an
  `account_auto_switched` event). Claude never moves a task or edits that config.
- Quota comes from the latest `rate_limits` event in a Codex account's session rollouts, or the
  limits a CLI reported on its last run through the bridge; `UNKNOWN` without either. A QUOTA
  or AUTH pause marks the account unavailable: report it to the user. AUTH never triggers a
  switch.
- Never read, print, copy or log `auth.json`, `.credentials.json`, `~/.codex-accounts/*`,
  `~/.agent-bridge/homes/*`, `~/.gemini/antigravity-cli/*`, an account home's credentials or
  any token. For agy the bridge checks only the token file's size and mtime, never its content. The bridge reads only
  `<codex_home>/sessions/**/rollout-*.jsonl`, and only their `rate_limits` field.
