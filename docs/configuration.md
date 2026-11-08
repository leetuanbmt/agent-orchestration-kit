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
| `accounts.<id>` | `provider`: `openai`, `anthropic`, `google` or `opencode`. `home`: isolated auth home; omit it to use the CLI's inherited global login (opencode accepts no `home`). `max_concurrency`: 1–8, default 1. `token_env` (anthropic only): the name of a variable holding a `claude setup-token` token, see [Accounts](#accounts). `limits`: optional per-account limits, see [Per-account limits](#per-account-limits-opt-in). |
| `agents.<id>` | `adapter`: `codex-cli`, `claude-cli`, `client`, `opencode-cli` or `agy-cli`. `account` (required except for `client`), optional `provider` (must match the adapter), `model`, `capabilities` (subset of `[read, write]`), `allow_write` (default true for `codex-cli`, false otherwise), `allow_bash` (`claude-cli` only), `timeout_s` (≥ 30), `enabled` (default true). |
| `roles.<name>` | An agent id, or `{agents: [...], default: <agent>, strategy: ordered \| explicit \| quota, requires: read \| write}`. `default` is tried first; the other agents are fallbacks in list order. `quota` picks the eligible agent whose account has the most remaining quota (unknown quota last, ties in list order). `default` cannot be combined with `strategy: explicit` or `quota`. |
| `routing.slot_wait_s` | 0–86400, default 1800. How long `bridge_run_workflow` keeps retrying, with backoff from 5 s to 60 s, a task whose account slots (or the worktree's writer slot) are held by another bridge process. `0` ends the workflow `WAITING` at once, as before. |
| `routing.quota_fallback` | `enabled` (default false), `min_remaining_pct` (default 10), `agents` (candidates in order; default: the executor role's agents), `max_switches` (1–20, default 3), `keep_sessions` (default false). See [Per-account limits](#per-account-limits-opt-in). |
| `workflow` | `stages`: ordered subset of `plan, execute, test, review`, default `[execute, test, review]`; must include `execute` and at least one of `test` / `review`. `max_rework`: 0–4, default 2. `verify_sandbox`: `auto` (default), `strict` or `none`, and `verify_writable`: extra directories checks may write; see [Verification sandbox](#verification-sandbox). |
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
| [`account-limits`](../examples/account-limits.yaml) | Claude Code session | Claude headless on two accounts, then Codex, with per-account limits and failover | Claude Code session |

### Routing

- A task pinned with `agent` (or `account`) runs there.
- Otherwise, when the task first starts, the bridge picks the role's `default`, then the first
  eligible agent in role order: enabled, CLI installed, able to write, account available and
  under `max_concurrency`, counting slots held by other bridge processes on the machine. The
  choice is logged as `agent_routed`. If another process takes the last slot before the run
  starts, the choice is undone (`agent_unrouted`) and no attempt is spent.
- With `strategy: quota` the bridge ranks every eligible agent by its account's remaining quota
  (the latest Codex rollout, or the limits a CLI reported on its last run) and picks the highest;
  unknown quota ranks after every known value. `agent_routed` then also records `remaining_pct`
  and the ranked `candidates`.
- The task then keeps that agent and its session for retries and resumes.
- `strategy: explicit` requires every task to name its agent.
- In a workflow, a task whose only usable accounts are busy in another bridge process is retried
  with backoff until `routing.slot_wait_s` passes (`slot_wait`, then `slot_wait_expired` events);
  the workflow stays `RUNNING` and lists those tasks in `detail.waiting_for_slot`.

### Stages

| Stage | Who | What happens |
|---|---|---|
| `plan` | Planner agent | `bridge_plan` runs it read-only; the bridge validates every planned task (paths, commands, dependencies) before creating any. `bridge_run_workflow` then runs the tasks in dependency order, one at a time. |
| `execute` | Executor agent | Implements one task. |
| `test` | Bridge | Re-runs the task's verification commands. |
| `review` | Reviewer | A `client` reviewer is the MCP caller (`bridge_review_task`). A reviewer agent runs read-only after the checks pass; its decision is recorded as `reviewer: <agent>`. If it fails, the task stays in `REVIEW` for the caller. |

Without a `review` stage the bridge approves work that passed every gate (`reviewer: bridge`).
High-risk work still waits for a human. Without a reviewer nobody reads the evidence, so changed
git-ignored files outside `allowed_paths` count as scope violations there (with a reviewer they
are listed in the result).

### Verification sandbox

Verification commands run code the executor wrote. The bridge confines them with Landlock on
Linux (kernel 5.13+) and with Seatbelt (`sandbox-exec`) on macOS; `agent-bridge health` shows
what applies here (`verify_sandbox`). The macOS profile allows everything except writes outside
the writable paths, AppleEvents, LaunchServices and running `open`, `launchctl` or `osascript`.
It is best-effort confinement, not a full sandbox.

| `workflow.verify_sandbox` | Effect |
|---|---|
| `auto` (default) | A check may write only to the worktree, a private `TMPDIR` (removed afterwards) and `verify_writable`; reads and the network are not restricted. Without either (Windows, old Linux kernels, or a bridge that already runs inside a sandbox) checks run unconfined and `health` reports the `verify_unconfined` problem (NOT OK) until a human sets `none`. |
| `strict` | As `auto`, and TCP connect/bind are denied (Landlock ABI 4+, kernel 6.7+, or Seatbelt). Without a sandbox, checks are refused rather than run unconfined. |
| `none` | No confinement, accepted by a human; `health` keeps a `verify_unconfined` warning. |

Test runners that write caches outside the project need them in `verify_writable`, for example
`verify_writable: [~/.cache/uv]` for `uv run pytest` or `[~/.npm]` for `npm test`; the check's
output shows `Permission denied` for anything missing. Everything a check changes inside the
worktree is diffed again after verification and judged like the agent's changes.

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
| `protected_paths` | `.env*`, `*.pem`, `*.key`, `.git/**`, `.ai/**`, `.mcp.json`, `.claude/settings*.json` | Never writable by a task and never allowed in `allowed_paths`. A project value adds to these defaults; it cannot remove them. |
| `sensitive_paths` | migrations, `auth/`, `deploy/`, `.github/workflows/`, `*secret*`, `*credential*`, `Dockerfile` | A change under these requires human approval before `DONE`. |
| `env_deny` / `env_allow` | `*_API_KEY`, `*_TOKEN`, `*_SECRET*`, `*PASSWORD*`, `AWS_*` / none | Variables removed from agent and verification environments. A project `env_deny` adds to the defaults; it cannot remove them. `env_allow` re-allows a denied variable for agent processes only, never for verification commands. |
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
- **Claude long-lived token (`claude setup-token`).** Instead of a login in `home`, an
  `anthropic` account can name an environment variable that holds the token:
  `token_env: AGENT_BRIDGE_CLAUDE_PRINTLESS_TOKEN`. The bridge reads that variable from its own environment and
  passes it as `CLAUDE_CODE_OAUTH_TOKEN` to that account's runs only. Other accounts, and
  verification commands, never see it.
  - The name must match `AGENT_BRIDGE_CLAUDE_<NAME>_TOKEN`. The namespace stops a tampered
    config from pointing at another secret, such as `GITHUB_TOKEN`. The `_TOKEN` suffix keeps it
    under the built-in scrub, so no other process inherits it.
  - Only a value starting with `sk-ant-oat` (what `setup-token` prints) is forwarded.
  - Export the variable in the shell that starts Claude Code (the MCP server inherits it) or
    runs `agent-bridge`.
  - While the variable is unset, the account reads as logged out.
  - Never write the token into `orchestrator.yaml`. The file lives in the worktree, and agents
    can read the worktree. Claude agents are denied `.ai/orchestrator.yaml`, but Codex agents
    are not.

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

### Per-account limits (opt-in)

An account can carry its own usage limits. An account without a `limits` block behaves exactly
as before. Limits live in `.ai/orchestrator.yaml` or the machine-wide account registry; both are
human-owned, and no agent, MCP tool or UI can change them.

```yaml
accounts:
  claude-main:
    provider: anthropic
    home: ~/.claude-accounts/main
    limits:
      five_hour_pct: 85          # stop at 85 % of the 5-hour window
      weekly_pct: 90             # stop at 90 % of the weekly window
      budget_usd: {day: 20, week: 100, month: 300}
      reserve_usd: 2             # held for each run until its cost is known
  codex-work:
    provider: openai
    home: ~/.codex-accounts/homes/work
    limits:
      any_window_pct: 95         # every window the provider reports
      budget_usd: {month: 50}
      pricing: {input_per_mtok: 1.25, output_per_mtok: 10, cached_input_per_mtok: 0.125}
      cooldown_s: 3600
```

| Key | Meaning |
|---|---|
| `enabled` | Default `true`. `false` keeps the checks visible in `agent-bridge usage` but enforces none of them. |
| `five_hour_pct`, `weekly_pct` | Used percent (0–100] of the provider window whose `window_minutes` is 300 or 10080. |
| `any_window_pct` | Used percent applied to every window the provider reports. |
| `budget_usd` | `day`, `week` and/or `month` caps in USD. Periods are UTC calendar periods: the day from 00:00 UTC, the ISO week from Monday, the month from the 1st. |
| `reserve_usd` | Amount held for each run on a budgeted account until its cost is known (needs `budget_usd`, at most the smallest cap). A run starts only when `spent + reserved + reserve_usd` stays within every cap; `usage`, `account-list` and `health` judge the next run the same way, so an account they show as `limited` is one the bridge will refuse. |
| `pricing` | USD per million tokens (`input_per_mtok`, `output_per_mtok`, optional `cached_input_per_mtok`), used to estimate cost when the CLI reports tokens but no cost. The kit ships no price table. |
| `cooldown_s` | 60–604800. Lifts a QUOTA mark after this many seconds when the snapshot has no future reset time to wait for. |

**How a limit is checked.** A percentage limit is breached when `used >= threshold`. A window
whose reset time has passed counts as 0 % used, so the account takes work again after the reset
without anyone clearing it. A budget is breached when the period's spend reaches the cap, or
when the next run's reserve would exceed it.

**Where it is enforced.** Routing skips an account over its limit, and the "no eligible agent"
error lists each reason. The authoritative check runs after the account's slot lock is taken
and immediately before the agent starts, for executor, planner and reviewer runs alike. On a
budgeted account the check and the reservation happen in one `BEGIN IMMEDIATE` transaction in
the machine-wide `accounts.db`, so two bridges cannot both start the last affordable run. A run
refused this way is logged as `limit_blocked` and fails with a message naming the limit and its
reset time.

**Cost sources.** Each run's cost is recorded in the shared usage ledger with its source:

| Source | When |
|---|---|
| `reported` | The CLI reported a cost (Claude's `total_cost_usd`, opencode's per-step `cost`). It is the CLI's own list-price estimate, not your bill: on a subscription login (Pro, Max, ChatGPT) it is not a charge, and `config-check` warns when such an account has `budget_usd`. |
| `actual` | A run that never started, charged 0. |
| `estimated` | Tokens times your `pricing`; or the `reserve_usd` charged for a run whose cost stayed unknown; or a run the provider refused (AUTH / QUOTA with no usage), charged 0. |
| `unavailable` | No cost, no tokens and no pricing. |

A percentage limit whose window the provider never reported shows `unavailable` and does not
block; the provider's own hard stop still pauses the task as QUOTA. A budget fails closed: a run
with unknown cost inside a period blocks that budget until the period ends. Give a budgeted
Codex account `pricing` (Codex reports tokens, not cost) or a `reserve_usd`; `config-check`
warns about a budget that has neither. Every network-retried attempt is charged on its own,
and a retry is skipped once the account is over its limit. A run that fails before its agent
starts is charged 0. A reservation whose bridge process died is charged its reserved amount (or
counted as unknown when it reserved nothing) the next time any bridge checks the account.

**Failover.** With `routing.quota_fallback.enabled`, a task whose account is over a configured
limit moves to the next eligible candidate the same way as for a QUOTA mark. Two more keys
bound it:

| Key | Meaning |
|---|---|
| `max_switches` | 1–20, default 3. Automatic moves per task; after that the bridge logs `auto_switch_skipped` ("switch limit reached") and the task waits. |
| `keep_sessions` | Default `false`. `true` refuses to move a task that already has an agent session, so its context is never dropped silently. |

When no account can take an unpinned task, the task is not started and the "no eligible
executor agent" error lists every account's reason. A bound task whose fallback finds no
eligible candidate logs `auto_switch_skipped` ("no eligible fallback"), and its start is
refused with its own account's limit and reset time. Nothing retries in the background; run
the task again after a reset or after a human changes the limits.

**Inspecting usage.** `agent-bridge usage [account]` (or the `bridge_account_usage` MCP tool)
shows each check with used, threshold, remaining, reset time, source and status. Accounts over
a limit show `over limit` in `agent-bridge account-list`, and `health` reports the status of
every account with limits.

**Limitations.**

- A run is charged to the period in which it started; a run that spans midnight UTC counts
  toward the earlier day.
- Cost is known only when a run ends, so a run is never stopped part-way when it crosses a
  budget. With `reserve_usd: 0`, concurrent runs on one account can overshoot a cap by up to
  `max_concurrency` runs.
- Percentage windows are matched by `window_minutes`; a provider that reports none gets no
  five-hour or weekly check (`any_window_pct` still applies).
- Spend is shared machine-wide per login (provider plus auth home), but thresholds come from
  each project's config: two projects that set different limits for the same login each enforce
  their own against the same spend. A project's own account declaration replaces a registry
  account of the same name, limits included.

**Troubleshooting.**

| Symptom | Cause and fix |
|---|---|
| `account X over its configured limit: budget_usd.day: ... N run(s) with unknown cost` | A run's cost could not be determined. Add `pricing` or `reserve_usd`, or wait for the period to reset. |
| A task waits with `auto_switch_skipped: switch limit reached` | The task moved `max_switches` times. Check `agent-bridge usage`, then resume it after a reset. |
| `usage` shows a check as `unavailable` | The provider has not reported that window yet. Run one task on the account, or use `any_window_pct`. |

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
