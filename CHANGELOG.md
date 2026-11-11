# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Claude `allow_bash` runs Bash and child processes in Claude Code’s required OS sandbox: credential paths denied, writes only to the working directory and `$TMPDIR`, no network, no unsandboxed retries or excluded commands. Owner hooks merge into the same settings file. Read/Edit retain their permission rules. macOS Keychain remains reachable; denies for `security` and `/usr/bin/security` are permission-rule speed bumps, not a boundary.
- `.ai/config.json` is tracked, so a committed `"verify_sandbox": "none"` applies to every clone. Review changes to that file as security changes. Agents cannot write `.ai/`.
- The `Bridge` Python API refuses to start inside an agent (`AGENT_BRIDGE_AGENT`), as the CLI and the MCP server already did, so an agent cannot call `human_approve` through an import. The README, `docs/REPORT.md` and the security rules now describe the trust model: everything runs as your user, human approval guards against mistakes and model overreach rather than code running as you, and what is and is not enforced (audit SEC-08).
- An attempt that ends early (quota, auth, network, crash, timeout, bridge shutdown, or a runner that died) is checked for scope too: changes outside the task block it with `SCOPE: ...` instead of pausing or failing it. Before, a resume took those changes as its baseline and never flagged them, so a task could close DONE with an out-of-scope file in place. Each attempt stores its starting snapshot so recovery can diff it (QA-BUG-009).
- Input hardening: a verification command must name an allowlisted program, not a path (`/tmp/x/pytest` passed the basename check); `SSH_AUTH_SOCK`, `*_DSN`, `*DATABASE_URL*`, `*CREDENTIALS*`, `KUBECONFIG` and `DOCKER_AUTH_CONFIG` are always stripped from agent and check environments unless `env_allow` names them; patterns that cover the whole project such as `**/*` are refused; `workflow-status` shows each planned task's verification commands and the skill asks to read them before running a workflow (audit SEC-07, SEC-09, SEC-10).
- The snapshot also covers the repository's git hooks and `.git/config` (via `git rev-parse --git-path`, so linked worktrees too): a hook or config change made by the agent or by a check is a protected violation and blocks the task. In a main checkout `.git` is inside the worktree, and a planted hook would run outside any sandbox at the next commit (QA-BUG-008).
- Verification commands run confined on Linux with Landlock (`workflow.verify_sandbox: auto`, the default): they may write only to the worktree, a private `TMPDIR` and `workflow.verify_writable`. `strict` also denies TCP and refuses to run checks without Landlock; `none` turns it off. `health` shows the mode. Test runners that write caches outside the project need them in `verify_writable` (e.g. `~/.cache/uv`) (audit SEC-01 fix 3, QA-BUG-001).
- The snapshot records git-ignored files (size and mtime; about 0.2 s for 100 000 files), so changes under ignored paths are listed in `changes.ignored_files` and shown to the reviewer. A changed ignored file that is protected (e.g. a nested `.env`) is a violation; in a workflow without a review stage, where nobody reads the evidence, any changed ignored file outside `allowed_paths` is a violation too (tool caches excepted). Before, such writes were invisible (audit SEC-03).
- Approving or closing a task requires every file its last attempt touched to still have the verified content (sha256 recorded after verification); otherwise the approval is refused with the files and how to re-verify. Edits to other files do not matter. Attempts verified before this change are approved as before, with an `approval_unbound` event (audit SEC-05).
- The stored patch keeps every file's diff from its start: files share the size limit, a cut file says how much is missing, files without room are listed in `patch_omitted_files`, and `patch_truncated` is set. The reviewer prompt lists every changed file and names what it must read in the worktree. Before, the patch kept only its tail, so a file early in the diff could vanish from the evidence (audit SEC-06).
- A run that changes a protected file (`.ai/`, `.env*`, keys, `.git/`, `.mcp.json`, Claude settings) is BLOCKED with `PROTECTED: <paths>` instead of sent back to the agent, so nothing runs on a possibly tampered worktree until a human looks. `health` lists tracked protected files that differ from HEAD (`protected_dirty`) and reports NOT OK while there are any. Changes to agent instruction files (`CLAUDE.md`, `AGENTS.md`, `.claude/**`, `.codex/**`, `.envrc`, `.vscode/**`) always need human approval (audit SEC-02).
- The scope check also covers the verification step: after the checks run, the worktree is diffed again against the same snapshot and anything they changed or left behind is judged like the agent's changes (test caches excepted, listed as `changed_by_verification`). When an agent or a check exits, whatever it left running in its process group is ended (TERM, grace, KILL) before its pipes are awaited, so background processes can no longer write after the snapshot (audit SEC-01 fixes 1-2, SEC-04).
- Claude agents' read tools can no longer open `~/.claude/.credentials.json`, the project's `.env*`, `*.pem` and `*.key` files, or any configured account's auth home (`--disallowedTools` now denies them on every run; a home that cannot be written as a permission rule stops the run). Before, only Codex, agent-bridge and agy credentials were denied (QA-BUG-006).
- Claude loads no user/project settings; optional `claude_hook_settings`, set only by a human in `.ai/config.json`, loads an external hooks-only JSON file as trusted code running unsandboxed. The default remains off and `--setting-sources ""` remains enforced.
- `agent-bridge web` prints a one-time link (valid ten minutes) instead of a URL with a reusable token. Opening it sets an HttpOnly, SameSite=Strict session cookie that is never written to disk or visible to page scripts, and sessions expire after 12 idle hours. Every API request must come from the dashboard's own origin (Origin and Sec-Fetch-Site are checked). Before, the token sat in the URL and in session storage, and a probe found it in the browser profile on disk.

### Added
- Read-only adapter `cursor-cli` (provider `cursor`, the Cursor CLI `cursor-agent`) for planner and reviewer roles, with `cursor_bin` in the config. It runs `cursor-agent -p --output-format stream-json --mode ask --sandbox enabled --trust`; in ask mode the model reported writes and shell blocked and wrote nothing (cursor-agent 2026.11.11). It refuses a directory whose `.cursor/hooks.json`, `.claude/settings.json` or `.claude/settings.local.json` has hooks (or the latter two are not a JSON object), which Cursor would execute outside its sandbox: the agent is listed unavailable and `health` warns (`adapter_refused`). `--trust` leaves the worktree trusted under `~/.cursor`. A cursor account with `limits.budget_usd` or `limits.pricing` is rejected because Cursor reports no cost; the login check runs without `CURSOR_API_KEY`; `cli_unprobed` covers cursor-agent above 2026.11.11. Its only account is the global `cursor-agent login`: `account-add <name> --provider cursor` registers one without a home.
- Human-only `project-agent-add <agent> (--account A | --adapter client)` and `project-agent-remove <agent>` (`--apply`, `--json`) add or remove an agent in `.ai/orchestrator.yaml`, with `--role`/`--default` for role membership. They preview by default, validate the whole result, back up to `.ai/backup-<stamp>/` and write atomically (comments in `orchestrator.yaml` are not kept). `project-account-add` now points to `project-agent-add`, and `project-account-remove` names the agents still using the account and `project-agent-remove`.
- Human-only `config-set <key> <value>` and `config-unset <key>` (`--apply`, `--json`) change one setting in the file that owns it: `accounts` in `.ai/accounts.yaml`, `version|agents|roles|routing|workflow|security` in `.ai/orchestrator.yaml`, bridge settings in `.ai/config.json`. The value is parsed as YAML. They preview by default; `--apply` backs the file up to `.ai/backup-<stamp>/` and writes atomically. The whole configuration is validated first, and a change that would create an outdated layout, an account outside `accounts.yaml`, or a secret-looking key is refused with nothing written. `config.json` security settings are flagged.
- `docs/configuration-guide.md`: task-by-task setup. It covers the three `.ai/` files, adding accounts (new login, machine login, Keychain token or API key), limits, roles, the verification sandbox, migrating an older project and the common errors.
- Human-only `config-migrate [--apply]` moves accounts and agents to their owning files. It previews by default, backs up to `.ai/backup-<stamp>/`, and refuses when the effective configuration would change. Registry limits are copied so no cap is lost.
- Task actions in the web dashboard's task drawer: review a task in REVIEW (approve, rework or block, with notes), run a READY task, resume one in REWORK, FAILED or PAUSED with optional feedback, and cancel an open task with a reason. Each runs the same `agent-bridge` CLI command; high-risk approval, reassignment and account commands stay in the terminal. `agent-bridge web --read-only` hides them and answers POST with 405.
- `strategy: quota` for a role in `.ai/orchestrator.yaml`: an unpinned task goes to the eligible agent whose account has the most remaining quota; unknown quota ranks last and ties keep list order. It applies only when a task is first routed, cannot be combined with `default`, and `agent_routed` records the ranked candidates. Roles without it behave as before.
- `health` returns `problems` (each cause of NOT OK, with a code, message and fix) and `warnings` (accounts marked QUOTA or AUTH, accounts over a limit, unconfined verification, legacy storage); the table view prints their fixes. `ok` is computed as before.
- `agent-bridge --help` gives every subcommand a one-line help, keeps the usage line short (`<command>`) and ends with a "getting started" list; `agent-bridge init` ends with the next steps (`health`, trust the folder in `claude`, the dashboard) instead of a single hint. The `updated <file>` lines are unchanged.
- `CONTRIBUTING.md` with setup, checks and conventions. The README (English and Vietnamese) gains a CI badge, a comparison with a single self-reporting agent, typical uses, an architecture diagram of the real components, an FAQ and a roadmap that lists only planned or considered work.
- CI (`.github/workflows/ci.yml`): `scripts/check.sh` on Ubuntu and macOS, and the end-to-end suite with Chromium on Ubuntu; read-only token, no secrets (audit PR-12).
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
- `agent-bridge init` now takes the installed deny list from `templates/claude-settings.json`, so the kit repository's own `.claude/settings.json` no longer changes what projects get.
- The MCP server reloads `.ai/config.json`, `.ai/orchestrator.yaml`, `.ai/accounts.yaml` and the machine account registry on its next tool call instead of needing a `/mcp` reconnect. It defers while one of its own tasks runs, and an invalid edit keeps the previous settings and shows as `config_reload_failed` in `health`.
- `config-check`, `config-migrate` and `project-account-add` print a readable view in a terminal (moves, proposed files, the line to paste, next commands) instead of raw JSON. `--json`, or output that is not a terminal, still prints the same JSON.
- Every setting has one owner. `.ai/accounts.yaml` holds the project's accounts and their limits, `.ai/orchestrator.yaml` holds agents, roles, routing and workflow, and `.ai/config.json` holds safety and runtime settings, now including `verify_sandbox` and `verify_writable`. Older layouts load unchanged; `config-check` lists them under `deprecated` and `health` warns `config_deprecated`. A name, or a verify key, declared in two places is an error naming both files instead of a silent override.
- Machine registry logins no longer enter every project. A project declares one in `.ai/accounts.yaml` with `login: <name>` and its own limits. An agent that names a registry login directly still runs with the registry settings, as deprecated. Machine-wide account commands still find every login.
- `project-account-add` writes only the account (new `--login` option) and prints the agent line to paste into `orchestrator.yaml`. Errors about an account name the file that owns it.
- `config-init <example>` writes `.ai/orchestrator.yaml` and `.ai/accounts.yaml` from an example pair (`examples/<name>.yaml` + `<name>.accounts.yaml`) and refuses if either exists.
- Web dashboard redesign. An Overview home answers what is waiting on you (with the command to copy), what is running (time used against the timeout) and whether your accounts have quota and free slots, with the quota-fallback policy in one line. A new Accounts view shows one card per account (state, each quota window as a meter with its reset, slots in use, agents) plus each project's fallback policy and the recent automatic switches. A sidebar holds the views (Home, Tasks, Board, Accounts, Settings) and the project list that scopes every view (off-canvas behind a menu button below 1024 px); language and theme are in the Settings view, status filters are tabs, columns and filters that would repeat one value are hidden, the task table fits next to the sidebar without horizontal scrolling, task ids carry their project name when more than one project is shown (ids restart at T0001 in every project), Home shows six queue items and eight project cards with a toggle for the rest, Accounts shows one fallback summary across projects after the account cards, long changed-file lists in the drawer are grouped by directory with cache folders closed, a task from a planner workflow shows the workflow's steps and progress in the drawer and its workflow id in the task list (searchable), controls are at least 24 px and axe-core reports no violation, an opt-in visual regression test compares the main views with committed baselines, and colors, type and spacing come from one token set in both themes. `view=projects` links open the Overview. Design rules and the accessibility checks are in `docs/design/web-dashboard.md`.
- `/api/overview` gains `accounts` (state, quota windows, running and `max_concurrency`, agents and projects per account), `switches` (automatic quota switches of the last 7 days) and a `fallback` policy per project, read only from each project's database and config. Existing fields are unchanged.
- Web dashboard reworked around what needs you. A summary row counts tasks that need you, wait for review, run, and finished in the last 24 hours; a queue lists the tasks waiting on you in the bridge's priority order with the command to copy (`[` and `]` step through them). A Board view groups tasks into Needs you, In review, Running (elapsed time against the timeout) and Queued lanes, with finished work folded underneath. Every state shows a symbol and a label, not color alone, and a polite live region announces when the number of tasks needing you changes.
- Dashboard task drawer: status, a lifecycle stepper and the next step with its command come first; verification checks read as pass or fail; the agent's final JSON is shown as summary, notes, tests and files, with the raw text kept below; raw fields are folded under *Technical details*. The page behind the drawer is `inert` while it is open.
- Dashboard states and themes: a loading skeleton instead of a false "no task matches" before the first response; an offline banner with the time of the last data; light, dark and system themes from color tokens (stored per browser); on a phone each task is a card with labelled fields and the view buttons share one row. `/api/overview` and every other API are unchanged.
- `bridge_run_workflow` no longer stops `WAITING` when the next task's account slot is held by another bridge process: it retries with backoff (5 s doubling to 60 s) for up to `routing.slot_wait_s` (default 1800 s) and lists the waiting tasks in `detail.waiting_for_slot`; shutdown and cancel end the wait within about a second. Set `routing.slot_wait_s: 0` for the old behaviour.
- A read-only task can no longer run beside another task in the same worktree; parallel work needs separate git worktrees, as the README already says (audit REL-07).

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
- The web dashboard no longer shows stopped work as in progress.
  - A request gets an active stage only from tasks that can still move on their own (BACKLOG, READY, RUNNING, VERIFYING, REVIEW, APPROVED). BLOCKED, FAILED, PAUSED and REWORK tasks show through their status instead.
  - A REWORK task is listed under "Needs you" as "Waiting for resume" with its `agent-bridge resume` command, no longer under "waiting".
  - Tasks of a deleted project folder no longer fill the attention list, requests or open counts; the project card shows how many unfinished ones it has, and their history stays readable.
- Recovery details: a task's runner is recognised by pid and start time, so a reused pid no longer keeps a dead run's task RUNNING; a bridge error on a busy database still takes the task out of RUNNING (the rescue is retried with backoff); a worktree runs one active task at a time, read-only ones included (audit REL-04, REL-05, REL-07).
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
