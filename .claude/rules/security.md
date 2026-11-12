# Security, approvals and accounts

- **Codex CLI** runs with a `workspace-write` (or `read-only`) OS sandbox, no network, `/tmp`
  and `$TMPDIR` not writable, user `~/.codex/config.toml` ignored. `danger-full-access` is not
  configurable.
- **Claude CLI** uses `--strict-mcp-config` (no MCP servers), `--setting-sources ""`
  (no user/project settings) and `--permission-mode dontAsk`. Read/Glob/Grep and scoped
  Edit/Write keep their existing permission rules, including credential read denies and
  protected write denies. A probe showed unscoped Write reaching files outside the worktree,
  while scoped rules refused those writes. Every rule names the absolute project root
  (`//<root>/src/**`): Claude resolves `./` rules against the Bash cwd, which a `cd` moves, so
  `./` rules refused in-scope writes and let deny rules miss after a `cd` (probed on 2.1.322).
  A project path with a space, comma, parenthesis, quote or glob character (`*?[]{}!`) cannot be written as a rule, so
  Claude runs are refused there. With `allow_bash: true`, Bash and its child processes run in Claude
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
- **agy** and **opencode** are read-only unless their agent sets `allow_write: true` (see
  below; agy executes only with it). Both are enforced by CLI permission rules, not by mode flags. opencode (2.2.6) gets a V2 config
  in `OPENCODE_CONFIG_CONTENT` that denies everything first and allows only read, glob and grep
  (`.env` files denied; `execute`/MCP, skills, websearch, subagents denied), with project config
  disabled and `XDG_CONFIG_HOME` set to an empty per-run directory so the user's plugins, MCP
  servers, agents and skills are not loaded (login and sessions stay). agy (1.5.5) runs
  `--sandbox --disable-slash-commands --settings` with `toolPermission: request-review` and deny
  rules for `write_file(*)`, `command(*)` and `mcp(*)`; `--mode plan` enforces nothing and is not
  used. A tool needing approval ends an agy turn with `denied_actions`, which the bridge treats as
  a failed turn. agy refuses a directory with `.agents/hooks.json` or `.agents/mcp_config.json`
  (agy runs them at start-up with no trust prompt). Still open: neither CLI has an OS sandbox for
  its own process; agy still allows `search_web`, subagents and browser tools, runs user-level
  hooks and MCP servers, and merges the user's global settings.
- **agy executor mode** (`allow_write: true`, probed 2026-11-11 on agy 1.5.5): the run uses
  `toolPermission: request-review`, allow rules `write_file(<dir>/)` per allowed directory (every
  depth, by path component) and `write_file(<file>)` per exact file, and deny only `command(*)` and
  `mcp(*)` (no `write_file` deny: it would beat every allow). The allow rules cover every depth
  under each directory; the bridge's post-run scope check enforces the task scope. A write outside
  them ends the turn at once and fails it. No shell: no tests, delete or rename. A hardlink, a
  symlink or a protected/excluded file under an allowed directory (or a hardlinked exact path)
  refuses the run, as does `.agents/hooks.json`/`mcp_config.json`. Not gated: subagents, schedule,
  image generation and `search_web`; workspace reads (including `.env`) are not blocked; the
  user's global agy settings are merged and user-level hooks/MCP servers still start; agy keeps
  state under `~/.gemini`. Deny rules are not a boundary (case-sensitive on a case-insensitive
  filesystem). No OS sandbox: prefer Codex for untrusted work. The CLI self-updates (1.5.3 became
  1.5.5 during the probe); its login check is known to be unreliable on 1.5.x. On a resumed
  conversation `structured_output` can be stale, so the answer is taken only from a JSON object in
  the response text; prose fails the turn.
- **opencode executor mode** (`allow_write: true`): the run keeps the deny-all config and adds one
  `edit <dir>/*` allow per allowed path (plain files exact; a glob with no directory prefix
  refuses the run), the edit tool policy, and no shell rule. Allow rules are broader than the task
  globs (every depth under the directory); the bridge's post-run scope check enforces the task
  scope. No shell: the agent cannot run tests, delete or rename. Deny rules are not relied on
  (`.ENV` overwrote a denied `.env` on a case-insensitive filesystem). opencode follows symlinks,
  so a symlink or a protected/excluded file under an allowed directory refuses the run.
  `opencode_worktree_remove` stays exposed (governed by `edit`). Probed with free models only
  (2026-11-11, 2.2.6); no OS sandbox, so prefer Codex for untrusted work.
- **cursor** (`cursor-cli`) is a read-only adapter too: `cursor-agent` runs in `--mode ask` with
  `--sandbox enabled` and `--trust`. Ask mode alone relies on the model declining, so every run
  also sets `CURSOR_CONFIG_DIR=<state_dir>/cursor-config` with a `cli-config.json` whose
  `permissions` are exactly allow `[]` / deny `Write(**)`, `Shell(*)` (other keys the CLI keeps
  there are preserved; file 0600, directory 0700; never logged); probed on cursor-agent
  2026.11.11, the CLI then refuses edits and shell itself, even with `--force`. Chats now live in
  that directory, so Cursor sessions started before this change cannot be resumed. The CLI
  executes hooks from a project's
  `.cursor/hooks.json`, `.claude/settings.json` and `.claude/settings.local.json` (a `hooks`
  object) even in ask mode and outside its sandbox, so the adapter refuses a directory with
  `.cursor/hooks.json`, or with either `.claude` file that has hooks or is not a JSON object; it
  also refuses `.cursor/cli.json`, because whether it can loosen the denies is not established.
  The agent is listed unavailable and `health` warns. `--trust` is needed (print mode exits 1 in an
  unseen directory) and leaves the worktree trusted in `~/.cursor/projects/`. The binary is
  installed as `cursor-agent` and as `agent`; both are login-denied. User-level Cursor hooks and
  skills still load; no OS sandbox was verified.
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
  anthropic, a whole `HOME` for google/agy; opencode and Cursor have only their global login), created by a
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
