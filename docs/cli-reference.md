# CLI and MCP reference

[README](../README.md) · [Configuration](configuration.md) · CLI and MCP reference

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
| `bridge_cancel_task` | `task_id`, `reason` | Kills the run's process group and cancels the task |
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
| `config-init [example]` | Human only: write `.ai/orchestrator.yaml` from a shipped example (refuses to overwrite); without an argument, list the examples and the path of `config.example.json` |
| `config-check` | Validate `.ai/orchestrator.yaml` (or the legacy config) |
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

These six views print tables in a terminal, and the same JSON the MCP tools return with
`--json` or when the output is piped.

### Tasks and workflows

| Command | Description |
|---|---|
| `create <spec.json>` | Create a task from a JSON spec file |
| `execute <id>` | Run a task |
| `status <id> [--wait S]` | Task state, optionally waiting for a change |
| `result <id>` | Task result with patch and checks |
| `review <id> --decision approve\|rework\|block --notes TEXT` | Review a task in `REVIEW` |
| `resume <id> [--feedback TEXT]` | Continue a task on the same agent and session |
| `cancel <id> --reason TEXT` | Cancel a task |
| `plan "<objective>" [--context TEXT]` | Run the planner agent; creates a workflow |
| `run-workflow <W…>` / `workflow-status <W…>` | Drive a workflow / read its state |

### Accounts

| Command | Description |
|---|---|
| `account-use <name> [--shell]` | Print `export` / function lines for `eval "$(…)"`, or open a subshell, so your own CLI uses that account |
| `account-clear <name>` | Clear an account's unavailable mark (run after the user confirms) |

`account-login`, `account-use`, `account-clear` and `account-remove` accept `--provider` when one
name is used by several providers.

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

These need an interactive terminal and a typed confirmation (or the TUI). The kit's
`.claude/settings.json` denies them to Claude Code.

| Command | Description |
|---|---|
| `approve <id>` | Close a high-risk or sensitive-path task waiting at `APPROVED` |
| `reassign <id> <agent\|account>` | Move a task to another agent; starts a new session |
| `account-login <name> [--device-auth]` | Fresh login into the account's own home (`codex login`, `claude auth login`, agy sign-in, `opencode auth login`) |
| `account-add <name> --provider openai\|anthropic\|google\|opencode [--home DIR] [--device-auth]` | Create a machine-wide account under `~/.agent-bridge/homes/` (or register `--home`), then log in |
| `account-remove <name>` | Forget a machine-wide account; deletes its home only if `account-add` created it |
