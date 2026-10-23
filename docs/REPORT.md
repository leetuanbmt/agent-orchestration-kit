# Build report: agent-orchestration-kit

Status at delivery: all automated checks green (see *Verification*), real Codex runs end to end
through the bridge, including two accounts in parallel.

## 1. Compatibility (verified on this machine)

| Component | Version | What was verified |
|---|---|---|
| Codex CLI | 0.163.0 | `exec --json`, `-o`, `--output-schema`, `--ignore-user-config`, `-s read-only\|workspace-write`, `-C`, prompt on stdin; `exec resume <thread_id> -` (no `-s`/`-C`: sandbox via `-c sandbox_mode=…`, enforced); JSONL events `thread.started`, `item.completed`, `turn.completed`, `turn.failed`, `error`; `CODEX_HOME` honoured; `codex login status` exit 0/1; 401 text on missing auth |
| Codex sandbox (macOS) | 0.163.0 | `workspace-write` can write git-ignored files in the worktree (incl. `.ai/`) but **not** `.git/`; `$TMPDIR` and `/tmp` unwritable when excluded; `ps`/`kill` forbidden |
| MCP Python SDK | 2.3.0 | `MCPServer` (formerly FastMCP); non-`ToolError` exceptions are hidden from the client |
| Claude Code | 2.1.303 | `.mcp.json` with `${CLAUDE_PROJECT_DIR:-.}`; `enabledMcpjsonServers` honoured only after the folder is trusted; `.claude/rules/*.md` auto-loaded; `claude -p` called `bridge_health`; `claude mcp list` → Connected |
| Claude Code headless | 2.1.306 | `claude -p --output-format stream-json --verbose`, prompt on stdin; events `system/init` (session_id; `mcp_servers: []` under `--strict-mcp-config`), `rate_limit_event` (`unifiedWindows.five_hour/seven_day.utilization` 0–1, `resetsAt`), `result` (`is_error`, `subtype`, `structured_output` with `--json-schema`, `session_id`, `api_error_status`); `--permission-mode dontAsk` + `--allowedTools`; `--setting-sources ""` (probed on 2.1.308: allow rules from any loaded settings source extend `--allowedTools`; the empty list loads none); `--resume`; `auth status` JSON `loggedIn`; `--bare` disables OAuth, so it is not used |
| codex-switch | local repo `claude-switcher/agents-switch` | profiles are full auth snapshots incl. refresh tokens, `switch` overwrites the global `auth.json` → not used for execution; new `home` subcommands used instead (below) |

## 2. Architecture

```
MCP client ─MCP stdio─▶ bridge/server.py ─┐
human CLI ────────────▶ bridge/cli.py ────┴▶ bridge/service.py (Bridge: routing, review, workflows)
                                               ├ orchestrator.py roles → agents → accounts, workflow stages (YAML)
                                               ├ state.py        SQLite WAL, tasks, workflows, quota, events
                                               ├ executor.py     Adapter base; CodexAdapter, ClaudeAdapter; process group
                                               ├ review.py       snapshot, diff, scope, verification
                                               ├ accounts.py     CODEX_HOME / CLAUDE_CONFIG_DIR per account, flock slots
                                               └ security.py     path rules, env scrub, redaction
```

- One service layer behind MCP and CLI, so both enforce the same gates.
- State machine `BACKLOG→READY→RUNNING→VERIFYING→REVIEW→APPROVED→DONE` plus `BLOCKED`,
  `REWORK`, `FAILED`, `CANCELLED`, `PAUSED`. Every transition is checked against a table of
  `(from, to, actor)` with actor ∈ {bridge, claude, human}; terminal states have no exits.
- A run: snapshot the worktree → `codex exec` in its own process group → parse JSONL and the
  schema-bound final message → diff against the snapshot (throwaway git index, so untracked files
  count) → scope check → bridge re-runs the verification commands → `REVIEW` or `REWORK`.
- Rework and resume continue the same Codex session on the same account.
- Dependencies: a task waits in `BACKLOG` until its `depends_on` tasks are `DONE`.

## 3. Security model

| Threat | Control |
|---|---|
| Codex edits outside its task | allowed/excluded/protected path check after every run; `HEAD` must not move; violation → `REWORK` |
| Codex forges its own task state | state in `<git-dir>/agent-bridge/` (sandbox cannot write `.git/`); `.ai/config.json` tracked so changes show in the diff |
| Codex approves itself | only Claude (MCP) approves; high-risk / sensitive-path tasks need `agent-bridge approve` from a TTY with the task id typed back |
| Claude bypasses the human gate | `.claude/settings.json` deny rules for approve/reassign/login/switch commands, `sqlite3`, edits under `.ai/` |
| Credential leakage | credential-shaped env vars scrubbed; output redacted before storage; profiles listed by name only; `Read` denied on `~/.codex/auth.json` and `~/.codex-accounts/**` |
| Silent account switch | task bound to its account for life; reassign is human-only; global `~/.codex` never written (login fails loudly if its fingerprint changes) |
| Runaway process | argv only, no shell; process-group SIGTERM→SIGKILL on timeout/cancel; stale Codex groups killed on start after a PID-name check |
| Unsafe verification | allowlisted executables, argv parsing without a shell, timeout |

## 4. Multi-account execution

- `default` = inherited environment (`~/.codex`). Named accounts = separate `CODEX_HOME`
  directories, each logged in on its own with `cli_auth_credentials_store="file"`.
- codex-switch was extended (Codex task in the claude-switcher repo, reviewed): `home add` (fresh
  login into `~/.codex-accounts/homes/<name>`), `home list --json`, `home run`, `home remove`;
  `switch` refuses while Codex runs or when the current account is unsaved, unless `--force`.
  The bridge discovers those homes and prints a config snippet; the only files it reads inside them are
  `sessions/**/rollout-*.jsonl`, for the `rate_limits` quota snapshot.
- Availability per account: `available`, `quota`, `auth`. QUOTA/AUTH pauses the task and blocks
  the account until a human clears it. Quota is read from session rollouts, and an opt-in
  `auto_switch` can move a task to a fallback account (see README → Accounts).
- Concurrency: one active task per account (cross-process `flock`), one `workspace-write` task
  per worktree, read-only tasks in parallel.

## 5. Verification

| Check | Result |
|---|---|
| `scripts/check.sh --with-health` | 71 passed, 1 skipped (the skip is the opt-in real smoke test); `health.ok = true`, codex-cli 0.163.0, quota `UNKNOWN` |
| Fake-Codex scenarios | ok, blocked, quota, auth, network, crash, hang, commit, outside, nojson, slow |
| Real Codex through the bridge | `CODEX_SMOKE=1 pytest -m real_codex`: 1 passed (temp repo, real Codex, READY→REVIEW→DONE, bridge check passed). Also 8 kit tasks (T0001–T0014; 6 others were cancelled before running) and 1 claude-switcher task were executed by the real Codex through the bridge |
| Claude Code integration | `claude mcp list` Connected; headless `claude -p` → `bridge_health` |
| Two real accounts in parallel | `default` (global `~/.codex`) and `ntmt100199` (home from `codex-switch home add`), one throwaway repo each, started together: both REVIEW with the expected one-line change and passing checks, 15.2 s wall vs 28 s summed, distinct sessions, each session file under its own `CODEX_HOME`, global `auth.json` mtime/size unchanged |
| Install into another project | throwaway repo: `setup.sh` → printed `agent-bridge health` → `ok`, state dir in that repo's `.git/agent-bridge` → `uninstall.sh` → `CLAUDE.md` byte-identical, only `.ai/backup-*` left (removed by `--purge`) |
| claude-switcher `test.sh` | 58 checks pass (re-run at delivery) |

## 6. Authorship

Built delegation-first once the bridge worked.

| By | Work |
|---|---|
| Claude | bridge core (models, state, executor, review, service, server, CLI), fake Codex, first test suite, rules, skill, CLAUDE.md, config, task specs, all reviews, docs |
| Codex via the bridge | `scripts/check.sh` (T0001); account discovery (T0002); routing and availability (T0003→T0005); concurrency, locks, reassign (T0006: ended BLOCKED on a test-isolation bug in the planner's test setup; its code was kept and the fix landed in T0012); state-dir security fix and test isolation (T0012, human-approved); codex-switch homes integration, approval hint, real smoke test (T0013); setup/uninstall (T0014); codex-switch fixes and `home` subcommands (claude-switcher T0001) |

Planner errors caught by the process: T0003 and T0013 had specs whose criteria required changes
outside `allowed_paths`; Codex reported `blocked` instead of overstepping. The spec self-check
rule in `.claude/rules/codex-delegation.md` came out of this.

## 7. Deviations from the original prompt

- Runtime state lives in `<git-dir>/agent-bridge/`, not `.ai/`: the sandbox can write `.ai/`,
  which would let Codex edit its own task record (found by probe, fixed in T0012).
- `.ai/config.json` is tracked, not ignored, so changes to it are visible in the diff.

## 8. Configurable orchestration (unreleased)

Role ≠ agent ≠ provider ≠ account ≠ model. `.ai/orchestrator.yaml` (pydantic schema, unknown
keys rejected, errors carry the field path) declares accounts, agents, roles, routing and
workflow stages; without it `orchestrator.legacy()` rebuilds the 0.1 setup from `config.json`
(agents named after accounts, the client plans and reviews, `auto_switch` → quota fallback).

| Piece | Implementation |
|---|---|
| Agent registry | `agents.<id>`: adapter, account, model, capabilities, `allow_write`, `allow_bash`, timeout, enabled |
| Provider adapters | `Adapter` base (process group, stdin prompt, watchdog, recursion marker) with `CodexAdapter` and `ClaudeAdapter`; `client` marks the MCP caller |
| Role registry | built-in planner / executor / reviewer (+ custom names); `requires` read/write; executor must write and cannot be the client |
| Routing | pin by agent or account; else first eligible agent in role order (`agent_routed`); `explicit` strategy; affinity after the first run; `max_concurrency` per account (flock slots + state check); opt-in quota fallback across agents; human `reassign` by agent or account |
| Workflow | stages `plan → execute → test → review`; reviewer agent read-only with a review schema, applied as actor `agent`; no review stage → bridge approves verified work; planner agent → validated tasks → sequential driver ending DONE / INCOMPLETE / WAITING |
| State | new columns `agent`, `workflow_id`; tables `workflows`, `account_quota` (Claude `rate_limit_event` snapshots); migration adds them to old databases |

Configurations covered by tests (fakes): A Claude planner → Codex executor → Claude reviewer;
B Codex planner → Claude executor → Codex reviewer; C Claude planner → two Codex executor accounts
(busy routing, quota fallback on/off); D one Codex agent in every role; E gates only.

Verification of this change:

| Check | Result |
|---|---|
| `scripts/check.sh` | 186 passed, 5 skipped (opt-in real smoke tests) |
| Real Claude CLI, `CLAUDE_SMOKE=1` | 1 passed: fake Codex made the change, real `claude -p` (haiku) reviewed read-only with no MCP servers and approved as actor `agent`; task DONE |
| Real Codex, `CODEX_SMOKE=1 -m real_codex` | 2 passed: the 0.1 lifecycle (READY→REVIEW→DONE) and a single-agent workflow where real Codex planned with the plan schema, implemented, and approved with the review schema (read-only); workflow DONE |
| Claude write scoping probe | see *Limitations*: scoped Edit/Write rules refuse writes outside `allowed_paths` |
| `uv tool install --editable` into a scratch tool dir | `agent-bridge init` in a fresh repo wrote `{"command": "agent-bridge", "args": ["serve"]}`; an MCP client started that entry and listed all 14 `bridge_*` tools |

The CLI and MCP server are named `agent-bridge` (was `codex-bridge`); `bridge/install.py` is
the one installer behind `agent-bridge init`/`uninstall` and the `setup.sh`/`uninstall.sh`
wrappers, and migrates an older install. `agent-bridge` alone opens a Textual dashboard
(`bridge/tui.py`) over the same store: it performs human actions only (approve, review, cancel,
reassign, account login/clear, config edit) and never starts an agent, since a run belongs to
the process that started it. Its flows are tested headless with Textual's Pilot.

### opencode, Antigravity and machine-wide accounts

| Piece | Implementation |
|---|---|
| `opencode-cli` | `opencode run --format json --standalone [-m] [--session]`, prompt on stdin; schema in the prompt, JSON parsed from the reply text (fenced or bare); `OPENCODE_CONFIG_CONTENT` denies edit, bash, webfetch, external_directory; `OPENCODE_DISABLE_PROJECT_CONFIG=1`; `provider.quota` errors pause as QUOTA |
| `agy-cli` | `agy --input-format stream-json --output-format stream-json -p= --mode plan --sandbox --json-schema …`, one `{"event":"user"}` line on stdin; `result.structured_output`; account home = `HOME` |
| Registry | `~/.agent-bridge/accounts.json` + `homes/<name>` (0700); merged into every project's accounts, project declarations win; legacy setups gain a Codex agent per registry openai account |
| Switching | `account-use` prints `export CODEX_HOME=…` / `CLAUDE_CONFIG_DIR=…` or an `agy` shell function, or opens a subshell; global homes are never rewritten |

Probes on this machine (opencode 2.1.2, agy 1.4.5): with the deny config opencode wrote no file
when asked to; a path-scoped `edit` rule (`{"*":"deny","src/**":"allow"}`) also wrote nothing,
so writing could not be shown to be scoped and both adapters stay read-only. agy in plan mode
wrote only its own plan artifact outside the worktree. Isolating opencode by XDG dirs failed
(its background service clashed on a fixed port), so opencode accounts use the global login.

| Check | Result |
|---|---|
| Real opencode reviewer, `OPENCODE_SMOKE=1` (ollama-cloud/gpt-oss:120b) | approved as actor `agent`; worktree held only the executor's change; task DONE. 1 of ~10 runs produced no review (reviewer failure, task left in REVIEW for the client as designed; cause not captured) |
| Real agy reviewer, `AGY_SMOKE=1` (gemini-3.8-flash-low, `--mode plan --sandbox`) | approved as actor `agent`; worktree held only the executor's change; task DONE |

## 9. Known limitations / unsupported

- opencode and agy cannot execute (write) tasks. agy keeps browser tools in plan mode that the
  CLI cannot disable; opencode has no per-account login isolation.
- `account-add --provider google` (agy sign-in inside a fresh `HOME`) and `account-add` for
  Claude were exercised only against the fake CLIs: they are human-only, so no real login was
  run. `account-add` refuses to finish if the global login changed, as `account-login` does.

- A Claude executor (`allow_write`) has no OS sandbox. Probe on 2.1.306 (haiku, `dontAsk`):
  an unscoped `Write` created a file outside the working directory; with `Edit(./src/**)` and
  `Write(./src/**)` the same run wrote `src/inside.txt` and was refused for a file outside the
  worktree and for `other.txt` in it. The adapter therefore scopes Edit/Write to the task's
  `allowed_paths` and denies excluded and protected paths. Read tools still read anything the
  user can, and `allow_bash` is not path-confined.
- Claude quota is known only after the account's first run through the bridge. Where an
  isolated `CLAUDE_CONFIG_DIR` login is stored on macOS was not verified; `account-login`
  compares the global `claude auth status` identity before and after and exits 3 if it changed.
- The workflow driver runs tasks one at a time; parallel writers need separate worktrees.

- Quota is a snapshot from the newest session rollout, not a live query: it is as fresh as the
  account's last Codex turn and `UNKNOWN` for an account that has never run.
- The snapshot hashes tracked, dirty and top-level protected files; a change to a nested,
  git-ignored secret file is not detected.
- Deny rules in `.claude/settings.json` are guidance for Claude, not a security boundary; the TTY
  check for human-only commands can be defeated by an agent that allocates a pty.
- Verification commands run outside the Codex sandbox, with the bridge's permissions.
- One writer per worktree: parallel writes need separate worktrees.
- The Codex and Claude CLIs still write their own session files under `~/.codex/sessions` and
  `~/.claude.json`; the kit does not control that.
- The target `.mcp.json` written by setup contains the kit's absolute path.
- `.ai/backup-*` and other files under `.ai/` (except config) are git-ignored and writable by
  Codex; they are convenience copies, not trusted input.
