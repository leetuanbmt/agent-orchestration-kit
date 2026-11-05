# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- The snapshot records git-ignored files (size and mtime; about 0.2 s for 100 000 files), so changes under ignored paths are listed in `changes.ignored_files` and shown to the reviewer. A changed ignored file that is protected (e.g. a nested `.env`) is a violation; in a workflow without a review stage, where nobody reads the evidence, any changed ignored file outside `allowed_paths` is a violation too (tool caches excepted). Before, such writes were invisible (audit SEC-03).
- Approving or closing a task requires every file its last attempt touched to still have the verified content (sha256 recorded after verification); otherwise the approval is refused with the files and how to re-verify. Edits to other files do not matter. Attempts verified before this change are approved as before, with an `approval_unbound` event (audit SEC-05).
- The stored patch keeps every file's diff from its start: files share the size limit, a cut file says how much is missing, files without room are listed in `patch_omitted_files`, and `patch_truncated` is set. The reviewer prompt lists every changed file and names what it must read in the worktree. Before, the patch kept only its tail, so a file early in the diff could vanish from the evidence (audit SEC-06).
- A run that changes a protected file (`.ai/`, `.env*`, keys, `.git/`, `.mcp.json`, Claude settings) is BLOCKED with `PROTECTED: <paths>` instead of sent back to the agent, so nothing runs on a possibly tampered worktree until a human looks. `health` lists tracked protected files that differ from HEAD (`protected_dirty`) and reports NOT OK while there are any. Changes to agent instruction files (`CLAUDE.md`, `AGENTS.md`, `.claude/**`, `.codex/**`, `.envrc`, `.vscode/**`) always need human approval (audit SEC-02).
- The scope check also covers the verification step: after the checks run, the worktree is diffed again against the same snapshot and anything they changed or left behind is judged like the agent's changes (test caches excepted, listed as `changed_by_verification`). When an agent or a check exits, whatever it left running in its process group is ended (TERM, grace, KILL) before its pipes are awaited, so background processes can no longer write after the snapshot (audit SEC-01 fixes 1-2, SEC-04).
- Claude agents' read tools can no longer open `~/.claude/.credentials.json`, the project's `.env*`, `*.pem` and `*.key` files, or any configured account's auth home (`--disallowedTools` now denies them on every run; a home that cannot be written as a permission rule stops the run). Before, only Codex, agent-bridge and agy credentials were denied (QA-BUG-006).
- Claude loads no user/project settings; optional `claude_hook_settings`, set only by a human in `.ai/config.json`, loads an external hooks-only JSON file as trusted code running unsandboxed. The default remains off and `--setting-sources ""` remains enforced.
- `agent-bridge web` requires a per-launch secret token on every API request; the printed dashboard URL carries the key.

### Added
- End-to-end suite `tests/qa` (`scripts/check.sh --e2e`): the real CLI, MCP server and web dashboard (Playwright) against fake agents in a throwaway home; known defects are strict `xfail` tests. Test cases and the QA report are in `docs/qa/`.
- Per-account limits (`accounts.<name>.limits` in `.ai/orchestrator.yaml`): 5-hour, weekly and any-window percentage thresholds, USD budgets per UTC day / week / month with an optional per-run `reserve_usd`, user-supplied token `pricing` for cost estimates, and a QUOTA-mark `cooldown_s`. Routing skips an account over its limit and the start gate refuses it; budgets are checked and reserved atomically in the machine-wide `accounts.db`, so concurrent bridges cannot overspend. Accounts without `limits` behave as before.
- Usage ledger: every run's cost, including network-retried attempts, is recorded with its source (`actual`, `estimated` or `unavailable`); a run with unknown cost fails closed against a budget.
- `agent-bridge usage [account] [--json]` and the read-only `bridge_account_usage` MCP tool show used, threshold, remaining, reset time, source and status per limit; `account-list` marks an account `over limit`; `health` reports each account's limit status; `config-check` warns about a budget that would fail closed on every run.
- `routing.quota_fallback` also moves a task off an account that is over a configured limit, bounded by `max_switches` (default 3) and optionally refused for tasks with a session (`keep_sessions`).
- Example `account-limits` (`agent-bridge config-init account-limits`).
- Human-run `storage relink <old-root> <new-root> [--apply]` reattaches central history after moving or renaming a repository or worktree, with a dry run by default.
- `storage prune [--apply]` removes missing roots from the project registry, and
  `storage gc --logs-older-than DAYS [--apply]` deletes old central attempt logs of terminal tasks;
  both preview changes by default and refuse to apply while bridge processes are running.
- Human-run `storage rollback <root> [--yes]` archives migrated central stores, and
  `storage cleanup-legacy [--apply]` moves verified legacy state into backups without deleting files.
- New repositories keep bridge state under `~/.agent-bridge/projects/<project>/worktrees/<worktree>/`
  (`AGENT_BRIDGE_HOME` overrides), so history survives deleting the worktree. `storage status` and
  `storage migrate [--apply]` copy existing git-dir state there; `health` flags legacy storage and
  writes to a legacy database after migration.
- `agent-bridge web [--port N] [--open]`: read-only localhost dashboard for known projects
  and worktrees, with progress, tasks needing attention, activity and request details in VI/EN.
- Agent executions append redacted stdout and stderr lines to per-attempt JSONL logs and report
  meaningful progress events on stderr.

### Changed

- README and README.vi restructured and checked against the code; the full configuration and
  CLI/MCP reference moved to `docs/configuration.md` and `docs/cli-reference.md`.
- Read-only commands (`health`, `status`, `result`, `list`, `agents`, `workflow-status`,
  `account-list`) no longer create `.ai/`, state, databases or registry entries.
- Updates to `~/.agent-bridge/projects.json` hold `projects.lock`, so concurrent bridges
  and `storage prune` no longer drop each other's entries.
- Centralize platform operations for Windows locks, process groups and process discovery; real Windows testing remains pending.
- Share account quota/auth marks and reported quota machine-wide in `$AGENT_BRIDGE_HOME/accounts.db`; `account-clear` lifts marks across projects.
- Account slot locks default to `~/.agent-bridge/locks` (`$AGENT_BRIDGE_HOME/locks`)
  across projects and processes; bridge databases and WAL/SHM sidecars use private 0600 permissions.
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
- A verification command whose runner died (crash, `kill -9`, closed terminal) is killed on the next bridge start: the running check's pid and start time are recorded like the agent's, so `recover` finds it (audit REL-02, recovery half).
- Processes opening a project (or the machine-wide ledger) for the first time at the same moment no longer crash with `duplicate column name` or `database is locked`: missing columns are added inside one `BEGIN IMMEDIATE` transaction, and switching a new file to WAL is retried (QA-BUG-007).
- A failing git command while the bridge reads the worktree (snapshot or diff) pauses the task with `EVIDENCE_ERROR` and gives the attempt back, instead of yielding empty evidence that passed every gate (audit REL-01).
- The web dashboard fits a phone screen: filters and status chips wrap and only the task table scrolls sideways, in its own box; the whole page scrolled 541 px sideways at 375 px wide (QA-BUG-005).
- Verification commands with unquoted shell operators (`&&`, `|`, `;`, `>`, `2>&1`, ...) are refused at task creation. They run without a shell, so the operator and everything after it were passed as arguments and a chained check silently never ran (QA-BUG-004).
- `agent-bridge create` reports a missing, unreadable or non-object spec file as one `error:` line naming the file instead of a Python traceback, and unknown-task errors no longer print in quotes (QA-BUG-003).
- `usage`, `account-list` and `health` include the next run's `reserve_usd` when judging a budget, as routing and the start gate do; they showed an account as `ok` while the bridge refused it (QA-BUG-002).
- Routing no longer binds an unpinned task to an account whose slots are all held by other
  bridge processes on the machine (it then failed with `busy in another bridge process` while
  another executor was free): such an account is now unavailable to routing and `agents`. A
  fresh routing that still loses the slot before the run starts is undone (`agent_unrouted`)
  and spends no attempt.
- A stop signal (SIGTERM, SIGINT, SIGHUP, SIGALRM, e.g. a caller's timeout) to `execute`,
  `resume`, `plan` or `run-workflow` no longer leaves the agent running with ppid 1 and the task
  `RUNNING`: the bridge kills the agent or the running check, parks the task `PAUSED`
  (`INTERRUPTED`, attempt given back) and exits 128 + signal. `serve` shuts down the same way.
- `cancel` from another process now stops that process's verification too: the running check
  is killed, the rest are skipped and the account lock is released before `cancel` returns
  (up to 15 s). Previously the runner kept running every check and held the account.
- An agent that reports `blocked` without changing anything no longer triggers the
  verification commands; the attempt records `verification.skipped`.
- The executor prompt no longer tells the agent to stop at once when part of the task is out
  of scope: it does every in-scope step first, keeps that work, lists what is out of scope
  last, and follows the spec's `context` on how to handle blockers.
- Let QUOTA marks lapse when a window open at mark time has reset and snapshot quota is positive; AUTH and accounts without snapshots still require `account-clear`.
- `init` works when `.claude/skills` (or another kit directory) is a symlink to a directory
  inside the project, such as a shared `.agents/skills`: the link is followed and the skill is
  installed there. Links leaving the work tree or entering `.git`, and symlinked files, are
  still refused. Retired `codex-*` paths are only looked at when the manifest lists them.
- `account-list`, `agents` and the dashboard no longer start agy to check a google account's
  login: run under the account's HOME, agy made macOS show "Keychain Not Found" on every
  refresh. The check reads the token file's size instead.
- Tasks the workflow driver starts or resumes are recorded with actor `bridge` instead of
  `client`, so the audit trail tells automatic retries from explicit calls.
- The dashboard quota bar and `account-list` ignored a window's reset time, so a reset 5h
  window still showed as nearly used up; they now use the same calculation as routing.
- A successful run no longer resets its account to available: with two runs on one account
  (`max_concurrency` 2, or a reviewer sharing it) it could lift the other run's AUTH or QUOTA
  mark; only `account-clear` lifts it now.
- A workflow whose planning or driving process died no longer stays PLANNING or RUNNING forever:
  workflows record their owner pid, and the next bridge start fails an orphaned plan and parks
  an orphaned run as WAITING, leaving workflows of a live process alone.
- Planning the same objective twice no longer hands the second workflow the first one's task,
  which it never listed or drove; planned tasks dedup only inside their own workflow.
- Orphan cleanup after a dead runner kills an agent's process group only when the pid's start
  time matches the one recorded at launch; it used to match any command line containing
  `claude`, `codex`, `opencode` or `agy`, so a reused pid could take down the user's own session.
- Paths with `,`, `(`, `)`, quotes or whitespace (a Next.js `app/(auth)/**`) are accepted again;
  routing skips a Claude writer for them and a task pinned to one is refused before it starts,
  since Claude permission rules cannot hold them. Control characters (newlines) are refused for
  every task: they would forge lines in the agent prompt.
- An agent that wrote the git-ignored `.ai/orchestrator.yaml` (dropping the review stage, say)
  passed the scope check; `.ai/config.json` and `.ai/orchestrator.yaml` are now hashed before
  and after every run.
- Claude agents start with `--setting-sources ""`: with `project`, the target repo's
  `.claude/settings.json` allow rules widened the tool allowlist and its hooks ran. The kit's
  credential `Read(...)` denies now go in `--disallowedTools` on every run, read-only included.
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
- `agent-bridge tasks` lists tasks from any directory, grouped by project: every project the
  bridge has run in (recorded in `~/.agent-bridge/projects.json`) and each git worktree, with
  per-state counts and the newest tasks (`--state`, `--limit`, `--json`). Databases are opened
  read-only.
- A role can name its `default` agent (`executor: {agents: [a, b], default: b}`): unpinned tasks
  try it first and fall back to the other agents in list order, so the default no longer has to
  be first in the list. Not allowed with `strategy: explicit`.
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
