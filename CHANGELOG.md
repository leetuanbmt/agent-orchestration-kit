# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Licensed under MIT (`LICENSE`, © leetuanbmt); the wheel carries it and declares
  `License-Expression: MIT`.
- One account name can be reused across providers: the second `account-add work` for another
  provider gets the id `work@provider`; listings show `work`; an agent's `account: work`
  resolves by its adapter's provider; account commands take `--provider` when a name is shared.
- Dashboard: Search, Agents and Accounts moved to the left column. Selecting an account shows
  its provider, login, state, quota, home, the agents using it and its last error in Detail.
- `account-list` shows usable accounts only, with a count of the hidden rows; `--all` lists
  switch profiles, homes not added and CLIs without an account. A terminal narrower than 120
  columns drops Source and Home and shortens the quota. `--json` is unchanged. The dashboard
  no longer lists copied-token switch profiles.
- `.ai/config.example.json` lists every bridge setting with its default (CLI paths, model,
  timeouts, retries, verify allowlist, protected and sensitive paths, env scrubbing, lock dir),
  so it works next to `orchestrator.yaml`; it no longer shows legacy `accounts`.

### Fixed
- The dashboard's Accounts panel cut the state down to one letter on a normal terminal. It
  now shows the account and its state (Provider moved to Detail; a name shared across
  providers shows its id, such as `work@google`), and the side and task columns keep a
  minimum width.
- The dashboard opened with the search box focused, where the arrow keys do nothing; it now
  opens on Needs you (or Tasks), and ↓ or Esc leaves the search box for the task list.
- The dashboard's account commands (`n`, `l`, `d`, `u`) ran the project's own `bridge/` package
  instead of agent-bridge when the project had one (for example the kit's old checkout), so
  `account-add` failed with "invalid choice". They now run the installed CLI.
- `agents`, `bridge_list_agents` and the dashboard no longer show an agent as available when
  its account is logged out ("account <name> logged out"). Routing is unchanged.
- `account-list` shows "logged out" as the State of an account whose login check failed,
  instead of a stored "available"; the extra blank line before its footer is gone.
- opencode accounts can be added: `account-add <name> --provider opencode` (and the dashboard's
  add dialog) registers one machine-wide account on opencode's global login; `account-login`
  runs `opencode auth login` to pick the integration and method (API key, token).

### Added
- `agent-bridge config-init [example]` (human only) writes `.ai/orchestrator.yaml` from a shipped
  example and never overwrites one; without an example it lists them and the path of
  `config.example.json`. The wheel now carries `examples/` and `.ai/config.example.json`, so a
  global install has them without a checkout.
- Configurable orchestration in `.ai/orchestrator.yaml`: accounts (provider, isolated home,
  `max_concurrency`), agents (adapter, account, model, capabilities), roles (planner, executor,
  reviewer, custom; ordered or explicit), routing (opt-in quota fallback) and workflow stages
  (`plan`, `execute`, `test`, `review`). Validated with descriptive errors;
  `agent-bridge config-check` checks it.
- Claude Code headless adapter (`claude-cli`): read-only by default; writing opt-in with
  `allow_write` and scoped to the task's `allowed_paths` (excluded and protected paths denied);
  Bash opt-in with `allow_bash`; no MCP servers, no user hooks; quota from its
  `rate_limit_event`. `account-login` refuses a Claude login that changed the global identity.
- Task status reports `review_pending` and its long-poll waits for a reviewer agent.
- Routing: tasks pinned by `agent` or `account`, otherwise the first eligible executor
  (`agent_routed` event), then affinity; per-account concurrency slots.
- Reviewer agents (read-only, decision recorded with the reviewer) and gates-only workflows
  where the bridge approves verified work.
- Planner agents: `bridge_plan` creates a workflow of validated tasks; `bridge_run_workflow`
  drives it; `bridge_workflow_status` reports it. `bridge_list_agents` shows the mapping. CLI
  `agents`, `plan`, `run-workflow`, `workflow-status`.
- Recursion guard: agent processes get `AGENT_BRIDGE_AGENT=1` and the bridge refuses to start
  under it.
- Example configs in `examples/` and an opt-in real Claude smoke test.
- Read-only adapters `opencode-cli` (opencode `run --format json`, edit/bash/webfetch denied
  through `OPENCODE_CONFIG_CONTENT`) and `agy-cli` (Antigravity `--mode plan --sandbox`,
  stream-json on stdin, `--json-schema`) for planner and reviewer roles; providers `opencode`
  (global login only) and `google` (account home is a whole `HOME`). Opt-in real smoke tests.
- Machine-wide accounts in `~/.agent-bridge/accounts.json`: `account-add` (human; fresh home
  under `~/.agent-bridge/homes/` or `--home`, then the provider's own login), `account-remove`
  (human), `account-use` (`eval` lines or `--shell`, works outside a project). Every project
  sees them; project declarations win.
- `account-list` and the dashboard show every account on the machine: registry and project
  accounts, codex-switch homes and profiles, claude-switch profiles (listed, never used) and
  installed agent CLIs without an account. Dashboard keys `n` / `d` / `u`.
- `account-list`, `health`, `agents` and `list` print tables with the next command in a
  terminal; `--json` (or a pipe) gives the data. `health` exits 1 when not OK.
- Dashboard redesigned as one screen of panels (Needs you, Tasks, Detail, Search, Agents,
  Accounts, executor quota bar). Enter opens the actions that fit the selected row; the footer
  shows only the keys that work there; `/` searches tasks; settings open with `s`.
- README screenshots from an end-to-end run of the installed tool on dummy data
  (`scripts/screenshots.py`).
- A non-editable install (`uv tool install git+...` or a wheel) carries the kit files, so
  `agent-bridge init` works without a checkout and `.mcp.json` calls the installed tool.
- Terminal dashboard: `agent-bridge` without a subcommand opens a Textual TUI for the project in
  the current directory: task list with a "needs you" filter, task detail and patch view,
  human approval, review, cancel, reassign, account login/clear, and the active settings with
  an `$EDITOR` shortcut.
- `agent-bridge init [dir]` and `agent-bridge uninstall [dir]` install into and remove from a
  project; after `uv tool install --editable <kit>`, `.mcp.json` runs `agent-bridge serve` with
  no path to the kit.

### Changed
- Rules and skill are named after roles: `.claude/rules/orchestration.md`, `delegation.md`
  (now including review), `security.md`, and the `agent-orchestration` skill. `setup.sh`
  removes unedited `codex-*` files from earlier installs.
- `reassign` accepts an agent or an account; `account-login` handles Claude accounts
  (`claude auth login` into `CLAUDE_CONFIG_DIR`).
- The verification record's `codex_status` is now `agent_status`.
- Without `orchestrator.yaml` behaviour is unchanged; with it, `accounts` or an enabled
  `auto_switch` in `config.json` is rejected (both moved to the YAML).
- New dependencies: `pyyaml`, `textual`.
- Project renamed to `agent-orchestration-kit` (GitHub repository and Python package name); the
  old GitHub URL redirects.
- **Breaking:** the CLI and the MCP server are now `agent-bridge` (was `codex-bridge`), so MCP
  tools are `mcp__agent-bridge__bridge_*`. `init` (or `setup.sh`) migrates an older install: the
  `.mcp.json` server, settings entries, `CLAUDE.md` block and the `codex-bridge` state directory
  in the git directory. Permission rules written by hand for the old tool names must be renamed.
- `setup.sh` and `uninstall.sh` are thin wrappers around the same installer (`bridge/install.py`).

## [0.1.0] - 2026-10-19

Initial release.

### Added
- `codex-bridge` MCP server (stdio): ten tools covering the task flow
  `bridge_create_task` → `bridge_execute_task` → `bridge_task_status` → `bridge_task_result` →
  `bridge_review_task` / `bridge_resume_task` / `bridge_cancel_task`, plus `bridge_health`,
  `bridge_list_accounts` and `bridge_list_tasks`.
- `codex-bridge` CLI with the same operations, and human-only `approve`, `reassign` and
  `account-login` commands that require an interactive terminal and a typed confirmation.
- Task lifecycle state machine (`BACKLOG → READY → RUNNING → VERIFYING → REVIEW → APPROVED → DONE`,
  plus `REWORK`, `FAILED`, `PAUSED`, `BLOCKED`, `CANCELLED`) with a bounded attempt budget.
- SQLite state and audit log in the git directory (`.git/codex-bridge/`), out of reach of the
  Codex sandbox; automatic migration of the older `.ai/bridge.db`.
- Scope enforcement at task creation and after every run (protected paths, symlink escape,
  full worktree diff, `HEAD` must not move).
- Bridge-run verification commands (argv only, allowlisted executables, timeout) as the review gate.
- Multi-account execution through separate Codex homes, per-account locks, QUOTA/AUTH pause.
- Per-account quota from Codex session rollouts: `bridge_list_accounts` shows the 5-hour and
  7-day `rate_limits` snapshot, `bridge_health` the remaining percentage per account. Only
  `<codex_home>/sessions/**/rollout-*.jsonl` is read; accounts without data report `UNKNOWN`.
- Opt-in `auto_switch` (`enabled`, `min_remaining_pct`, `fallback_accounts`): before `execute`
  or `resume`, a task whose account is in QUOTA state or below the threshold moves to the first
  eligible fallback account, recorded as an `account_auto_switched` event
  (`auto_switch_skipped` when none qualifies). AUTH never switches.
- Security hardening: `workspace-write` / `read-only` sandbox only, no network, credential-shaped
  environment variables scrubbed, secret redaction in stored output, process-group kill on
  timeout or cancel.
- Project-local configuration (`.ai/config.json`, `.ai/config.example.json`) with unknown keys rejected.
- `scripts/setup.sh` and `scripts/uninstall.sh` to install the kit into another project and reverse it
  exactly, with backups and an install manifest.
- Claude Code integration: `CLAUDE.md`, `.claude/rules/codex-*.md`, `codex-delegation` skill.
- Test suite with a fake `codex` that speaks the real JSONL protocol (97 tests, isolated from the
  real `~/.codex`) and an opt-in real Codex smoke test (`CODEX_SMOKE=1`).
- English and Vietnamese READMEs.

[Unreleased]: https://github.com/leetuanbmt/agent-orchestration-kit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/leetuanbmt/agent-orchestration-kit/releases/tag/v0.1.0
