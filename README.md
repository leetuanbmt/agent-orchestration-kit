<h1 align="center">agent-orchestration-kit</h1>

<p align="center">
  A project-local, provider-agnostic harness for <b>multi-agent coding workflows</b>.<br>
  Roles (planner, executor, reviewer) map to agents; agents run on CLIs (Codex, Claude Code) with isolated accounts.<br>
  A small MCP server (<code>codex-bridge</code>) owns task state, runs agents safely and re-verifies their work.<br>
  Everything lives in the repository; no global configuration is touched.
</p>

<p align="center">
  <b>English</b> · <a href="README.vi.md">Tiếng Việt</a>
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey">
  <img alt="python" src="https://img.shields.io/badge/python-3.12-blue">
  <img alt="dependencies" src="https://img.shields.io/badge/dependencies-mcp%20%7C%20pyyaml-brightgreen">
  <img alt="tests" src="https://img.shields.io/badge/tests-144%20passed%20%7C%202%20opt--in-brightgreen">
  <img alt="version" src="https://img.shields.io/badge/version-0.1.0%20%2B%20unreleased-blue">
</p>

---

When one AI agent both writes the code and judges it, nobody checks the work: "done" means
whatever the agent says, even if the tests never ran or it edited files outside its assignment.

`agent-orchestration-kit` separates the jobs and lets you choose who does each one:

- **Roles**: `planner` splits work into tasks, `executor` implements one task at a time,
  `reviewer` decides approve / rework / block. Custom roles can be added.
- **Agents**: an adapter (`codex-cli`, `claude-cli`, or `client`, the MCP caller itself), one
  account, an optional model, read/write capabilities.
- **Accounts**: isolated logins (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`), each with its own
  concurrency limit and quota.
- **`codex-bridge`**: keeps task state in SQLite, routes tasks to agents, runs them with the
  narrowest permissions, checks which files changed and re-runs the verification commands
  itself. An agent's own report is never treated as evidence.

```
user ─▶ MCP client (Claude Code) ──MCP stdio──▶ codex-bridge ──argv, own process group──▶ codex exec --json
          (plan, review, or just drive)          (roles → agents → accounts,              claude -p --output-format stream-json
                                                  scope check, verification, audit log)   (plan / implement / review)
```

Without `.ai/orchestrator.yaml` the kit behaves exactly as 0.1: the Claude Code session plans
and reviews, Codex executes, accounts come from `.ai/config.json`.

Everything is project-local: `.mcp.json`, `.claude/`, `CLAUDE.md`, `.ai/`, and runtime state in
the repository's git directory (`.git/codex-bridge/`). Nothing under `~/.claude`, `~/.codex` or
global MCP config is modified.

---

## Installation

**Requirements:**
- macOS or Linux, git, [uv](https://docs.astral.sh/uv/) (Python 3.12 is fetched by uv).
- At least one agent CLI, logged in with its own subscription: Codex CLI (tested 0.163.0,
  `codex login status`) and/or Claude Code (tested 2.1.306, `claude auth status`). No API key
  needed.

```bash
git clone https://github.com/leetuanbmt/agent-orchestration-kit.git
cd agent-orchestration-kit
uv sync                                   # project-local .venv
scripts/check.sh --with-health            # tests + CLI reachability
claude                                    # once: trust the folder → codex-bridge connects
claude mcp list                           # codex-bridge: ✔ Connected
```

`.mcp.json` registers the server with `${CLAUDE_PROJECT_DIR:-.}` paths and
`.claude/settings.json` enables it (`enabledMcpjsonServers`). Claude Code ignores a project's own
approvals until the folder is trusted, which is why the first interactive start is needed.

### Use it in another project

```bash
scripts/setup.sh /path/to/project [--force]     # merge .mcp.json, settings, CLAUDE.md block, rules, skill
scripts/uninstall.sh /path/to/project [--purge] # reverse exactly what setup added
```

- Existing `.mcp.json` and `.claude/settings.json` are merged key by key (other servers, deny
  rules and settings stay); `CLAUDE.md` gets a marked block (`<!-- codex-bridge:start/end -->`).
  Every file setup changes is first copied to `.ai/backup-<UTC timestamp>/`.
- Rules are installed as `.claude/rules/{orchestration,delegation,security}.md` and the skill as
  `.claude/skills/agent-orchestration/`. Files an older install left under the `codex-*` names
  are removed when unedited (kept with a warning otherwise). A different file already at a
  destination, or a different `codex-bridge` server, stops setup unless `--force` is given.
- The target's `.mcp.json` points at this kit by **absolute path** (`uv run --project <kit>`):
  it is machine-specific; moving the kit means running setup again.
- The install manifest is kept in `<git-dir>/codex-bridge/install-manifest.json`, out of any
  agent's reach. Uninstall removes only what setup added and keeps files you edited since (with
  a warning). `--purge` also deletes the target's `.ai/` directory, including its config.

---

## Quick start

### 1. Pick a setup

Copy one of the examples to `.ai/orchestrator.yaml` (or keep none for the legacy setup), then:

```bash
uv run codex-bridge config-check   # schema check with descriptive errors
uv run codex-bridge agents         # who plans, executes, reviews; who is available now
```

| Example | Planner | Executor | Reviewer |
|---|---|---|---|
| *(no file, legacy)* | Claude Code session | Codex (per `config.json` account) | Claude Code session |
| [`examples/orchestrator.yaml`](examples/orchestrator.yaml) | Claude headless | Codex on two accounts (fallback order) | Claude headless |
| [`examples/claude-executor.yaml`](examples/claude-executor.yaml) | Codex | Claude headless (`allow_write`) | Codex |
| [`examples/single-agent.yaml`](examples/single-agent.yaml) | Codex | Codex | Codex |
| [`examples/gates-only.yaml`](examples/gates-only.yaml) | — | Codex | none: deterministic gates only |

### 2. Ask Claude Code for the change

The `agent-orchestration` skill drives either flow:

```
you plan:      bridge_create_task → bridge_execute_task → bridge_task_status(wait_s)
                 → bridge_task_result → bridge_review_task (→ bridge_resume_task for rework)
agent plans:   bridge_plan → bridge_workflow_status(wait_s) → bridge_run_workflow
                 → bridge_workflow_status (DONE / INCOMPLETE / WAITING)
```

### 3. Check status

```bash
uv run codex-bridge health        # adapters, remaining quota per account, task counts
uv run codex-bridge account-list  # accounts, login state, codex-switch homes/profiles
uv run codex-bridge list          # tasks, newest first
```

---

## Roles and agents (`.ai/orchestrator.yaml`)

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
| `accounts.<id>` | `provider` (`openai` \| `anthropic`), `home` (isolated auth home; omit for the inherited global login, used read-only), `max_concurrency` (1–8) |
| `agents.<id>` | `adapter` (`codex-cli` \| `claude-cli` \| `client`), `account`, `model`, `capabilities` (`[read, write]` subset), `allow_write` (claude-cli only writes when true), `allow_bash` (claude-cli), `timeout_s`, `enabled` |
| `roles.<name>` | an agent id, or `{agents: [...], strategy: ordered \| explicit, requires: read \| write}`. `executor` is required and must be able to write; `planner`/`reviewer` need read |
| `routing.quota_fallback` | `enabled`, `min_remaining_pct`, `agents` (candidates in order; default: the executor role) |
| `workflow` | `stages` (ordered subset of `plan, execute, test, review`; needs `execute` and at least one gate), `max_rework` (0–4) |

Unknown keys, unknown ids, a provider/adapter mismatch, two accounts sharing one home, or a role
no listed agent can play are rejected with the field path in the message.

- **Routing.** A task pinned by `agent` (or `account`) runs there. Otherwise the first eligible
  executor in role order is chosen when it first starts (enabled, CLI installed, can write,
  account available and under `max_concurrency`), logged as `agent_routed`; the task then keeps
  that agent and session. `strategy: explicit` requires every task to name its agent.
- **Review stage.** A `client` reviewer is the MCP caller. A reviewer agent runs read-only after
  the bridge's checks pass; its decision is recorded with `reviewer: <agent>`. If it fails, the
  task stays in REVIEW and the caller decides. Without a `review` stage the bridge approves work
  that passed every gate (`reviewer: bridge`); high-risk work still waits for a human.
- **Plan stage.** `bridge_plan` runs the planner agent read-only; the bridge validates every
  planned task (paths, commands, dependencies) before creating any, then `bridge_run_workflow`
  executes them in dependency order, one at a time.

---

## Task lifecycle

States: `BACKLOG → READY → RUNNING → VERIFYING → REVIEW → APPROVED → DONE`, plus `REWORK`,
`FAILED`, `PAUSED`, `BLOCKED`, `CANCELLED`.

| Move | Who | Trigger |
|---|---|---|
| `RUNNING → VERIFYING` | bridge | the agent exited cleanly |
| `VERIFYING → REVIEW` / `REWORK` | bridge | scope clean and every verification command passed / any check failed |
| `RUNNING → PAUSED` | bridge | quota, auth, network, missing CLI, interrupted runner (does not consume the attempt budget) |
| `REVIEW → APPROVED` / `REWORK` / `BLOCKED` | client or reviewer agent | `bridge_review_task` / reviewer run |
| `REVIEW → APPROVED` | bridge | no review stage and every gate passed |
| `APPROVED → DONE` | bridge, or a **human** for high risk | quality gates re-checked |

A task with `risk_level: high`, or a change under a sensitive path (migrations, auth, deploy, CI,
secrets), stops at `APPROVED` until a human runs `codex-bridge approve <id>` in a terminal.

---

## Command reference

### MCP tools

| Tool | Description |
|---|---|
| `bridge_health` | Adapters (path/version), Codex login, active tasks, remaining quota % per account |
| `bridge_list_agents` | Agents, roles, capabilities, availability, stages, quota fallback |
| `bridge_list_accounts` | Accounts, login state, quota snapshot (5h / 7d windows), codex-switch homes/profiles (no credentials) |
| `bridge_create_task` | Validated task contract (paths, criteria, verification, deps, risk, agent/account) |
| `bridge_execute_task` | Start the executor agent in the background |
| `bridge_task_status` | Compact state; `wait_s ≤ 55` long-poll |
| `bridge_task_result` | Spec, attempts, agent final JSON, diff stat + patch, bridge checks, reviews |
| `bridge_review_task` | approve / rework / block by the client (also overrides a failed reviewer agent) |
| `bridge_resume_task` | Rework or recover, same session and agent |
| `bridge_cancel_task` | Kill the run's process group and cancel |
| `bridge_list_tasks` | Newest first, optional state filter |
| `bridge_plan` | Run the planner agent; creates a workflow |
| `bridge_workflow_status` | Workflow state and tasks; `wait_s ≤ 55` long-poll |
| `bridge_run_workflow` | Drive a workflow until DONE / INCOMPLETE / WAITING |

### CLI (`uv run codex-bridge <cmd>`)

| Command | Description |
|---|---|
| `config-check` | Validate `.ai/orchestrator.yaml` (or the legacy config) |
| `agents` | Agents, roles and availability |
| `health` | Bridge and adapter status |
| `create <spec.json>` | Create a task from a spec file |
| `execute` / `status` / `result` | Run a task, read its state, read its result |
| `review` / `resume` / `cancel` | Review, continue or cancel a task |
| `plan "<objective>"` / `run-workflow <W…>` / `workflow-status <W…>` | Planner-agent workflows |
| `list` | List tasks |
| `account-list` | Accounts, login state, codex-switch homes/profiles |
| `account-clear <name>` | Clear an account's unavailable flag after the user confirms |

**Human-only** (need an interactive terminal and a typed confirmation):

| Command | Description |
|---|---|
| `approve <id>` | Approve a high-risk / sensitive-path task |
| `reassign <id> <agent\|account>` | Move a task to another agent (new session) |
| `account-login <name>` | Fresh login into the account's own home (`codex login` or `claude auth login`) |

---

## Bridge configuration (`.ai/config.json`, optional)

See `.ai/config.example.json`. Unknown keys are rejected.

| Key | Default | Meaning |
|---|---|---|
| `sandbox` | `workspace-write` | or `read-only`; `danger-full-access` is not accepted |
| `codex_bin` / `claude_bin` | `codex` / `claude` | CLI executables |
| `default_timeout_s` / `max_timeout_s` | 1800 / 7200 | per-run limits |
| `max_attempts` | 3 | legacy setup only; with `orchestrator.yaml` use `workflow.max_rework` |
| `network_retries` / `retry_backoff_s` | 1 / 10 | bounded retry for network errors only |
| `verify_timeout_s` | 900 | per verification command |
| `verify_allowlist` | pytest, uv, python, … | executables verification may start |
| `protected_paths` | `.env*`, keys, `.git/**`, `.ai/**`, … | never writable by a task |
| `sensitive_paths` | migrations, auth, deploy, CI, secrets | change ⇒ human approval |
| `env_deny` / `env_allow` | `*_API_KEY`, `*_TOKEN`, … | scrubbed from agent/verify env |
| `accounts` / `default_account` / `auto_switch` | — | legacy setup only; with `orchestrator.yaml`, `accounts` or an enabled `auto_switch` here is an error |
| `lock_dir` | system temp | cross-process account locks |

### State and files

| Path | Tracked | Content |
|---|---|---|
| `.ai/config.json` | yes | bridge config; any change by an agent is a scope violation |
| `.ai/orchestrator.yaml` | no (machine-specific homes) | roles, agents, accounts, workflow |
| `$(git rev-parse --absolute-git-dir)/codex-bridge/` | never | `bridge.db` (tasks, attempts, workflows, quota snapshots, audit log), per-run temp files, `install-manifest.json` (setup) |

State lives in the git directory because Codex's `workspace-write` sandbox can write anywhere in
the worktree, including git-ignored files, but not under `.git/` (verified on codex-cli 0.163).
Older databases gain the new columns and tables on the first start.

---

## Accounts

An account without `home` uses the CLI's inherited global login (`~/.codex`, `~/.claude`),
read-only from the bridge's point of view. An account with `home` is a separate login: the
bridge sets `CODEX_HOME` or `CLAUDE_CONFIG_DIR` for every run on it. Homes must be absolute,
outside the project and outside the global homes, and never shared by two accounts.

`codex-bridge account-login <name>` (human) logs a fresh session into that home; for Codex it
fails loudly if the global `~/.codex/auth.json` changes during login. `codex-switch home add`
homes work as Codex accounts; `codex-switch` snapshot profiles are listed but never used, because
using them would mean copying refresh tokens.

- A task stays on its agent and account. Retries and resumes use the same home and session. A
  human moves it with `codex-bridge reassign <id> <agent>` (new session), or the bridge does when
  quota fallback is enabled (below).
- QUOTA or AUTH marks the account unavailable. New runs on it are refused until
  `codex-bridge account-clear <name>` after the user confirms.
- Quota: for Codex, the latest `rate_limits` event in the home's session rollouts
  (`<home>/sessions/**/rollout-*.jsonl`; nothing else is opened); for Claude, the
  `rate_limit_event` the CLI reported on its last run through the bridge. Remaining =
  `100 − max(5h used %, 7d used %)`; `UNKNOWN` for an account that has never reported.
- Concurrency: `max_concurrency` agent processes per account (cross-process `flock` slots in
  `lock_dir`) and one `workspace-write` task per worktree; read-only runs (planning, reviewing,
  `read-only` tasks) may run alongside.

### Quota fallback (opt-in)

```yaml
routing:
  quota_fallback: {enabled: true, min_remaining_pct: 15, agents: [codex-b, codex-a]}
```

Before a task starts or restarts, the bridge checks its account. When the account is in QUOTA
state or below `min_remaining_pct`, the task moves to the first candidate agent on another
account that is available, logged in, under its concurrency limit and at or above the threshold
(or `UNKNOWN`). The move drops the session (the task restarts with the full prompt plus review
feedback), is logged as `account_auto_switched`, and is never triggered by AUTH. Enabling it is
the user's authorisation; the legacy `auto_switch` block in `config.json` maps onto it.

---

## How it works & Security

1. **Codex CLI:** OS sandbox `workspace-write` or `read-only`, no network, `/tmp` and `$TMPDIR` not
   writable, user `~/.codex/config.toml` ignored, prompt on stdin. Planner and reviewer runs are
   `read-only`.
2. **Claude CLI:** no OS sandbox, so the bridge narrows it: `--strict-mcp-config` (no MCP servers,
   so no recursive orchestration), `--setting-sources project` (no user hooks), `--permission-mode
   dontAsk` with a tool allowlist: Read/Glob/Grep; Edit/Write only with `allow_write: true`; Bash
   only with `allow_bash: true`. **This is weaker isolation than Codex** (a writing Claude agent
   can edit any worktree file its tools reach; the post-run scope check catches it after the
   fact). Prefer Codex as executor; give Claude write access only to trusted work.
3. **Recursion guard:** every agent process gets `CODEX_BRIDGE_AGENT=1`; the server and CLI refuse
   to start under it.
4. **Processes:** argv lists, no shell; own process group, SIGTERM then SIGKILL on timeout or
   cancel; orphaned agent groups from a dead runner are killed on the next start (only if the
   PID is a `codex` or `claude` process).
5. **Environment:** credential-shaped variables (`*_API_KEY`, `*_TOKEN`, `*_SECRET*`, …) are removed.
6. **Paths:** checked at creation (absolute, `..`, `~`, symlink escape, protected, over-broad) and
   after each run for every adapter (hash snapshot, full worktree diff, `HEAD` must not move).
7. **Verification:** re-run by the bridge, argv only, allowlisted executables, timeout.
8. **Approval:** executors cannot approve. The client or a reviewer agent approves; the bridge
   approves only in gates-only workflows. High-risk or sensitive-path tasks need a human at an
   interactive terminal. `.claude/settings.json` also denies these commands, account logins,
   `sqlite3` and edits under `.ai/` to Claude Code.
9. **Audit:** every transition, attempt, routing decision, review (with reviewer), account change
   and workflow state is an event in `bridge.db`. Stored output passes through secret redaction.

---

## Testing

```bash
scripts/check.sh                                   # full suite (fake codex + fake claude: deterministic)
CODEX_SMOKE=1 uv run pytest -m real_codex          # one real Codex run, opt-in, uses your quota
CLAUDE_SMOKE=1 uv run pytest tests/e2e/test_real_claude_smoke.py   # one real Claude review (haiku)
```

Result:
```console
144 passed, 2 skipped
```

`tests/fakes/codex` and `tests/fakes/claude` speak the real JSONL / stream-json protocols and
answer by output schema (executor, reviewer, planner). The suite covers configurations A–E
(Claude→Codex→Claude, Codex→Claude→Codex, two Codex executor accounts, single agent, gates
only), routing, pinning, affinity, quota fallback, bounded rework, reviewer failure, config
validation, account isolation, concurrency slots, legacy mapping and schema upgrade.

---

## Implemented and not supported

| Implemented | Not supported (yet) |
|---|---|
| Adapters `codex-cli`, `claude-cli`, `client` | Other CLIs (Gemini, Aider, …): add an `Adapter` subclass |
| Roles planner / executor / reviewer + custom roles in config | Custom roles have no stage of their own; `tester` is the bridge's `test` stage |
| Ordered and explicit routing, pinning, affinity, per-account limits | Load-balancing strategies beyond "first eligible" |
| Opt-in quota fallback across agents | Fallback on AUTH (by design: never) |
| Planner agent → workflow → sequential driver | Parallel writing tasks in one worktree (use separate worktrees) |
| Reviewer agent with client override; gates-only workflows | Multi-reviewer voting |
| Claude quota from `rate_limit_event` | Claude quota before the account's first run through the bridge |

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `codex-bridge: ⏸ Pending approval` | open `claude` in the folder once and trust it |
| `config-check` error | the message names the field; fix `.ai/orchestrator.yaml` |
| `health.ok = false` | an adapter CLI missing, or `codex` not logged in: log in (human) |
| `no eligible executor agent (…)` | every executor is disabled, busy or unavailable; the reasons are listed per agent |
| task PAUSED `QUOTA` / `AUTH` | wait or re-login, then `codex-bridge account-clear <name>` and resume; with quota fallback on, resuming a QUOTA pause moves it |
| `agent_review_failed` event | the reviewer agent errored; the task waits in REVIEW for `bridge_review_task` |
| workflow `WAITING` | `detail.waiting` and task states say what needs attention; run `bridge_run_workflow` again after |
| task PAUSED `INTERRUPTED` | the runner died; `bridge_resume_task` continues the session |
| `ConflictError … already writing` | one writer per worktree; wait or use another worktree |
| `account … busy in another bridge process` | the account's slots are in use elsewhere |
| REWORK `outside allowed paths` | the executor touched files outside the spec; read the patch, rework |

---

## Uninstall

In another project: `scripts/uninstall.sh /path/to/project` (add `--purge` to drop `.ai/`).
The bridge database in `.git/codex-bridge/` can be deleted by hand. For the kit itself, delete
the directory; nothing outside it (and account homes you created yourself) is used.

## Known limitations

See `docs/REPORT.md` → *Limitations*.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
