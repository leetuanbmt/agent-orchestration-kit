# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Configurable orchestration in `.ai/orchestrator.yaml`: accounts (provider, isolated home,
  `max_concurrency`), agents (adapter, account, model, capabilities), roles (planner, executor,
  reviewer, custom; ordered or explicit), routing (opt-in quota fallback) and workflow stages
  (`plan`, `execute`, `test`, `review`). Validated with descriptive errors;
  `codex-bridge config-check` checks it.
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
- Recursion guard: agent processes get `CODEX_BRIDGE_AGENT=1` and the bridge refuses to start
  under it.
- Example configs in `examples/` and an opt-in real Claude smoke test.

### Changed
- Rules and skill are named after roles: `.claude/rules/orchestration.md`, `delegation.md`
  (now including review), `security.md`, and the `agent-orchestration` skill. `setup.sh`
  removes unedited `codex-*` files from earlier installs.
- `reassign` accepts an agent or an account; `account-login` handles Claude accounts
  (`claude auth login` into `CLAUDE_CONFIG_DIR`).
- The verification record's `codex_status` is now `agent_status`.
- Without `orchestrator.yaml` behaviour is unchanged; with it, `accounts` or an enabled
  `auto_switch` in `config.json` is rejected (both moved to the YAML).
- New dependency: `pyyaml`.
- Project renamed to `agent-orchestration-kit` (GitHub repository and Python package name). The
  `bridge` module, the `codex-bridge` CLI and the MCP server name are unchanged, so existing
  installs keep working; the old GitHub URL redirects.

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
