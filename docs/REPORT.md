# Build report: agent-orchestration-kit

Status at delivery: all automated checks green (see *Verification*), real Codex runs end to end
through the bridge, including two accounts in parallel.

## 1. Compatibility (verified on this machine)

`agent-bridge health` warns (`cli_unprobed`) when an installed Codex, Claude, Cursor, opencode or agy CLI is newer than
the version in `PROBED_VERSIONS` (`bridge/service.py`): codex-cli 0.163.0, Claude Code
2.1.308, cursor-agent 2026.11.11, opencode 2.2.6 and agy 1.5.7 (re-probed on 2026-11-13). After re-running the probes below on a newer CLI, update both this table and that
constant.

| Component | Version | What was verified |
|---|---|---|
| Codex CLI | 0.163.0 | `exec --json`, `-o`, `--output-schema`, `--ignore-user-config`, `-s read-only\|workspace-write`, `-C`, prompt on stdin; `exec resume <thread_id> -` (no `-s`/`-C`: sandbox via `-c sandbox_mode=…`, enforced); JSONL events `thread.started`, `item.completed`, `turn.completed`, `turn.failed`, `error`; `CODEX_HOME` honoured; `codex login status` exit 0/1; 401 text on missing auth |
| Codex sandbox (macOS) | 0.163.0 | `workspace-write` can write git-ignored files in the worktree (incl. `.ai/`) but **not** `.git/`; `$TMPDIR` and `/tmp` unwritable when excluded; `ps`/`kill` forbidden |
| MCP Python SDK | 2.3.0 | `MCPServer` (formerly FastMCP); non-`ToolError` exceptions are hidden from the client |
| Claude Code | 2.1.303 | `.mcp.json` with `${CLAUDE_PROJECT_DIR:-.}`; `enabledMcpjsonServers` honoured only after the folder is trusted; `.claude/rules/*.md` auto-loaded; `claude -p` called `bridge_health`; `claude mcp list` → Connected |
| Claude Code headless | 2.1.306 | `claude -p --output-format stream-json --verbose`, prompt on stdin; events `system/init` (session_id; `mcp_servers: []` under `--strict-mcp-config`), `rate_limit_event` (`unifiedWindows.five_hour/seven_day.utilization` 0–1, `resetsAt`), `result` (`is_error`, `subtype`, `structured_output` with `--json-schema`, `session_id`, `api_error_status`); `--permission-mode dontAsk` + `--allowedTools`; `--setting-sources ""` (probed on 2.1.308: allow rules from any loaded settings source extend `--allowedTools`; the empty list loads none); `--resume`; `auth status` JSON `loggedIn`; `--bare` disables OAuth, so it is not used |
| codex-switch | local repo `claude-switcher/agents-switch` | profiles are full auth snapshots incl. refresh tokens, `switch` overwrites the global `auth.json` → not used for execution; new `home` subcommands used instead (below) |
| agy CLI | 1.5.7 | Headless Auto default; every bridge run pins `agentMode: ""` (Default) so write_file allow rules hold; read-only deny-all and command denies verified on 2026-11-13 |

## 2. Architecture

```
MCP client ─MCP stdio─▶ bridge/server.py ─┐
human CLI ────────────▶ bridge/cli.py ────┴▶ bridge/service.py (Bridge: facade, review, workflows)
                                               ├ gate.py         account state, limits, usage ledger, routing
                                               ├ runner.py       start gate, agent run, verification, review stage
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
| Codex edits outside its task | allowed/excluded/protected path check after every run, after verification and for attempts that end early; `HEAD` must not move; violation → `REWORK`, protected file or leftover from an interrupted attempt → `BLOCKED`; git-ignored files tracked by size/mtime |
| Codex forges its own task state | state in `<git-dir>/agent-bridge/` (sandbox cannot write `.git/`); `.ai/config.json` tracked so changes show in the diff |
| Codex approves itself | only Claude (MCP) approves; high-risk / sensitive-path tasks need `agent-bridge approve` from a TTY with the task id typed back |
| Claude bypasses the human gate | `.claude/settings.json` deny rules for approve/reassign/login/switch commands, `sqlite3`, edits under `.ai/` |
| Credential leakage | credential-shaped env vars scrubbed (plus `SSH_AUTH_SOCK`, `*_DSN`, `*DATABASE_URL*`, `*CREDENTIALS*`, `KUBECONFIG`, `DOCKER_AUTH_CONFIG`); output redacted before storage; profiles listed by name only; Claude agents' `Read` denied on Codex/agent-bridge/agy credentials, `~/.claude/.credentials.json`, every configured account home and `.env*`/key files |
| Silent account switch | task bound to its account for life; reassign is human-only; global `~/.codex` never written (login fails loudly if its fingerprint changes) |
| Runaway process | argv only, no shell; process-group SIGTERM→SIGKILL on timeout/cancel and whatever is left in the group when an agent or check exits; stale agent and verification groups killed on start after a PID and start-time check |
| Unsafe verification | allowlisted executable names (no paths, no shell operators), argv parsing without a shell, timeout; Landlock write confinement to the worktree, a private TMPDIR and `verify_writable` (`workflow.verify_sandbox`, `auto` by default, `strict` also denies TCP); the worktree is diffed again after the checks |
| Approval of changed content | approval and closing require every file the attempt touched to keep its verified sha256 |
| Agent uses the Python API | `Bridge()` refuses under `AGENT_BRIDGE_AGENT`; this is process discipline, not an OS boundary: everything runs as the same user |

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
- Concurrency: one active task per account (cross-process `flock`) and one active task per
  worktree, read-only ones included; parallel work needs separate worktrees.

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
| Routing | pin by agent or account; else first eligible agent in role order (`agent_routed`); `explicit` and opt-in `quota` (most remaining quota) strategies; workflows wait for a slot busy in another bridge (`routing.slot_wait_s`); affinity after the first run; `max_concurrency` per account (flock slots + state check); opt-in quota fallback across agents; human `reassign` by agent or account |
| Workflow | stages `plan → execute → test → review`; reviewer agent read-only with a review schema, applied as actor `agent`; no review stage → bridge approves verified work; planner agent → validated tasks → sequential driver ending DONE / INCOMPLETE / WAITING |
| State | new columns `agent`, `workflow_id`; tables `workflows`, `account_quota` (Claude `rate_limit_event` snapshots); migration adds them to old databases |

Configurations covered by tests (fakes): A Claude planner → Codex executor → Claude reviewer;
B Codex planner → Claude executor → Codex reviewer; C Claude planner → two Codex executor accounts
(busy routing, quota fallback on/off); D one Codex agent in every role; E gates only.

Verification of this change:

| Check | Result |
|---|---|
| `scripts/check.sh` | 188 passed, 5 skipped (opt-in real smoke tests) |
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
| `opencode-cli` | `opencode run --format json --standalone [-m] [--session]`, prompt on stdin; schema in the prompt, JSON parsed from the reply text (fenced or bare); `OPENCODE_CONFIG_CONTENT` is a V2 deny-all config allowing only read, glob, grep, with an empty per-run `XDG_CONFIG_HOME`; `OPENCODE_DISABLE_PROJECT_CONFIG=1`; `provider.quota` errors pause as QUOTA |
| `agy-cli` | `agy --input-format stream-json --output-format stream-json -p= --sandbox --disable-slash-commands --settings <deny rules, or write_file allows for an allow_write executor> --json-schema …`, one `{"event":"user"}` line on stdin; `result.structured_output`; account home = `HOME` |
| Registry | `~/.agent-bridge/accounts.json` + `homes/<name>` (0700); merged into every project's accounts, project declarations win; legacy setups gain a Codex agent per registry openai account |
| Switching | `account-use` prints `export CODEX_HOME=…` / `CLAUDE_CONFIG_DIR=…` or an `agy` shell function, or opens a subshell; global homes are never rewritten |

Earlier probes (opencode 2.1.2, agy 1.4.5; superseded by the 2026-11-11 probe below): with the deny config opencode wrote no file
when asked to; a path-scoped `edit` rule (`{"*":"deny","src/**":"allow"}`) also wrote nothing,
so writing could not be shown to be scoped and both adapters stay read-only. agy in plan mode
wrote only its own plan artifact outside the worktree. Isolating opencode by XDG dirs failed
(its background service clashed on a fixed port), so opencode accounts use the global login.

Cursor probe, 2026-11-11 (cursor-agent 2026.11.11, macOS): `cursor-agent -p --output-format
stream-json --mode ask --sandbox enabled --trust --workspace <root>`, prompt on stdin. In ask mode
the model reported writes and shell blocked and wrote nothing. Hooks in `.cursor/hooks.json`,
`.claude/settings.json` and `.claude/settings.local.json` (a `hooks` object) ran in ask mode,
outside the sandbox: one touched a file outside the workspace, so the adapter refuses all three
(and an unparseable `.claude` file). A project `mcp.json` was not started. `--trust` is
remembered: the directory is recorded in `~/.cursor/projects/<slug>/` and chats are stored under
`~/.cursor/chats/`; without it print mode exits 1 in an unseen directory. The `result` event's
`result` is every assistant text concatenated; usage keys are camelCase and no cost is reported.
The binary is installed as `cursor-agent` and `agent`.

Probe, 2026-11-11 (opencode 2.2.6, agy 1.5.3, cursor-agent 2026.11.11), real CLIs:

- opencode 2.2.6: the old V1 `permission` block (edit, bash, webfetch, external_directory deny)
  left `execute` (the user's MCP servers), `subagent`, `skill`, `websearch` and
  `experimental_image_generate` open, and user plugins, MCP servers, agents and skills from
  `~/.config/opencode` still loaded. A V2 config in `OPENCODE_CONFIG_CONTENT` (`permissions` deny
  `*` first, then allow read/glob/grep, deny `.env` reads; `policies` deny `tool.use`, `mcp.use`,
  `skill.use`, `websearch.use` except read/glob/grep) lands after the built-in defaults for every
  agent (`opencode debug agents --standalone`, last match wins); the model then had only
  read, glob and grep. `skill` was still listed but not callable per policy (not separately
  tested). With `--standalone`, `XDG_CONFIG_HOME` alone pointing at an empty directory removes
  the user config as a source; data, login and sessions stay. Without `--standalone` the CLI
  talks to the user's background service and ignores it. `OPENCODE_DISABLE_PROJECT_CONFIG=1` is
  required: a project plugin runs code and a project `agents.build.permissions` rule removes every
  deny. The shell tool is named `shell` in V2 (`bash` before); its command is in
  `part.state.input.command`.
- agy 1.5.3: `--mode plan` enforces nothing (with `--disable-slash-commands` it prints that it
  has no effect, and the agent wrote files and ran shell); `--sandbox` alone did not stop writes
  when the user's global settings say always-proceed. `--settings` merges with the global
  settings; with `toolPermission: request-review` and deny `write_file(*)`, `command(*)`,
  `mcp(*)`, file creation and edits, every shell command (even ones the global allow list
  permits) and MCP calls failed with "Matches user-configured deny rule", the turn continued and
  `structured_output` was produced. Not blocked: `search_web`, `invoke_subagent`, and the
  start-up of hooks and MCP servers. A project `.agents/hooks.json` ran its hooks and a project
  `.agents/mcp_config.json` spawned its stdio servers in a never-seen directory with no trust
  prompt and no setting stopped them, so the adapter refuses both files. Headless, a tool needing
  approval is auto-denied and the turn ends at once with status SUCCESS, an empty response, no
  structured output and `denied_actions`; the bridge treats that as a failed turn.
- cursor-agent 2026.11.11: ask mode relies on the model declining. `cli-config.json` in the
  directory named by `CURSOR_CONFIG_DIR` with permissions allow `[]` / deny `Write(**)`,
  `Shell(*)` makes the CLI itself refuse edits and shell ("Blocked by permissions
  configuration"), even with `--force`. `CURSOR_CONFIG_DIR` relocates only `cli-config.json` and
  chats (so `--resume` needs the same directory every run); login, `~/.cursor/projects` trust
  records, user hooks and skills stay global. The CLI rewrites that file and adds keys such as
  `authInfo`, so the bridge keeps the existing object and replaces only `permissions`. Whether a
  project `.cursor/cli.json` can loosen the denies was not established, so it is refused.
Probe, 2026-11-13 (agy 1.5.7 on macOS, gemini-3.8-flash-low, global login), real CLI:

- Read-only settings (`toolPermission: request-review`, deny `write_file(*)`,
  `command(*)`, `mcp(*)`): writes in `src/`, at the repository root and outside the
  repository, plus shell `touch`, all failed with "Matches user-configured deny rule".
  The turn continued and ended SUCCESS.
- Writing settings without agentMode (`request-review`, allow `write_file(src/)`, deny
  `command(*)`, `mcp(*)`): 1.5.7 starts headless runs in Auto when no mode is saved,
  as documented in its changelog. Auto approved workspace writes outside the allow rules:
  `other_root.txt`, `.env` and `.git/hooks/pre-commit` were written. A write outside the
  workspace was denied and ended the turn with `denied_actions`. The command deny held:
  `touch x` and `sh -c 'echo X > f'` were both refused.
- With the same writing settings plus `"agentMode": ""` (Default), writes to
  `other_root.txt` and `.env` outside the allow rules were denied and ended the turn
  with `denied_actions`; `src/` writes still worked. Every bridge run now pins this mode.
- In a real task run, the bridge post-run scope check caught the Auto write: REWORK,
  "outside allowed paths: other_root.txt".
- The old main-branch argv `--mode plan --sandbox --disable-slash-commands` printed
  "warning: --mode plan has no effect while slash command expansion is disabled."
  Every write and shell command succeeded. This branch already dropped `--mode`.

- agy executor probe, 2026-11-11 (agy 1.5.5 on macOS; the CLI updated itself from 1.5.3 that
  day), real CLI. Settings `{"toolPermission":"request-review","permissions":{"allow":
  ["write_file(src/)","write_file(README.md)"],"deny":["command(*)","mcp(*)"]}}` via
  `--settings`, no `--mode`: writes and edits (both governed by `write_file`) succeed only under
  an allowed directory or at an allowed exact file. Rules are relative to the workspace root;
  `write_file(src/)` matches at every depth by path component (not `src-evil/`), case-sensitive;
  `write_file(README.md)` matches only the root file; glob forms do not work; a catch-all deny
  `write_file(*)` beats every allow. A write outside the allows needs approval, which headless is
  auto-denied and the turn ends at once (exit 0, SUCCESS, empty response, no structured output,
  `denied_actions`; the step event even shows DONE). `..` is normalised and symlinks resolved
  before the check; a pre-existing hardlink inside an allowed directory is written in place,
  changing its other name (e.g. `.env`). Deny rules are case-sensitive on a case-insensitive
  filesystem (`.ENV` overwrote a denied `.env`). With `command(*)` denied the agent cannot run
  shell, delete or rename; `mcp(*)` deny still refuses MCP. Not gated: define/invoke_subagent
  (subagents seemed to inherit the write rules), schedule, image generation, `search_web`; reads
  in the workspace are not blocked; agy writes its own state under `~/.gemini`. `denied_actions`
  is not sticky across a `--conversation` resume, but `structured_output` can be stale there
  (previous turn's JSON while `response` holds new prose), so the bridge takes a resumed turn's
  answer only from the response text. Not changed: the agy login check is unreliable on 1.5.x.
- Still open for all three: no OS sandbox for the CLI's own process; agy keeps `search_web`,
  subagents and browser tools and runs user-level hooks/MCP; Cursor user-level hooks and skills
  load; Cursor sessions started before this change cannot be resumed; opencode user config is no
  longer loaded for bridge runs.

| Check | Result |
|---|---|
| Real opencode reviewer, `OPENCODE_SMOKE=1` (ollama-cloud/gpt-oss:120b) | approved as actor `agent`; worktree held only the executor's change; task DONE. 1 of ~10 runs produced no review (reviewer failure, task left in REVIEW for the client as designed; cause not captured) |
| Real agy reviewer, `AGY_SMOKE=1` (gemini-3.8-flash-low; run before the 1.5.3 probe, with `--mode plan --sandbox`) | approved as actor `agent`; worktree held only the executor's change; task DONE |

Probe, 2026-11-11 (opencode 2.2.6, macOS, free models only): opencode as executor.

- With `* * deny`, read/glob/grep allow and `{"action":"edit","resource":<pattern>,"effect":"allow"}`,
  the `write` and `edit` tools (both action `edit`) wrote only paths matching an allow pattern.
  The allowlist is fail-closed: `..`, `./`, `//`, absolute in-worktree paths and paths outside the
  worktree (`external_directory * deny`) were normalised and refused.
- Patterns match the normalised worktree-relative path, case-sensitively; `*` also matches `/`
  (`src/*` allows every depth); an exact name is anchored at the root; `docs/**/*.txt` does not
  match `docs/a.txt`; `*.md` reaches `.git/hooks/x.md`. The adapter therefore maps allowed paths
  to `<dir>/*` or exact files and refuses prefix-less globs.
- Deny rules are not a boundary on a case-insensitive filesystem: with `edit .env deny`, writing
  `.ENV` overwrote `.env`. A symlink already inside an allowed directory is followed (writes
  landed in other directories, `.env`, `.git/hooks` and outside the worktree); the tools cannot
  create symlinks. The adapter refuses a writing run when an allowed directory holds a symlink or
  a file matching the protected/excluded paths.
- The policy `tool.use edit:*` allow covers write and edit; a policy allow grants nothing without
  the permissions allow. The model saw edit, write, read, glob, grep, skill (policy-denied) and
  `opencode_worktree_remove` (governed by `edit`, refused outside the allowlist); no shell,
  delete, rename or patch tool. Other models may expose other tools: not established. A refused
  tool call is a `tool_use` event with `part.state.status` `error`; the run continues, exit 0.
- Design bar as for the other writing adapters: the CLI rules prevent what the post-run scope
  check cannot see or undo; an in-worktree out-of-scope write is detected afterwards (REWORK).
  There is no OS sandbox, so prefer Codex for untrusted work.

## 9. Known limitations / unsupported

- cursor-agent cannot execute (write) tasks; opencode and agy can only with `allow_write: true`
  (edit/write_file allowlist, no shell; see the 2026-11-11 executor probes in section 8). agy keeps `search_web`, subagents
  and browser tools that no rule blocks; opencode has no per-account login isolation.
- `account-add --provider google` (agy sign-in inside a fresh `HOME`) and `account-add` for
  Claude were exercised only against the fake CLIs: they are human-only, so no real login was
  run. `account-add` refuses to finish if the global login changed, as `account-login` does.

- Claude Read/Edit tools use permission rules. Probe on 2.1.306 (haiku, `dontAsk`):
  an unscoped `Write` created a file outside the working directory; with `Edit(./src/**)` and
  `Write(./src/**)` the same run wrote `src/inside.txt` and was refused for a file outside the
  worktree and for `other.txt` in it. The adapter therefore scopes Edit/Write to the task's
  `allowed_paths` and denies excluded and protected paths. Probe on 2.1.322 (haiku, `dontAsk`):
  `./` rules resolve against the Bash cwd, so after `cd sub` the in-scope `src/a.txt` was refused,
  `sub/src/b.txt` was written and `Read(./.env)` no longer blocked the root `.env`; with
  `//<absolute root>/src/**` and `Read(//<absolute root>/.env)` the same run wrote `src/a.txt` and
  refused the other two. Every project rule is therefore anchored at the absolute root. The same
  probe found that the Write tool obeys only `Edit(...)` rules: `Edit(./src/**)` alone allowed it,
  `Write(./src/**)` alone did not, and a `Write(./src/a.txt)` deny did not stop it while an
  `Edit(./src/a.txt)` deny did. The adapter emits both, so its Edit denies are the ones that hold. Read tools still read anything the
  user can subject to credential denies. With `allow_bash`, Bash and its children now run in
  Claude Code’s OS sandbox: credential reads denied, writes only to the working directory and
  `$TMPDIR`, no network, no unsandboxed retries, and failure if the sandbox is unavailable.
  macOS Keychain remains reachable; permission denies for `security` and `/usr/bin/security`
  are only a speed bump. Read/Edit tools keep their permission rules.
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
