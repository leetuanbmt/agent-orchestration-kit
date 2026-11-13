# CLI and MCP reference

[README](../README.md) · [Configuration guide](configuration-guide.md) · [Configuration reference](configuration.md) · CLI and MCP reference

## MCP tools

The `agent-bridge` MCP server (`agent-bridge serve`, started by Claude Code from `.mcp.json`)
exposes these tools.

| Tool | Parameters | Description |
|---|---|---|
| `bridge_health` | — | Adapters (path, version), Codex login, active tasks, remaining quota per account |
| `bridge_list_agents` | — | Agents, roles, capabilities, availability, stages, quota fallback |
| `bridge_list_accounts` | — | Accounts, login state, quota snapshot (5h / 7d windows), codex-switch homes and profiles; no credentials |
| `bridge_account_usage` | `account` (optional) | Read-only: per account its state, each configured limit (used, threshold, remaining, reset time, source, status), spend per day / week / month and quota. Limits cannot be changed through MCP |
| `bridge_create_task` | `objective`, `allowed_paths`, `acceptance_criteria`, optional `context`, `scope`, `excluded_paths`, `depends_on`, `verification_commands`, `risk_level` (`low` / `medium` / `high`), `timeout_s`, `max_attempts`, `sandbox`, `agent`, `account` | Validates and stores a task; identical live specs are deduplicated. `agent` pins an executor agent, `account` the first executor on that account |
| `bridge_execute_task` | `task_id` | Starts the executor agent in the background |
| `bridge_task_status` | `task_id`, `wait_s` | Compact state; `wait_s` (≤ 55) blocks until the task leaves `RUNNING` / `VERIFYING` |
| `bridge_task_result` | `task_id` | Spec, attempts, the agent's final JSON, diff stat and patch, the bridge's checks, reviews |
| `bridge_review_task` | `task_id`, `decision`, `notes` | `approve`, `rework` or `block` by the client; also overrides a failed reviewer agent |
| `bridge_resume_task` | `task_id`, `feedback` | Rework or recover on the same agent and session |
| `bridge_cancel_task` | `task_id`, `reason` | Kills the run's process group and cancels the task (any state except DONE and CANCELLED) |
| `bridge_list_tasks` | `state`, `limit` (50) | Tasks, newest first |
| `bridge_plan` | `objective`, `context` | Runs the planner agent and creates a workflow |
| `bridge_workflow_status` | `workflow_id`, `wait_s` | Workflow state and tasks; `wait_s` (≤ 55) blocks while `PLANNING` or `RUNNING` |
| `bridge_run_workflow` | `workflow_id` | Drives a workflow until `DONE`, `INCOMPLETE` or `WAITING` |

## CLI

`agent-bridge <command>`. Every command refuses to run inside an agent the bridge started
(`AGENT_BRIDGE_AGENT=1`).

### Setup and configuration

| Command | Description |
|---|---|
| `init [dir] [--force]` | Install the kit into a project (default: current directory) |
| `uninstall [dir] [--purge]` | Remove what `init` added, keeping files you edited; `--purge` also deletes `.ai/` |
| `config-init [example]` | Human only: write `.ai/orchestrator.yaml` and `.ai/accounts.yaml` from a shipped example pair (refuses if either exists); without an argument, list the examples and the path of `config.example.json` |
| `config-check [--json]` | Validate `.ai/orchestrator.yaml` and `.ai/accounts.yaml` (or the legacy config); `deprecated` lists settings in an older location |
| `config-migrate [--apply] [--json]` | Human only: preview moving accounts to `.ai/accounts.yaml` and agents/roles to `orchestrator.yaml`; `--apply` backs both files up to `.ai/backup-<stamp>/` and writes them. Refuses if the effective configuration would change |
| `serve` | MCP stdio server (what `.mcp.json` runs) |

### Monitoring

| Command | Description |
|---|---|
| *(none)* / `tui` | Interactive dashboard for the current project; needs a terminal (prints help otherwise) |
| `web [--port N] [--open]` | Read-only web dashboard for every known project and worktree, on `127.0.0.1` (default port 8765; `0` picks a free port) |
| `health [--json]` | OK / NOT OK, adapters, quota, task counts and what to fix; exits 1 when not OK |
| `agents [--json]` | Roles mapped to agents, then agents with capabilities and availability |
| `usage [account] [--json]` | Per-account limits: used, threshold, remaining, reset time, source (`actual` / `estimated` / `unavailable`) and status; accounts without limits show today's spend. See [Per-account limits](configuration.md#per-account-limits-opt-in) |
| `account-list [--all] [--json]` | Usable accounts and the command to add more; `--all` adds codex-switch / claude-switch profiles, homes not added yet and CLIs without an account |
| `list [--state S] [--limit N] [--json]` | Tasks of this project (default limit 50); tasks that need you are marked `●` with the next command |
| `tasks [--state S] [--limit N] [--json]` | Tasks of every project and worktree on this machine, grouped by project (default 10 per project) |

The monitoring views above, plus `config-check`, `config-migrate`, `config-set`, `config-unset` and
`project-account-add`, `project-agent-add` and `project-agent-remove`, print
a readable view in a terminal, and the same JSON the MCP tools return with `--json` or when the
output is piped. (`config-migrate`, `config-set`, `config-unset`, `project-account-add` and the `project-agent-*` commands still need a terminal to
run.)

### Tasks and workflows

| Command | Description |
|---|---|
| `create <spec.json>` | Create a task from a JSON spec file |
| `execute <id>` | Run a task |
| `status <id> [--wait S]` | Task state, optionally waiting for a change |
| `result <id>` | Task result with patch and checks |
| `review <id> --decision approve\|rework\|block --notes TEXT` | Review a task in `REVIEW` |
| `resume <id> [--feedback TEXT]` | Continue a task on the same agent and session |
| `cancel <id> --reason TEXT` | Cancel a task in any state except DONE and CANCELLED (BLOCKED included) |
| `plan "<objective>" [--context TEXT]` | Run the planner agent; creates a workflow |
| `run-workflow <W…>` / `workflow-status <W…>` | Drive a workflow / read its state |

### Accounts

| Command | Description |
|---|---|
| `account-use <name> [--shell]` | Print `export` / function lines for `eval "$(…)"`, or open a subshell, so your own CLI uses that account |
| `account-show <name>` | One account: its listing row (login, quota), the credential reference (never the value), the agents that use it and the roles it is default for. Prints JSON |
| `account-verify <name>` | Check the account's login and credential reference; prints JSON with `ok` and exits 1 when the account cannot run |
| `account-clear <name>` | Clear an account's unavailable mark (run after the user confirms) |

`account-login`, `account-use`, `account-clear`, `account-set-token` and `account-remove` accept
`--provider` when one name is used by several providers.

### Storage

See [State and storage](configuration.md#state-and-storage). Every command previews unless
`--apply` (or `--yes`) is given.

| Command | Description |
|---|---|
| `storage status` | Where this worktree's state lives and the migration plan |
| `storage migrate [--apply]` | Copy legacy git-dir state to `$AGENT_BRIDGE_HOME` |
| `storage relink <old-root> <new-root> [--apply]` | Reattach history after moving a repository (human) |
| `storage rollback <root> [--yes]` | Move a migrated central store aside (human) |
| `storage cleanup-legacy [--apply]` | Back up verified legacy files (human) |
| `storage prune [--apply]` | Remove registry entries for missing folders |
| `storage gc --logs-older-than DAYS [--apply]` | Delete old logs of finished tasks |

### Human-only commands

These need an interactive terminal and a typed confirmation (or the TUI). The
`.claude/settings.json` that `agent-bridge init` installs denies them to Claude Code.

| Command | Description |
|---|---|
| `approve <id>` | Close a high-risk or sensitive-path task waiting at `APPROVED` |
| `reassign <id> <agent\|account>` | Move a task to another agent; starts a new session |
| `account-login <name> [--device-auth]` | Fresh login into the account's own home (`codex login`, `claude auth login`, agy sign-in, `opencode auth login`, `cursor-agent login`) |
| `account-add <name> --provider openai\|anthropic\|google\|opencode\|cursor [--home DIR] [--device-auth]` | Create a machine-wide account under `~/.agent-bridge/homes/` (or register `--home`), then log in |
| `account-set-token <name> [--delete]` | Store a setup-token or API key in the macOS Keychain for an account with `credential: keychain`; `security` prompts for the secret on the terminal, so it never passes through argv. `--delete` removes the stored token. Refuses accounts without `credential: keychain` |
| `account-remove <name>` | Forget a machine-wide account; deletes its home only if `account-add` created it |
| `project-account-add <name> (--provider P \| --login L) [--home DIR] [--credential keychain] [--token-env VAR] [--executor] [--json]` | Declare an account in `.ai/accounts.yaml` (a new home, or a reference to a registry login); prints the agent line to paste into `orchestrator.yaml` |
| `project-account-limits <name> [--five-hour-pct N] [--weekly-pct N] [--clear]` | Set or clear an account's window limits in `.ai/accounts.yaml` |
| `project-agent-add <agent> (--account A \| --adapter client) [--model M] [--allow-bash] [--role R] [--default] [--apply] [--json]` | Add an agent to `.ai/orchestrator.yaml` (adapter from the account's provider) and, with `--role`, to that role (`--default` makes it first). Previews unless `--apply`, which backs the file up to `.ai/backup-<stamp>/`; comments are not kept |
| `project-agent-remove <agent> [--apply] [--json]` | Remove an agent and its role entries; refuses a role it would empty; lists unfinished tasks pinned to it. Same preview and backup |
| `project-account-remove <name>` | Remove an account `accounts.yaml` owns and its Keychain token; keeps the home. Refuses while an agent uses it, naming the agents and `project-agent-remove` |
| `config-set <key> <value> [--apply] [--json]` | Set one setting in the `.ai` file that owns it, after validating the whole configuration. Previews unless `--apply`, which backs the file up to `.ai/backup-<stamp>/` and writes it atomically |
| `config-unset <key> [--apply] [--json]` | Remove one setting the same way (it falls back to its default) |

`config-set` and `config-unset` take a dotted key. The first segment picks the file: `accounts` is
`.ai/accounts.yaml`; `version`, `agents`, `roles`, `routing`, `workflow` and `security` are
`.ai/orchestrator.yaml`; a bridge setting such as `verify_sandbox` is `.ai/config.json`. Without an
`orchestrator.yaml` only `config.json` keys are accepted. The value is parsed as YAML, so `5`,
`true`, `null`, `[a, b]`, `{five_hour_pct: 80}` and plain text all work. Examples:

```bash
agent-bridge config-set roles.executor.default codex-b --apply
agent-bridge config-set roles.executor.agents "[codex-a, codex-b]" --apply
agent-bridge config-set agents.codex-a.model gpt-5 --apply
agent-bridge config-set accounts.cx.limits.weekly_pct 90 --apply
agent-bridge config-set accounts.cx.max_concurrency 2 --apply
agent-bridge config-set routing.quota_fallback.enabled true --apply
agent-bridge config-set workflow.max_rework 1 --apply
agent-bridge config-set verify_sandbox strict --apply     # .ai/config.json: flagged as a security setting
agent-bridge config-unset accounts.cx.limits --apply
```

Refused, with nothing written: an invalid result (the message names the problem), a change that
would create an outdated layout (for example `workflow.verify_sandbox`, which belongs in
`config.json`), an account declared in `orchestrator.yaml` (change it there, or run
`config-migrate`), and any key ending in `token`, `secret` or `password` (`token_env` is allowed;
no secrets in `.ai` files). A value equal to the current one writes nothing. Comments in a YAML
file are not kept; the original is in the backup. `config.json` keys marked as security settings
(`sandbox`, `verify_*`, `protected_paths`, `sensitive_paths`, `env_deny`, `env_allow`,
`claude_hook_settings`, `ignore_user_config`) print "security setting: .ai/config.json is tracked,
so this applies to every clone".
