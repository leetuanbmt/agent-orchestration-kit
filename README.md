<h1 align="center">claude-codex-project-kit</h1>

<p align="center">
  A project-local harness where <b>Claude Code plans and reviews</b> and <b>Codex CLI implements</b>.<br>
  Connected by a small MCP server (<code>codex-bridge</code>) that owns task state, runs Codex safely and re-verifies its work.<br>
  Everything lives in the repository; no global configuration is touched.
</p>

<p align="center">
  <b>English</b> · <a href="README.vi.md">Tiếng Việt</a>
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey">
  <img alt="python" src="https://img.shields.io/badge/python-3.12-blue">
  <img alt="dependencies" src="https://img.shields.io/badge/dependencies-mcp-brightgreen">
  <img alt="tests" src="https://img.shields.io/badge/tests-97%20passed%20%7C%201%20opt--in-brightgreen">
  <img alt="version" src="https://img.shields.io/badge/version-0.1.0-blue">
</p>

---

When one AI agent both writes the code and judges it, nobody checks the work: "done" means whatever the agent says, even if the tests never ran or it edited files outside its assignment.

`claude-codex-project-kit` splits the roles:
- **Claude Code (advisor)**: analyses, splits work into tasks, reviews patches and decides approve / rework / block.
- **Codex CLI (executor)**: implements, writes tests and fixes one task at a time.
- **`codex-bridge`**: keeps task state in SQLite, runs Codex in a sandbox, checks which files changed and re-runs the verification commands itself. Codex's own report is never treated as evidence.

```
user ─▶ Claude Code ──MCP stdio──▶ codex-bridge ──argv, own process group──▶ codex exec --json
          (plan, review)           (SQLite state, scope check,                (implement, test)
                                    verification, audit log)
```

Everything is project-local: `.mcp.json`, `.claude/`, `CLAUDE.md`, `.ai/config.json`, and runtime
state in the repository's git directory (`.git/codex-bridge/`). Nothing under `~/.claude`,
`~/.codex` or global MCP config is modified.

---

## Installation

**Requirements:**
- macOS or Linux, git, [uv](https://docs.astral.sh/uv/) (Python 3.12 is fetched by uv).
- Claude Code (tested 2.1.303) and Codex CLI (tested 0.163.0), each logged in with its own
  subscription (`codex login status` → "Logged in using ChatGPT"). No OpenAI API key needed.

```bash
git clone https://github.com/leetuanbmt/claude-codex-project-kit.git
cd claude-codex-project-kit
uv sync                                   # project-local .venv
scripts/check.sh --with-health            # tests + Codex reachability
claude                                    # once: trust the folder → codex-bridge connects
claude mcp list                           # codex-bridge: ✔ Connected
```

`.mcp.json` registers the server with `${CLAUDE_PROJECT_DIR:-.}` paths and `.claude/settings.json`
enables it (`enabledMcpjsonServers`). Claude Code ignores a project's own approvals until the folder
is trusted, which is why the first interactive start is needed; `claude -p` loads it without a prompt.

### Use it in another project

```bash
scripts/setup.sh /path/to/project [--force]     # merge .mcp.json, settings, CLAUDE.md block, rules, skill
scripts/uninstall.sh /path/to/project [--purge] # reverse exactly what setup added
```

- Existing `.mcp.json` and `.claude/settings.json` are merged key by key (other servers, deny
  rules and settings stay); `CLAUDE.md` gets a marked block (`<!-- codex-bridge:start/end -->`).
  Every file setup changes is first copied to `.ai/backup-<UTC timestamp>/`.
- Rules are installed as `.claude/rules/codex-*.md` and the skill as
  `.claude/skills/codex-delegation/`. A different file already at one of those paths, or a
  different `codex-bridge` server, stops setup unless `--force` is given.
- The target's `.mcp.json` points at this kit by **absolute path** (`uv run --project <kit>`):
  it is machine-specific; moving the kit means running setup again.
- The install manifest is kept in `<git-dir>/codex-bridge/install-manifest.json`, out of Codex's
  reach. Uninstall removes only what setup added and keeps files you edited since (with a
  warning). `--purge` also deletes the target's `.ai/` directory, including its config.
- Each project has its own bridge database in its git directory, so task state and Codex sessions
  never leak between projects.

---

## Quick start

### 1. Delegate work through Claude

Ask Claude for the change; the `codex-delegation` skill drives:

```
bridge_create_task → bridge_execute_task → bridge_task_status(wait_s)
  → bridge_task_result → bridge_review_task (→ bridge_resume_task for rework)
```

### 2. Check status

```bash
uv run codex-bridge health        # bridge + Codex status, remaining quota per account
uv run codex-bridge account-list  # accounts, login state, codex-switch homes/profiles
uv run codex-bridge list          # tasks, newest first
```

### 3. Add a dedicated Codex account

```bash
codex-switch home add work          # fresh login into ~/.codex-accounts/homes/work (human)
uv run codex-bridge account-list    # shows the home and a ready config snippet
# add to .ai/config.json:  "accounts": {"work": {"codex_home": "~/.codex-accounts/homes/work"}}
```

---

## Task lifecycle

States: `BACKLOG → READY → RUNNING → VERIFYING → REVIEW → APPROVED → DONE`, plus `REWORK`,
`FAILED`, `PAUSED`, `BLOCKED`, `CANCELLED`.

| Move | Who | Trigger |
|---|---|---|
| `RUNNING → VERIFYING` | bridge | Codex exited cleanly |
| `VERIFYING → REVIEW` / `REWORK` | bridge | scope clean and every verification command passed / any check failed |
| `RUNNING → PAUSED` | bridge | quota, auth, network, missing CLI, interrupted runner (does not consume the attempt budget) |
| `REVIEW → APPROVED` / `REWORK` / `BLOCKED` | Claude | `bridge_review_task` |
| `APPROVED → DONE` | bridge, or a **human** for high risk | quality gates re-checked |

A task with `risk_level: high`, or a change under a sensitive path (migrations, auth, deploy, CI,
secrets), stops at `APPROVED` until a human runs `codex-bridge approve <id>` in a terminal.

---

## Command reference

### MCP tools (used by Claude)

| Tool | Description |
|---|---|
| `bridge_health` | Codex path/version/login, active tasks, remaining quota % per account |
| `bridge_list_accounts` | Accounts, login state, quota snapshot (5h / 7d windows), codex-switch homes/profiles (no credentials) |
| `bridge_create_task` | Validated task contract (paths, criteria, verification, deps, risk, account) |
| `bridge_execute_task` | Start Codex in the background |
| `bridge_task_status` | Compact state; `wait_s ≤ 55` long-poll |
| `bridge_task_result` | Spec, attempts, Codex final JSON, diff stat + patch, bridge checks |
| `bridge_review_task` | approve / rework / block (Claude) |
| `bridge_resume_task` | Rework or recover, same Codex session and account |
| `bridge_cancel_task` | Kill the run's process group and cancel |
| `bridge_list_tasks` | Newest first, optional state filter |

### CLI (`uv run codex-bridge <cmd>`)

| Command | Description |
|---|---|
| `health` | Bridge and Codex status |
| `create <spec.json>` | Create a task from a spec file |
| `execute` / `status` / `result` | Run a task, read its state, read its result |
| `review` / `resume` / `cancel` | Review, continue or cancel a task |
| `list` | List tasks |
| `account-list` | Accounts, login state, codex-switch homes/profiles |
| `account-clear <name>` | Clear an account's unavailable flag after the user confirms |

**Human-only** (need an interactive terminal and a typed confirmation):

| Command | Description |
|---|---|
| `approve <id>` | Approve a high-risk / sensitive-path task |
| `reassign <id> <account>` | Move a task to another account (new Codex session) |
| `account-login <name>` | Fresh login for a `codex_home` you choose |

---

## Configuration (`.ai/config.json`, optional)

See `.ai/config.example.json`. Unknown keys are rejected.

| Key | Default | Meaning |
|---|---|---|
| `sandbox` | `workspace-write` | or `read-only`; `danger-full-access` is not accepted |
| `default_timeout_s` / `max_timeout_s` | 1800 / 7200 | per-run limits |
| `max_attempts` | 3 | initial run + 2 reworks; then BLOCKED |
| `network_retries` / `retry_backoff_s` | 1 / 10 | bounded retry for network errors only |
| `verify_timeout_s` | 900 | per verification command |
| `verify_allowlist` | pytest, uv, python, … | executables verification may start |
| `protected_paths` | `.env*`, keys, `.git/**`, `.ai/**`, … | never writable by a task |
| `sensitive_paths` | migrations, auth, deploy, CI, secrets | change ⇒ human approval |
| `env_deny` / `env_allow` | `*_API_KEY`, `*_TOKEN`, … | scrubbed from Codex/verify env |
| `accounts` / `default_account` | `{}` / `default` | see Accounts |
| `auto_switch` | `{"enabled": false, "min_remaining_pct": 10, "fallback_accounts": []}` | see Accounts → Auto-switch |
| `lock_dir` | system temp | cross-process account locks |

### State and files

| Path | Tracked | Content |
|---|---|---|
| `.ai/config.json` | yes | project config; any change by Codex is a scope violation |
| `.ai/config.example.json` | yes | documented example |
| `$(git rev-parse --absolute-git-dir)/codex-bridge/` | never | `bridge.db` (tasks, attempts, audit log), output schema, per-run temp files, `install-manifest.json` (setup) |

State lives in the git directory because Codex's `workspace-write` sandbox can write anywhere in
the worktree, including git-ignored files, but not under `.git/` (verified on codex-cli 0.163).
A database inside the worktree would let Codex rewrite its own task state. Older installs with
`.ai/bridge.db` are migrated automatically on the first start.

---

## Accounts

`default` runs Codex with the inherited environment (`~/.codex`), read-only from the bridge's
point of view. Named accounts are separate Codex homes, each logged in on its own (see *Quick start → 3*).

`codex-bridge account-login <name>` does the same for a `codex_home` you choose. Neither path
copies tokens from another profile, and both fail loudly if the global `~/.codex/auth.json`
changes during login. `codex-switch` snapshot profiles are listed but never used, because using
them would mean copying refresh tokens.

- A task stays on its account. Retries and resumes use the same `CODEX_HOME` and Codex session.
  A human moves it with `codex-bridge reassign <id> <account>` (new session), or the bridge does
  when `auto_switch` is enabled (below).
- QUOTA or AUTH marks the account unavailable. New runs on it are refused until
  `codex-bridge account-clear <name>` after the user confirms.
- Quota is read from the latest `rate_limits` event in the account's Codex session rollouts
  (`<codex_home>/sessions/**/rollout-*.jsonl`; nothing else in the home is opened). Remaining =
  `100 − max(5h used %, 7d used %)`; a window past its reset counts as 0 % used. It is a snapshot
  as fresh as the account's last Codex turn, and `UNKNOWN` for an account that has never run.
- Concurrency: one active task per account (cross-process `flock` in `lock_dir`) and one
  `workspace-write` task per worktree; `read-only` tasks may run alongside. Parallel writers need
  separate repositories or worktrees, each with its own bridge state.

### Auto-switch (opt-in)

```json
"auto_switch": {"enabled": true, "min_remaining_pct": 15, "fallback_accounts": ["work", "default"]}
```

Before a task starts (`execute`) or restarts (`resume`), the bridge checks its account. When the
account is in QUOTA state or its remaining quota is below `min_remaining_pct`, the task moves to
the first account in `fallback_accounts` that is available, logged in, not running another task,
and at or above the threshold (or `UNKNOWN`).

- The move works like `reassign`: the Codex session is dropped and the task restarts with the full
  prompt plus any review feedback; files Codex already changed stay in the worktree.
- Every move is an `account_auto_switched` event (from, to, reason, remaining %); when no fallback
  qualifies, `auto_switch_skipped` is logged and the task stays where it is.
- Never triggered by AUTH, nor by an account whose quota is `UNKNOWN`. Off by default; only the
  user edits `.ai/config.json`.

---

## How it works & Security

1. **Codex:** sandbox `workspace-write` or `read-only` only, no network, `/tmp` and `$TMPDIR` not
   writable, user `~/.codex/config.toml` ignored (`--ignore-user-config`), prompt passed on stdin.
2. **Processes:** argv lists, no shell; own process group, SIGTERM then SIGKILL on timeout or cancel;
   orphaned Codex groups from a dead runner are killed on the next start (only if the PID is Codex).
3. **Environment:** credential-shaped variables (`*_API_KEY`, `*_TOKEN`, `*_SECRET*`, …) are removed.
4. **Paths:** checked at creation (absolute, `..`, `~`, symlink escape, protected, over-broad) and after
   each run (hash snapshot of dirty + protected files, full worktree diff via a throwaway index,
   `HEAD` must not move).
5. **Verification:** re-run by the bridge, argv only, allowlisted executables, timeout.
6. **Approval:** Codex cannot approve. Claude approves via MCP. High-risk or sensitive-path tasks need a
   human at an interactive terminal (`approve` refuses without a TTY). `.claude/settings.json` also
   denies these commands, `sqlite3` and edits under `.ai/` to Claude.
7. **Audit:** every transition, attempt, review, account change and migration is an event in
   `bridge.db`. Stored output passes through secret redaction.

---

## Testing

```bash
scripts/check.sh                            # full suite (fake codex: deterministic)
CODEX_SMOKE=1 uv run pytest -m real_codex   # one real Codex run, opt-in, uses your quota
```

Result:
```console
97 passed, 1 skipped
```

The fake `tests/fakes/codex` speaks the real JSONL protocol and covers: success, blocked, quota,
auth, network, crash, hang, commit, out-of-scope writes, sessions per `CODEX_HOME`.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `codex-bridge: ⏸ Pending approval` | open `claude` in the folder once and trust it |
| `health.ok = false` | `codex` missing or not logged in: `codex login` (human) |
| task PAUSED `QUOTA` / `AUTH` | wait or re-login, then `codex-bridge account-clear <name>` and resume; with `auto_switch` on, resuming a QUOTA pause moves it to a fallback |
| `auto_switch_skipped` event | no fallback qualified: all busy, logged out, unavailable or below threshold |
| task PAUSED `INTERRUPTED` | the runner died; `bridge_resume_task` continues the session |
| `ConflictError … already writing` | one writer per worktree; wait or use another worktree |
| `account … busy in another bridge process` | another project uses that account right now |
| REWORK `outside allowed paths` | Codex touched files outside the spec; read the patch, rework |
| pytest fails only inside Codex | sandbox forbids `$TMPDIR`, `ps`; the bridge's own run is the gate |

---

## Uninstall

In another project: `scripts/uninstall.sh /path/to/project` (add `--purge` to drop `.ai/`).
The bridge database in `.git/codex-bridge/` can be deleted by hand. For the kit itself, delete
the directory; nothing outside it (and `~/.codex-accounts/homes/*` you created yourself) is used.

## Known limitations

See `docs/REPORT.md` → *Limitations*.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
