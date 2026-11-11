# Configuration reference

[README](../README.md) · [Configuration guide](configuration-guide.md) · Configuration reference · [CLI and MCP reference](cli-reference.md)

New to the settings? Start with the [configuration guide](configuration-guide.md); this page
lists every key and default.

agent-bridge reads three optional files from the project's `.ai/` directory. Each setting has
one owner:

| File | Owns | Tracked by git |
|---|---|---|
| `.ai/accounts.yaml` | The accounts this project uses: provider, home or a `login:` reference, credentials reference, `max_concurrency`, `limits` | No (it names machine-specific auth homes) |
| `.ai/orchestrator.yaml` | Agents, roles, routing and workflow stages | No |
| `.ai/config.json` | Safety and runtime: timeouts, Codex sandbox, verification sandbox, path rules, env rules, verification allowlist | Yes (an agent changing it is a scope violation) |

`agent-bridge init` writes `.ai/.gitignore`, which ignores everything in `.ai/` except
`config.json` and `.gitignore` itself.

Because `.ai/config.json` is shared through git, a commit that loosens it (for example
`"verify_sandbox": "none"` or a shorter `env_deny`) applies to everyone who pulls it. Review
every change to that file as a security change.

**Older layouts still load unchanged.** Accounts in `orchestrator.yaml`, agents or executor
entries in `accounts.yaml`, `workflow.verify_*` in `orchestrator.yaml`, and registry logins used
without a project declaration all keep working. `config-check` lists them under `deprecated`,
and `health` reports a `config_deprecated` warning. `agent-bridge config-migrate` (human only)
previews the move of accounts and agents. `--apply` backs up both files to
`.ai/backup-<UTC stamp>/` and writes them; the effective configuration must stay identical, or
nothing is written. Comments in `orchestrator.yaml` are not kept. `config-migrate` does not move
`workflow.verify_*`; move those to `.ai/config.json` by hand. A name declared in two places is
an error that names both files, never a silent override.

Without `orchestrator.yaml` the **legacy setup** applies: the MCP client (your Claude Code
session) plans and reviews, Codex executes, and accounts come from `.ai/config.json`. This is
the 0.1 behaviour.

## `.ai/orchestrator.yaml` and `.ai/accounts.yaml`

Start from a shipped example and validate it:

```bash
agent-bridge config-init                # list the examples
agent-bridge config-init orchestrator   # write .ai/orchestrator.yaml and .ai/accounts.yaml; refuses if either exists
agent-bridge config-check               # schema and account-home isolation; errors name the field (exit 1)
agent-bridge agents                     # resolved roles, agents and availability
```

To change one setting without editing a file, use `agent-bridge config-set <key> <value>` and
`config-unset <key>` (human only). The first segment of the dotted key picks the file, the value is
parsed as YAML, the whole configuration is validated first, and nothing is written without
`--apply`; the changed file is backed up to `.ai/backup-<UTC stamp>/`. See the
[CLI reference](cli-reference.md#human-only-commands).

`config-init`, `config-migrate`, `config-set`, `config-unset`, `project-account-add`,
`project-account-limits` and `project-account-remove` are meant to be run by a human: they refuse to run without a terminal,
and the `.claude/settings.json` that `agent-bridge init` installs also denies `config-init` to Claude Code.

`.ai/accounts.yaml`:

```yaml
accounts:
  codex-work:  {provider: openai,    home: ~/.codex-accounts/homes/work}
  claude-main: {provider: anthropic, home: ~/.claude-accounts/main, max_concurrency: 1}
  shared:      {login: work, limits: {weekly_pct: 90}}   # a machine registry login, see below
```

`.ai/orchestrator.yaml`:

```yaml
version: 1
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
| `accounts.<id>` (in `accounts.yaml`) | `provider`: `openai`, `anthropic`, `google`, `opencode` or `cursor`. `home`: isolated auth home; omit it to use the CLI's inherited global login (opencode and cursor accept no `home`). `login`: the name of a machine registry login instead; provider and home come from the registry (`home` is then refused, a `provider` must match). `max_concurrency`: 1–8, default 1. `token_env` (anthropic only): the name of a variable holding a `claude setup-token` token, see [Accounts](#accounts). `limits`: optional per-account limits, see [Per-account limits](#per-account-limits-opt-in). |
| `agents.<id>` | `adapter`: `codex-cli`, `claude-cli`, `client`, `opencode-cli`, `cursor-cli` or `agy-cli`. `account` (required except for `client`), optional `provider` (must match the adapter), `model`, `capabilities` (subset of `[read, write]`), `allow_write` (default true for `codex-cli`, false otherwise), `allow_bash` (`claude-cli` only), `timeout_s` (≥ 30), `enabled` (default true). |
| `roles.<name>` | An agent id, or `{agents: [...], default: <agent>, strategy: ordered \| explicit \| quota, requires: read \| write}`. `default` is tried first; the other agents are fallbacks in list order. `quota` picks the eligible agent whose account has the most remaining quota (unknown quota last, ties in list order). `default` cannot be combined with `strategy: explicit` or `quota`. |
| `routing.slot_wait_s` | 0–86400, default 1800. How long `bridge_run_workflow` keeps retrying, with backoff from 5 s to 60 s, a task whose account slots (or the worktree's writer slot) are held by another bridge process. `0` ends the workflow `WAITING` at once, as before. |
| `routing.quota_fallback` | `enabled` (default false), `min_remaining_pct` (default 10), `agents` (candidates in order; default: the executor role's agents), `max_switches` (1–20, default 3), `keep_sessions` (default false). See [Per-account limits](#per-account-limits-opt-in). |
| `workflow` | `stages`: ordered subset of `plan, execute, test, review`, default `[execute, test, review]`; must include `execute` and at least one of `test` / `review`. `max_rework`: 0–4, default 2. `verify_sandbox` and `verify_writable` are still read here but are deprecated: set them in `.ai/config.json`, see [Verification sandbox](#verification-sandbox). |
| `security` | `project_isolation` and `human_approval_for_sensitive_actions`. Both are fixed to `true`; the keys exist only to make the policy explicit. |

Ids use `[a-z0-9_-]`, at most 32 characters. Validation rejects unknown keys, unknown ids, an
agent whose account belongs to another provider, two accounts sharing one auth home, a role no
listed agent can play, and a `client` agent in the `executor` role.

Role rules:

- `executor` is required and needs `write`; `planner` and `reviewer` need `read`.
- A `client` agent can only read: it plans and reviews through the MCP tools and is never
  started by the bridge.
- `cursor-cli` and `agy-cli` are read-only; `allow_write` or a `write` capability on them is
  rejected. `opencode-cli` is read-only unless `allow_write: true` (the `writes` default stays
  Codex-only). Its writing run gets one opencode `edit` allow per allowed path (`src/**` or a
  directory becomes `src/*`; a glob with no directory prefix such as `*.md` refuses the run), no
  shell, and no deny rules. The allow rules are broader than the task globs (every depth under the
  directory) and the bridge's post-run scope check enforces the task scope. The agent cannot run
  tests, delete or rename. A symlink or a protected/excluded file under an allowed directory
  refuses the run. Probed with free models only; no OS sandbox, so prefer Codex for untrusted
  work; `opencode_worktree_remove` stays exposed. Reviewer and planner runs of the same agent stay
  deny-all read-only.
- Built-in role names are `planner`, `executor`, `reviewer`, `advisor` and `tester`. Other role
  names are accepted, but only planner, executor and reviewer have a stage; testing is the
  bridge's own `test` stage.

### Shipped examples

Each example is a pair: `examples/<name>.yaml` becomes `.ai/orchestrator.yaml` and
`examples/<name>.accounts.yaml` becomes `.ai/accounts.yaml`.

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

Set both keys in `.ai/config.json` (`"verify_sandbox": "auto"`, `"verify_writable": [...]`).
`workflow.verify_sandbox` / `workflow.verify_writable` in `orchestrator.yaml` still load as a
deprecated location. A key set in both files is an error naming both, so moving a key can
never silently weaken the sandbox.

| `verify_sandbox` | Effect |
|---|---|
| `auto` (default) | A check may write only to the worktree, a private `TMPDIR` (removed afterwards) and `verify_writable`; reads and the network are not restricted. Without either (Windows, old Linux kernels, or a bridge that already runs inside a sandbox) checks run unconfined and `health` reports the `verify_unconfined` problem (NOT OK) until a human sets `none`. |
| `strict` | As `auto`, and TCP connect/bind are denied (Landlock ABI 4+, kernel 6.7+, or Seatbelt). Without a sandbox, checks are refused rather than run unconfined. |
| `none` | No confinement, accepted by a human; `health` keeps a `verify_unconfined` warning. |

Test runners that write caches outside the project need them in `verify_writable`, for example
`"verify_writable": ["~/.cache/uv"]` for `uv run pytest` or `["~/.npm"]` for `npm test` (absolute
paths or `~/...`); the check's
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
| `codex_bin` / `claude_bin` / `opencode_bin` / `cursor_bin` / `agy_bin` | `codex` / `claude` / `opencode` / `cursor-agent` / `agy` | CLI executables. |
| `ignore_user_config` | `true` | Pass `--ignore-user-config` to Codex, so `~/.codex/config.toml` (hooks, MCP servers, profiles) is not loaded. |
| `claude_hook_settings` | `null` | Absolute path to a JSON file outside the project containing only a `hooks` object; passed to every Claude run as `--settings`. Trusted code, runs unsandboxed. Set it only by hand. |
| `default_timeout_s` / `max_timeout_s` | `1800` / `7200` | Per-run time limits. |
| `max_attempts` | `3` | Legacy setup only; with `orchestrator.yaml` use `workflow.max_rework`. |
| `network_retries` / `retry_backoff_s` | `1` / `10` | Bounded retry for network errors only. |
| `verify_timeout_s` | `900` | Limit per verification command. |
| `verify_sandbox` | not set (`auto`) | Verification sandbox mode: `auto`, `strict` or `none`; see [Verification sandbox](#verification-sandbox). |
| `verify_writable` | not set (`[]`) | Extra absolute directories (or `~/...`) verification commands may write. |
| `verify_allowlist` | `pytest`, `uv`, `python`, `python3`, `ruff`, `mypy`, `make`, `npm`, `npx`, `pnpm`, `yarn`, `node`, `go`, `cargo`, `git`, `true`, `false` | Executables a verification command may start. |
| `protected_paths` | `.env*`, `*.pem`, `*.key`, `.git/**`, `.ai/**`, `.mcp.json`, `.claude/settings*.json` | Never writable by a task and never allowed in `allowed_paths`. A project value adds to these defaults; it cannot remove them. |
| `sensitive_paths` | migrations, `auth/`, `deploy/`, `.github/workflows/`, `*secret*`, `*credential*`, `Dockerfile` | A change under these requires human approval before `DONE`. |
| `env_deny` / `env_allow` | `*_API_KEY`, `*_TOKEN`, `*_SECRET*`, `*PASSWORD*`, `AWS_*` / none | Variables removed from agent and verification environments. A project `env_deny` adds to the defaults; it cannot remove them. `env_allow` re-allows a denied variable for agent processes only, never for verification commands. |
| `lock_dir` | `$AGENT_BRIDGE_HOME/locks` | Cross-process account slot locks, shared by every project on the machine. |
| `accounts` / `default_account` / `auto_switch` | — | Legacy setup only. With `orchestrator.yaml`, setting `accounts` or an enabled `auto_switch` is an error. |

## Accounts

An account is one login with its own auth home. A project declares the accounts it uses in
`.ai/accounts.yaml`. Machine-wide logins are created with `agent-bridge account-add`, and a
project uses one only by declaring it with `login:`.

| Provider | Adapter | What `home` sets |
|---|---|---|
| `openai` | `codex-cli` | `CODEX_HOME` |
| `anthropic` | `claude-cli` | `CLAUDE_CONFIG_DIR` |
| `google` | `agy-cli` | `HOME` (a whole home directory) |
| `opencode` | `opencode-cli` | No `home`: opencode has a single global login |
| `cursor` | `cursor-cli` | No `home`: Cursor has a single global login (`cursor-agent login`) |

- **No `home`.** The CLI's inherited global login (`~/.codex`, `~/.claude`) is used; the bridge
  never writes to it.
- **With `home`.** A separate login. Homes must be absolute, outside the project and outside
  the global homes, and never shared by two accounts.
- **Logging in.** A human runs `agent-bridge account-login <name>` for a fresh session in that
  home. For Codex it fails if the global `~/.codex/auth.json` changes during the login.
  - **Codex with an OpenAI API key.** `agent-bridge account-login <name> --with-api-key` asks
    for the key at a hidden prompt and pipes it to `codex login --with-api-key` on stdin
    (codex 0.166 reads it from there), with that account's `CODEX_HOME`. Codex stores it in
    that home's `auth.json`, as it does its own logins; the bridge keeps no copy. A value not
    starting with `sk-` is refused before Codex runs. It needs a named `openai` account with
    its own home, and cannot be combined with `--device-auth`.
  - An API-key account bills per token and reports no 5-hour or weekly window, so
    `five_hour_pct` and `weekly_pct` never stop it; use `budget_usd`.
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
- **Adding an account from the CLI.** Instead of editing `.ai/accounts.yaml`, a human can
  run:

  ```bash
  agent-bridge project-account-add claude-thuyseatch --provider anthropic --credential keychain --executor
  agent-bridge account-set-token claude-thuyseatch
  agent-bridge project-account-add shared --login work   # declare a machine registry login
  ```

  - The command writes only the account to `.ai/accounts.yaml`. It never rewrites
    `orchestrator.yaml`; a name that already exists in either file is refused.
  - It declares the account with its own new `0700` home (default
    `~/.agent-bridge/homes/<name>`, or `--home`), or with `--login <name>` a reference to a
    registry login.
  - It prints, under `paste`, the agent line (`agent-<name>`; Codex and Claude agents get
    `allow_write`) to add to `agents:` in `orchestrator.yaml`. With `--executor` it also says
    to put that agent first in `roles.executor`.
  - The merged config is validated, including the home isolation check, before anything is
    written. `config-check` shows the result.
  - `agent-bridge project-account-remove <name>` asks you to type the name, then removes the
    account, any agent entries in `accounts.yaml` that use it (older layout) and the stored
    Keychain token. It keeps the home, which may hold a login. An account it does not own is
    refused with the file that does. Remove the agents in `orchestrator.yaml` that use the
    account first: the merged config must still load, or nothing is written.
  - Usage limits per provider window, at creation (`--five-hour-pct 80 --weekly-pct 90`) or
    later:

    ```bash
    agent-bridge project-account-limits vydnt --five-hour-pct 80 --weekly-pct 90
    agent-bridge project-account-limits vydnt --clear   # remove both
    ```

    Each value is the percent of that window used at which the account stops taking work;
    these are `limits.five_hour_pct` and `limits.weekly_pct`. Other `limits` keys in the entry
    are kept.
  - All three commands are human-only and need a terminal.
- **An Anthropic Console API key (`credential_kind: api_key`).** With `credential: keychain` or
  `token_env`, the stored secret can be an API key instead of a setup-token:

  ```bash
  agent-bridge project-account-add claude-api --provider anthropic --credential keychain --credential-kind api_key
  agent-bridge account-set-token claude-api   # prompts for the sk-ant-api... key
  ```

  - Runs get only `ANTHROPIC_API_KEY`, and only for a value starting with `sk-ant-api`. A
    setup-token in an `api_key` account, or a key in an `oauth_token` account (the default), is
    never forwarded and reads as logged out: the two bill different sources.
  - When the named secret is missing or has the wrong prefix, the run stops with PAUSED AUTH
    instead of using whatever login the home holds. This applies to both kinds.
  - With `ANTHROPIC_API_KEY` as its only credential (empty `CLAUDE_CONFIG_DIR`), `claude auth
    status` reports `loggedIn: true`, `authMethod: api_key`, `apiKeySource:
    ANTHROPIC_API_KEY` (Claude Code, probed 2026-11-08 with a fake key; the CLI does not check
    the key there). A rejected key shows up on the first run as PAUSED AUTH.
  - API-key usage is billed per token and does not report the 5-hour and weekly windows, so
    `five_hour_pct` and `weekly_pct` never stop such an account. Use `budget_usd` instead.
- **Checking one account.** Both commands are read-only and safe to run from Claude Code:
  - `agent-bridge account-show <name>` prints the account's `account-list` row, plus where its
    token comes from (`keychain agent-bridge/<key>` or `env <VAR>`, never the value), the
    agents that use it and the roles whose `default` is one of them.
  - `agent-bridge account-verify <name>` checks the CLI login and, for a `token_env` or
    keychain account, that the secret is present and has its kind's prefix (`sk-ant-oat` or
    `sk-ant-api`). It exits 1 when the account cannot run.
- **The same token in the macOS Keychain (`credential: keychain`).** Instead of `token_env`
  (the two cannot be combined), an `anthropic` account can keep its `setup-token` token in the
  login Keychain. A human stores it once with `agent-bridge account-set-token <name>`.
  `security` prompts for the token itself, so it never appears on a command line. Remove it with
  `--delete`.
  - The bridge reads the item on every run, so a new token needs no restart of Claude Code.
  - The item is keyed by the account's home (service `agent-bridge`), so projects that use
    the same account share it. A missing, locked or wrong-shaped item reads as logged out.
  - Only `CLAUDE_CODE_OAUTH_TOKEN` reaches that account's runs; a global token or an
    `env_allow`ed `ANTHROPIC_API_KEY` is removed from them. Under Seatbelt, verification
    commands cannot reach the Keychain: the `security` binary, the Security services and the
    keychain files are denied. Without the services deny, a `ctypes` call into
    Security.framework reached it; this was probed.
  - Limit: the Keychain keeps the token off disk and out of git. It does not stop unsandboxed
    code running as you, such as a Claude agent with `allow_bash`, from asking `security`
    for it. Linux has no Keychain support; use `token_env` there.

### Machine-wide accounts

`agent-bridge account-add work --provider openai` (human only) creates
`~/.agent-bridge/homes/work` (mode 0700), records it in `~/.agent-bridge/accounts.json` and runs
the provider's own login inside it. `--home DIR` registers an existing home instead. The
registry is a catalog of logins: a project uses one by declaring it in `.ai/accounts.yaml`
(`shared: {login: work}`), with its own `max_concurrency` and `limits`. Registry logins no agent
references do not appear in the project; machine-wide commands (`account-login`, `-use`,
`-show`, `-verify`, `-clear`) still find every one. For compatibility, an agent whose
`account:` names a registry login directly still runs with the registry's settings, listed as
deprecated.

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
`account-login <name>` runs `opencode auth login`. Cursor works the same way:
`account-add <name> --provider cursor` registers one home-less account and `account-login <name>`
runs `cursor-agent login`. `cursor-cli` runs `cursor-agent` in `--mode ask` with `--sandbox
enabled` and `--trust`, and sets `CURSOR_CONFIG_DIR=<state dir>/cursor-config` whose
`cli-config.json` permissions deny `Write(**)` and `Shell(*)` (other keys kept; the file is
private and never logged). Chats live in that directory, so Cursor sessions started before this
change cannot be resumed. Cursor executes hooks from `.cursor/hooks.json`, `.claude/settings.json`
and `.claude/settings.local.json` outside its sandbox, so the adapter refuses a directory where
`.cursor/hooks.json` or `.cursor/cli.json` exists or either `.claude` file has a non-empty
`hooks` value or is not a JSON object. `opencode-cli` runs with a deny-all V2 permission config
(read, glob and grep only) and an empty per-run `XDG_CONFIG_HOME`, so your opencode user config
(plugins, MCP servers, agents, skills) is not loaded for bridge runs; `agy-cli` runs with session
`--settings` (`request-review`; `write_file(*)`, `command(*)`, `mcp(*)` denied), no `--mode`, and
refuses a directory with `.agents/hooks.json` or `.agents/mcp_config.json`. A turn where agy
reports `denied_actions` fails. Neither has an OS sandbox; agy still allows `search_web`,
subagents and browser tools and runs user-level hooks and MCP servers. A refused agent is listed `available: false` with that reason, is skipped when a
planner or reviewer is picked (the task stays in REVIEW with an `agent_review_skipped` event), and
`health` warns (`adapter_refused`). `--trust` leaves the worktree trusted in `~/.cursor/projects/`
and chats under `~/.cursor/chats/`. Cursor reports no cost, so a cursor account with
`limits.budget_usd` or `limits.pricing` is a configuration error; percentage and cooldown limits
are allowed.

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
as before. Limits live with the account in `.ai/accounts.yaml` (still read from an account in
`orchestrator.yaml` or a directly referenced registry login, both deprecated). They are
human-owned, and no agent, MCP tool or UI can change them. `config-migrate` copies a registry
login's limits into the project so no cap is lost.

```yaml
# .ai/accounts.yaml
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
  their own against the same spend. A `login:` entry uses only the project's limits, never the
  registry's.

**Troubleshooting.**

| Symptom | Cause and fix |
|---|---|
| `account X over its configured limit: budget_usd.day: ... N run(s) with unknown cost` | A run's cost could not be determined. Add `pricing` or `reserve_usd`, or wait for the period to reset. |
| A task waits with `auto_switch_skipped: switch limit reached` | The task moved `max_switches` times. Check `agent-bridge usage`, then resume it after a reset. |
| `usage` shows a check as `unavailable` | The provider has not reported that window yet. Run one task on the account, or use `any_window_pct`. |

## State and storage

| Path | Tracked | Content |
|---|---|---|
| `.ai/config.json` | Yes | Bridge settings, including the verification sandbox |
| `.ai/accounts.yaml` | No | The project's accounts and their limits |
| `.ai/orchestrator.yaml` | No | Agents, roles, routing, workflow |
| `.ai/backup-<UTC timestamp>/` | No | Copies of files `init` or `config-migrate --apply` changed |
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
`rollback`). The `.claude/settings.json` that `agent-bridge init` installs denies the applying
forms to Claude.

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
