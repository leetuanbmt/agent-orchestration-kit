# Security, approvals and accounts

- **Codex CLI** runs with a `workspace-write` (or `read-only`) OS sandbox, no network, `/tmp`
  and `$TMPDIR` not writable, user `~/.codex/config.toml` ignored. `danger-full-access` is not
  configurable.
- **Claude CLI** uses `--strict-mcp-config` (no MCP servers), `--setting-sources ""`
  (no user/project settings) and `--permission-mode dontAsk`. Read/Glob/Grep and scoped
  Edit/Write keep their existing permission rules, including credential read denies and
  protected write denies. A probe showed unscoped Write reaching files outside the worktree,
  while scoped rules refused those writes. With `allow_bash: true`, Bash and its child processes run in Claude
  Code’s OS sandbox: no reads of any account home, registry homes, global CLI credential
  directories or private project account/orchestrator files; writes only to the working directory
  and `$TMPDIR`; no network. Sandbox availability is required, unsandboxed retries are disabled,
  and no commands are excluded. macOS Keychain remains reachable. `security` and
  `/usr/bin/security` are denied by permission rules only: a speed bump, not a boundary.
  The sandbox does not apply to Read/Edit tools. Read tools can read anything the user can
  except the denied credential paths. Prefer Codex as the executor for untrusted work, because
  Claude's Read tools and the claude process itself are not OS-confined and the Keychain is
  reachable from sandboxed Bash.
  Optional owner-configured `claude_hook_settings` must be an absolute regular JSON file
  outside the project with only a `hooks` object;
  its hooks are merged into the sandbox settings when Bash is enabled. Hooks remain trusted
  code running unsandboxed, and the option is off by default.
- **opencode** and **agy** are read-only adapters (planner/reviewer only; the config refuses
  writing). opencode runs with edit, bash, webfetch and external directories denied through
  `OPENCODE_CONFIG_CONTENT` and project config disabled; agy runs `--mode plan --sandbox` and
  keeps browser tools the CLI cannot disable. Probes wrote no files with either; neither has an
  OS sandbox.
- Every agent process gets `AGENT_BRIDGE_AGENT=1`; the CLI, the MCP server and the `Bridge`
  Python API refuse to start under it. Everything still runs as one user: human-only steps rely on
  the agents being confined, not on an OS boundary inside the bridge.
- Verification commands run under Landlock where available (`verify_sandbox` in the tracked
  `.ai/config.json`, `auto` by default; `workflow.verify_sandbox` in orchestrator.yaml is the
  deprecated location, and setting both is an error): writes only to the worktree, a private
  TMPDIR and `verify_writable`; the worktree is diffed again after them. Never weaken this
  setting yourself, and treat any `.ai/config.json` change in a diff as a security change.
  On macOS (Seatbelt) a check also cannot write the repository's git config, `config.worktree`,
  `hooks/` or `info/`, and cannot read any configured account home, the registry's `homes/`,
  `~/.codex/auth.json`, `~/.claude/.credentials.json`, `.ai/orchestrator.yaml` or
  `.ai/accounts.yaml`. Landlock (Linux) restricts writes only.
- Git config is code the bridge would run (`core.fsmonitor`, filters, hooks). After every run the
  bridge hashes the repository's config, `config.worktree`, hooks and `info/attributes` from disk
  before any git call; a change BLOCKs the task without running git, and while it is still on
  disk execute/resume are refused and `health` is NOT OK until a human restores it. Bridge git
  calls run with `core.fsmonitor=false` and `core.hooksPath=/dev/null` (via `GIT_CONFIG_*`),
  `--no-ext-diff --no-textconv` and a scrubbed environment.
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
- A project uses an account only by declaring it in `.ai/accounts.yaml`: its own home, or
  `login: <name>` for a machine registry login. Its limits live there, so a project need is
  never solved by changing machine-wide settings. When telling a human where to change an
  account, name the file the bridge's error or `config-check` names; `config-migrate` and
  `project-account-*` are human-only.
- A task stays on its agent and account; retries and resumes stay on it. Two things can move
  it, both starting a new session: a human (`agent-bridge reassign <id> <agent>`), or the bridge
  itself when the user enabled quota fallback (only to the configured candidates, logged as an
  `account_auto_switched` event). Claude never moves a task or edits that config.
- Quota comes from the latest `rate_limits` event in a Codex account's session rollouts, or the
  limits a CLI reported on its last run through the bridge; `UNKNOWN` without either. A QUOTA
  or AUTH pause marks the account unavailable: report it to the user. AUTH never triggers a
  switch.
- A `credential: keychain` token lives in the macOS login Keychain (service `agent-bridge`).
  Claude never runs `agent-bridge account-set-token` and never runs `security
  find-generic-password` for it; the bridge reads it only to build that account's run env.
- Never read, print, copy or log `auth.json`, `.credentials.json`, `~/.codex-accounts/*`,
  `~/.agent-bridge/homes/*`, `~/.gemini/antigravity-cli/*`, an account home's credentials or
  any token. For agy the bridge checks only the token file's size and mtime, never its content. The bridge reads only
  `<codex_home>/sessions/**/rollout-*.jsonl`, and only their `rate_limits` field.
