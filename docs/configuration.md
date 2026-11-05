# Configuration reference

[README](../README.md) · Configuration · [CLI and MCP reference](cli-reference.md)

agent-bridge reads two optional files from the project's `.ai/` directory:

| File | Purpose | Tracked by git |
|---|---|---|
| `.ai/orchestrator.yaml` | Accounts, agents, roles, routing and workflow stages | No (it names machine-specific auth homes) |
| `.ai/config.json` | Bridge settings: timeouts, sandbox, path rules, verification allowlist | Yes (an agent changing it is a scope violation) |

`agent-bridge init` writes `.ai/.gitignore`, which ignores everything in `.ai/` except
`config.json` and `.gitignore` itself.

Without `orchestrator.yaml` the **legacy setup** applies: the MCP client (your Claude Code
session) plans and reviews, Codex executes, and accounts come from `.ai/config.json`. This is
the 0.1 behaviour.

## `.ai/orchestrator.yaml`

Start from a shipped example and validate it:

```bash
agent-bridge config-init                # list the examples
agent-bridge config-init orchestrator   # write .ai/orchestrator.yaml; refuses to overwrite
agent-bridge config-check               # schema check; errors name the field
agent-bridge agents                     # resolved roles, agents and availability
```

`config-init` is meant to be run by a human; the kit's `.claude/settings.json` denies it to
Claude Code.

```yaml
version: 1
accounts:
  codex-work:  {provider: openai,    home: ~/.codex-accounts/homes/work}
  claude-main: {provider: anthropic, home: ~/.claude-accounts/main, max_concurrency: 1}
agents:
  claude:          {adapter: client}                         # the MCP caller
  claude-headless: {adapter: claude-cli, account: claude-main, model: sonnet}
  codex-a:         {adapter: codex-cli,  account: codex-work}
roles:
  planner: claude-headless
  executor: {agents: [codex-a], strategy: ordered}
  reviewer: claude                                           # you review via bridge_review_task
routing:
  quota_fallback: {enabled: false, min_remaining_pct: 10}
workflow:
  stages: [plan, execute, test, review]
  max_rework: 2
```

| Section | Keys |
|---|---|
| `version` | Must be `1`. |
| `accounts.<id>` | `provider`: `openai`, `anthropic`, `google` or `opencode`. `home`: isolated auth home; omit it to use the CLI's inherited global login (opencode accepts no `home`). `max_concurrency`: 1–8, default 1. |
| `agents.<id>` | `adapter`: `codex-cli`, `claude-cli`, `client`, `opencode-cli` or `agy-cli`. `account` (required except for `client`), optional `provider` (must match the adapter), `model`, `capabilities` (subset of `[read, write]`), `allow_write` (default true for `codex-cli`, false otherwise), `allow_bash` (`claude-cli` only), `timeout_s` (≥ 30), `enabled` (default true). |
| `roles.<name>` | An agent id, or `{agents: [...], default: <agent>, strategy: ordered \| explicit, requires: read \| write}`. `default` is tried first; the other agents are fallbacks in list order. `default` cannot be combined with `strategy: explicit`. |
| `routing.quota_fallback` | `enabled` (default false), `min_remaining_pct` (default 10), `agents` (candidates in order; default: the executor role's agents). |
| `workflow` | `stages`: ordered subset of `plan, execute, test, review`, default `[execute, test, review]`; must include `execute` and at least one of `test` / `review`. `max_rework`: 0–4, default 2. |
| `security` | `project_isolation` and `human_approval_for_sensitive_actions`. Both are fixed to `true`; the keys exist only to make the policy explicit. |

Ids use `[a-z0-9_-]`, at most 32 characters. Validation rejects unknown keys, unknown ids, an
agent whose account belongs to another provider, two accounts sharing one auth home, a role no
listed agent can play, and a `client` agent in the `executor` role.

Role rules:

- `executor` is required and needs `write`; `planner` and `reviewer` need `read`.
- A `client` agent can only read: it plans and reviews through the MCP tools and is never
  started by the bridge.
- `opencode-cli` and `agy-cli` are read-only; `allow_write` or a `write` capability on them is
  rejected.
- Built-in role names are `planner`, `executor`, `reviewer`, `advisor` and `tester`. Other role
  names are accepted, but only planner, executor and reviewer have a stage; testing is the
  bridge's own `test` stage.

### Shipped examples

| Example | Planner | Executor | Reviewer |
|---|---|---|---|
| *(no file, legacy)* | Claude Code session | Codex (per `config.json` account) | Claude Code session |
| [`orchestrator`](../examples/orchestrator.yaml) | Claude headless | Codex on two accounts, fallback order | Claude headless |
| [`claude-executor`](../examples/claude-executor.yaml) | Codex | Claude headless (`allow_write`) | Codex |
| [`single-agent`](../examples/single-agent.yaml) | Codex | Codex | Codex |
| [`gates-only`](../examples/gates-only.yaml) | — | Codex | None: deterministic gates only |
| [`other-reviewers`](../examples/other-reviewers.yaml) | agy | Codex | opencode |

### Routing

- A task pinned with `agent` (or `account`) runs there.
- Otherwise, when the task first starts, the bridge picks the role's `default`, then the first
  eligible agent in role order: enabled, CLI installed, able to write, account available and
  under `max_concurrency`. The choice is logged as `agent_routed`.
- The task then keeps that agent and its session for retries and resumes.
- `strategy: explicit` requires every task to name its agent.

### Stages

| Stage | Who | What happens |
|---|---|---|
| `plan` | Planner agent | `bridge_plan` runs it read-only; the bridge validates every planned task (paths, commands, dependencies) before creating any. `bridge_run_workflow` then runs the tasks in dependency order, one at a time. |
| `execute` | Executor agent | Implements one task. |
| `test` | Bridge | Re-runs the task's verification commands. |
| `review` | Reviewer | A `client` reviewer is the MCP caller (`bridge_review_task`). A reviewer agent runs read-only after the checks pass; its decision is recorded as `reviewer: <agent>`. If it fails, the task stays in `REVIEW` for the caller. |

Without a `review` stage the bridge approves work that passed every gate (`reviewer: bridge`).
High-risk work still waits for a human.

## `.ai/config.json`

Optional. Copy only the keys you change; a list you set replaces the default list, and unknown
keys are rejected. The full file with every default is `config.example.json`;
`agent-bridge config-init` (without an argument) prints its path. In a checkout it is
[`.ai/config.example.json`](../.ai/config.example.json).

| Key | Default | Meaning |
|---|---|---|
| `sandbox` | `workspace-write` | Codex sandbox for executor runs, or `read-only`. `danger-full-access` is not accepted. |
| `model` | `null` | Default Codex model when an agent sets none (the CLI's default when `null`). |
| `codex_bin` / `claude_bin` / `opencode_bin` / `agy_bin` | `codex` / `claude` / `opencode` / `agy` | CLI executables. |
| `ignore_user_config` | `true` | Pass `--ignore-user-config` to Codex, so `~/.codex/config.toml` (hooks, MCP servers, profiles) is not loaded. |
| `claude_hook_settings` | `null` | Absolute path to a JSON file outside the project containing only a `hooks` object; passed to every Claude run as `--settings`. Trusted code, runs unsandboxed. Set it only by hand. |
| `default_timeout_s` / `max_timeout_s` | `1800` / `7200` | Per-run time limits. |
| `max_attempts` | `3` | Legacy setup only; with `orchestrator.yaml` use `workflow.max_rework`. |
| `network_retries` / `retry_backoff_s` | `1` / `10` | Bounded retry for network errors only. |
| `verify_timeout_s` | `900` | Limit per verification command. |
| `verify_allowlist` | `pytest`, `uv`, `python`, `python3`, `ruff`, `mypy`, `make`, `npm`, `npx`, `pnpm`, `yarn`, `node`, `go`, `cargo`, `git`, `true`, `false` | Executables a verification command may start. |
| `protected_paths` | `.env*`, `*.pem`, `*.key`, `.git/**`, `.ai/**`, `.mcp.json`, `.claude/settings*.json` | Never writable by a task and never allowed in `allowed_paths`. |
| `sensitive_paths` | migrations, `auth/`, `deploy/`, `.github/workflows/`, `*secret*`, `*credential*`, `Dockerfile` | A change under these requires human approval before `DONE`. |
| `env_deny` / `env_allow` | `*_API_KEY`, `*_TOKEN`, `*_SECRET*`, `*PASSWORD*`, `AWS_*` / none | Variables removed from agent and verification environments. |
| `lock_dir` | `$AGENT_BRIDGE_HOME/locks` | Cross-process account slot locks, shared by every project on the machine. |
| `accounts` / `default_account` / `auto_switch` | — | Legacy setup only. With `orchestrator.yaml`, setting `accounts` or an enabled `auto_switch` is an error. |

## Accounts

An account is one login with its own auth home. Projects declare accounts in
`orchestrator.yaml`; machine-wide accounts are created with `agent-bridge account-add`.

| Provider | Adapter | What `home` sets |
|---|---|---|
| `openai` | `codex-cli` | `CODEX_HOME` |
| `anthropic` | `claude-cli` | `CLAUDE_CONFIG_DIR` |
| `google` | `agy-cli` | `HOME` (a whole home directory) |
| `opencode` | `opencode-cli` | No `home`: opencode has a single global login |

- **No `home`.** The CLI's inherited global login (`~/.codex`, `~/.claude`) is used; the bridge
  never writes to it.
- **With `home`.** A separate login. Homes must be absolute, outside the project and outside
  the global homes, and never shared by two accounts.
- **Logging in.** A human runs `agent-bridge account-login <name>` for a fresh session in that
  home. For Codex it fails if the global `~/.codex/auth.json` changes during the login.
- **codex-switch.** Homes made with `codex-switch home add` work as Codex accounts. codex-switch
  and claude-switch *profiles* are listed but never used, because they hold copied tokens.

### Machine-wide accounts

`agent-bridge account-add work --provider openai` (human only) creates
`~/.agent-bridge/homes/work` (mode 0700), records it in `~/.agent-bridge/accounts.json` and runs
the provider's own login inside it. `--home DIR` registers an existing home instead. Every
project sees these accounts by name; an account the project declares itself takes precedence.

One name can serve several providers: after `account-add work --provider openai`,
`account-add work --provider google` creates the account `work@google` (home
`~/.agent-bridge/homes/work@google`). Listings show both as `work`, and an agent with
`account: work` gets the account matching its adapter's provider. `account-login`,
`account-use`, `account-clear` and `account-remove` take `--provider` (or the id
`work@google`) when a name is shared.

To use an account in your own terminal:

```bash
eval "$(agent-bridge account-use work)"   # exports CODEX_HOME / CLAUDE_CONFIG_DIR
agent-bridge account-use work --shell     # or open a subshell; exit to return
```

For agy, `account-use` defines an `agy` shell function instead of changing `HOME`. For opencode,
`account-add <name> --provider opencode` registers one account without a home, and
`account-login <name>` runs `opencode auth login`.

### Quota, availability and concurrency

- **Quota source.** Codex: the latest `rate_limits` event in
  `<home>/sessions/**/rollout-*.jsonl` (nothing else in the home is opened). Claude: the
  `rate_limit_event` the CLI reported on its last run through the bridge. Remaining quota is
  `100 − max(5h used %, 7d used %)`; it is `UNKNOWN` for an account that has never reported.
- **Unavailable marks.** A QUOTA or AUTH failure marks the account unavailable for every project
  on the machine (`$AGENT_BRIDGE_HOME/accounts.db`). A QUOTA mark clears itself when a snapshot
  shows positive remaining quota after a window that was open at mark time has reset. AUTH
  marks, and accounts without a snapshot, need `agent-bridge account-clear <name>` after the
  user confirms the issue is resolved.
- **Concurrency.** At most `max_concurrency` agent processes per account (cross-process `flock`
  slots in `lock_dir`), and one `workspace-write` task per worktree. Read-only runs (planning,
  reviewing, `read-only` tasks) may run alongside.
- **Affinity.** A task stays on its agent and account. A human can move it with
  `agent-bridge reassign <id> <agent>`, which starts a new session.

### Quota fallback (opt-in)

```yaml
routing:
  quota_fallback: {enabled: true, min_remaining_pct: 15, agents: [codex-b, codex-a]}
```

Before a task starts or restarts, the bridge checks its account. When the account is in QUOTA
state or below `min_remaining_pct`, the task moves to the first candidate agent on another
account that is available, logged in, under its concurrency limit and at or above the threshold
(or `UNKNOWN`). The move drops the session: the task restarts with the full prompt plus any
review feedback. It is logged as `account_auto_switched` and is never triggered by AUTH.
Enabling it is the user's authorisation; the legacy `auto_switch` block in `config.json` maps
onto it.

## State and storage

| Path | Tracked | Content |
|---|---|---|
| `.ai/config.json` | Yes | Bridge settings |
| `.ai/orchestrator.yaml` | No | Roles, agents, accounts, workflow |
| `.ai/backup-<UTC timestamp>/` | No | Copies of files `init` changed |
| `$AGENT_BRIDGE_HOME/projects/<project>/worktrees/<worktree>/` | Never | `bridge.db`, `logs/`, per-run temp files, `worktree.json`, `migration.json` after migration |
| `$AGENT_BRIDGE_HOME/accounts.db`, `accounts.json`, `homes/`, `locks/` | Never | Machine-wide account marks, accounts and slot locks |
| `<git-dir>/agent-bridge/install-manifest.json` | Never | What `init` installed, used by `uninstall` |

`AGENT_BRIDGE_HOME` defaults to `~/.agent-bridge` and must be an absolute directory outside the
worktree. State lives outside the worktree so Codex's `workspace-write` sandbox cannot write
there (on codex-cli 0.166 an agent's `touch` and Python writes into it failed with "Operation
not permitted"). History stays readable after the worktree folder is deleted. Bridge databases
and their WAL/SHM files use 0600 permissions.

Projects set up by an older version keep their state in `<git-dir>/agent-bridge/` (legacy)
until it is migrated. Older databases gain new columns and tables on first start.

### Migrating and maintaining state

All `storage` commands preview by default and change nothing without `--apply` (or `--yes` for
`rollback`). The kit's Claude Code settings deny the applying forms to Claude.

1. Close Claude Code sessions, the TUI and `agent-bridge web`.
2. `agent-bridge storage status` shows the plan.
3. `agent-bridge storage migrate --apply` copies the state with an SQLite backup (WAL
   included), checks integrity, table counts and row hashes, copies logs with SHA-256
   verification, and confirms the source did not change before publishing the central store.
   Legacy files are left in place.

| Command | Effect |
|---|---|
| `storage relink <old-root> <new-root> [--apply]` | Reattach central history after moving or renaming a repository or worktree |
| `storage rollback <root> [--yes]` | Move a migrated central store aside; `--yes` accepts hiding newer central events |
| `storage cleanup-legacy [--apply]` | Move verified legacy files to `~/.agent-bridge/backups`; nothing is deleted |
| `storage prune [--apply]` | Remove registry entries whose root is no longer a directory |
| `storage gc --logs-older-than DAYS [--apply]` | Delete attempt logs older than DAYS (≥ 1) of `DONE`, `CANCELLED` or `BLOCKED` tasks; database rows are kept |

`prune` and `gc` refuse to apply while bridge processes are running. `health` warns when an old
process keeps writing to a legacy database after migration.
