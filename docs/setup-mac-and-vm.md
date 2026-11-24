# Set up on a Mac and on a remote Linux VM

[README](../README.md) · [Configuration guide](configuration-guide.md) · [Configuration reference](configuration.md) · [CLI and MCP reference](cli-reference.md)

This page walks one project from nothing to a working setup with several Claude accounts, twice:
on a Mac, where secrets can live in the Keychain, and on a Linux VM you reach over SSH, where
they live in the bridge's private file store. Every command is meant to be run by you in a terminal: the account commands are
human-only and refuse to run without one.

Placeholders: `<name>` is an account name, `<PROJECT>` is the project's git root, `<NAME>` is the
account name in upper case.

## Choose the credential for each Claude account

| You have | Secret | Prefix | Billed to |
|---|---|---|---|
| A Pro or Max subscription | A login in the account's own home, or a `claude setup-token` token | `sk-ant-oat` | The subscription's 5-hour and weekly windows |
| API credit (Anthropic Console) | A Console API key | `sk-ant-api` | Credit, per token. No 5-hour or weekly window is reported, so `five_hour_pct` and `weekly_pct` never stop it: cap it with `budget_usd` |

One account holds one kind of secret. The bridge forwards only a value with the prefix of the
account's `credential_kind` and treats anything else as logged out. Where the secret is stored
depends on the machine:

| Machine | Where the secret lives | Flag on `project-account-add` | Store it with |
|---|---|---|---|
| Mac | The login Keychain | `--credential keychain` | `agent-bridge account-set-token <name>` |
| Linux VM | A `0600` file, `~/.agent-bridge/credentials/<name>` | `--credential file` | `agent-bridge account-set-token <name>` (see [Part 2](#part-2-a-linux-vm-over-ssh)) |

Add `--credential-kind api_key` for a Console API key; the default is `oauth_token`.
`account-set-token` refuses an account that uses neither `credential: keychain` nor
`credential: file`. The older `--token-env` (an environment variable) still works, but
`config-check` warns about it: see [Credentials on Linux](#credentials-on-linux).

## Part 1: a Mac

### 1. Install the bridge

```bash
git clone https://github.com/leetuanbmt/agent-orchestration-kit.git ~/tools/agent-orchestration-kit
uv tool install --editable ~/tools/agent-orchestration-kit
uv tool update-shell        # once, if ~/.local/bin is not on PATH
agent-bridge --help         # in a new shell
```

Install Claude Code and make sure `claude auth status` works for the account you will use.

### 2. Install the kit into the project and write the configuration

```bash
cd <PROJECT>                 # a git repository
agent-bridge init
agent-bridge config-init     # list the shipped examples
agent-bridge config-init claude-executor
agent-bridge config-check
```

`init` installs the MCP server, rules and skill but does **not** create `.ai/orchestrator.yaml`.
`project-account-*` and `project-agent-*` stop with `orchestrator.yaml is missing: start one with
config-init first` until it exists. `config-init` refuses to overwrite either file.

### 3. Add each account

Pick one of the three, per account.

**A subscription login (a new home for this project):**

```bash
agent-bridge project-account-add <name> --provider anthropic
agent-bridge account-login <name>        # opens the browser for a fresh login
```

**A `claude setup-token` token in the Keychain.** Run `claude setup-token` first, in a session
signed in to the right account, and keep the printed `sk-ant-oat...` token for the prompt:

```bash
agent-bridge project-account-add <name> --provider anthropic --credential keychain
agent-bridge account-set-token <name>    # prompts for the token; it never reaches argv
```

**A Console API key in the Keychain:**

```bash
agent-bridge project-account-add <name> --provider anthropic --credential keychain --credential-kind api_key
agent-bridge account-set-token <name>    # prompts for the sk-ant-api... key
```

`project-account-add` writes the account to `.ai/accounts.yaml` and prints an agent line under
`paste`. Add the agent yourself in the next step.

### 4. Add an agent for the account

The command previews until you pass `--apply`, which backs `orchestrator.yaml` up to
`.ai/backup-<stamp>/` first. The agent name is yours; `agent-<name>` follows the convention
`project-account-add` prints.

```bash
agent-bridge project-agent-add agent-<name> --account <name> --role executor          # preview
agent-bridge project-agent-add agent-<name> --account <name> --role executor --apply
```

Add `--default` to make it the first executor.

### 5. Check it

```bash
agent-bridge account-verify <name>
agent-bridge agents
agent-bridge health
```

`account-verify` prints `ok: true` when the login or secret is present and well formed. It does
not call Anthropic, so a token that is revoked or expired still passes. Run one small task to
prove the account works.

## Part 2: a Linux VM over SSH

Linux has no Keychain, so every Claude account on the VM uses `--credential file`. An SSH command
(`ssh host 'agent-bridge agents'`) and Claude Code started from a remote session are **not
interactive**, and non-interactive shells skip most startup files: only `PATH` has to reach them.
The secrets do not, because the bridge reads them from its file store at run time.

### 1. Install and find the binary

Install as in Part 1, then ask an interactive shell where it landed (`uv tool install` puts it in
`~/.local/bin`):

```bash
ssh <host>
which agent-bridge
```

### 2. Make `PATH` visible to non-interactive shells

Find the login shell:

```bash
getent passwd "$USER" | cut -d: -f7
```

For **zsh**, `~/.zshenv` is read by every zsh, interactive or not:

```bash
grep -q '.local/bin' ~/.zshenv 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshenv
```

For **bash**, put the same line at the very top of `~/.bashrc`, above any "if not running
interactively, return" guard (Ubuntu's file starts with one). This path was not tested for this
guide.

Never put a token in a shell profile or in a file one sources: every shell then carries it,
including the shells of agent sessions, and anything that prints its environment publishes it.

### 3. Create the project configuration

```bash
cd <PROJECT>
agent-bridge init
agent-bridge config-init claude-executor
agent-bridge config-check
```

### 4. Declare each account

```bash
agent-bridge project-account-add <name> --provider anthropic --credential file
```

For a Console API key add `--credential-kind api_key`.

### 5. Store each secret

Get each token on a machine where you can sign in (`claude setup-token` for a subscription, the
Console for an API key). On the VM, in a terminal (`ssh -t <host>`), run once per account:

```bash
agent-bridge account-set-token <name>
```

It prompts without echo (or reads one line from a pipe), refuses a value without the account's
prefix, and writes `~/.agent-bridge/credentials/<name>` atomically with mode `0600` in a `0700`
directory. `agent-bridge account-set-token <name> --delete` removes it.

### 6. Check from the machine you will actually use

Run the checks through SSH, the same way the bridge will be started, not from an interactive
shell:

```bash
ssh <host> 'cd <PROJECT> && for a in <name1> <name2>; do agent-bridge account-verify "$a"; done'
ssh <host> 'cd <PROJECT> && agent-bridge agents | grep -E "\"agent\"|\"available\"|unavailable_reason"'
```

`account-verify` shows `reference: file ~/.agent-bridge/credentials/<name>`, `present` and
`well_formed`, plus a `problem` when the bridge refuses the file. If two accounts must have
separate quota, make sure their tokens differ without printing them:

```bash
ssh <host> 'for a in <name1> <name2>; do printf "%s " "$a"; sha256sum < ~/.agent-bridge/credentials/"$a" | cut -c1-8; done'
```

Equal hashes mean the same token was pasted twice, and both accounts then share one quota.

### 7. Add the agents

Same as Part 1, step 4.

## Credentials on Linux

The bridge reads a `credential: file` secret only when it builds an account's run environment
or checks the credential, and refuses it, without reading it, when the file is a symlink, is not
a regular file, is not owned by you, or is group- or world-readable, or when
`~/.agent-bridge/credentials` is not a `0700` directory. The refusal shows up as a `problem` in
`account-verify` and as an `AUTH` pause on a run.

What keeps the file from the agents:

| Who | Can it read `~/.agent-bridge/credentials`? |
|---|---|
| A Claude agent's Read, Glob and Grep tools | No: denied by `--disallowedTools` rules, like the account homes |
| A Claude agent's Bash (`allow_bash: true`) | No: in the sandbox's `denyRead` list |
| A verification command on macOS (Seatbelt) | No: denied by the profile |
| A verification command on Linux (Landlock) | **Yes.** Landlock restricts writes only. Its output is still redacted |
| A Codex agent (`workspace-write`) | **Yes.** The `workspace-write` sandbox lets commands read any file (probed with Codex 0.169 on macOS), so the store is not protected from Codex. Codex permission profiles can deny a path, but the bridge does not use them yet |
| Another process running as your user | Yes. The bridge only stops it reading the bridge's own environment and memory: on Linux `serve` and every CLI command mark themselves non-dumpable (`PR_SET_DUMPABLE 0`) |

The bridge also moves every `AGENT_BRIDGE_CLAUDE_*_TOKEN` variable out of its own environment
when it starts, so no child inherits one, and redacts the exact value of every secret it loaded
from stored output (agent reports, logs, events, check output).

List any other file that holds secrets, such as an old token env file a wrapper still sources, in
`credential_paths` in `.ai/config.json` (see [Configuration](configuration.md)). Claude agents and
checks are then denied it the same way.

### Move from `token_env` to the file store

For each account that has `token_env`, in a terminal on the VM. Keep the old env file until
`account-verify` passes: you paste the same token again at the prompt.

```bash
agent-bridge config-unset accounts.<name>.token_env --apply
agent-bridge config-set accounts.<name>.credential file --apply
agent-bridge account-set-token <name>
agent-bridge account-verify <name>
```

For an account with `credential_kind: api_key`, take the kind off first and put it back last
(`credential_kind` needs a credential source, and `token_env` and `credential` cannot be set
together):

```bash
agent-bridge config-unset accounts.<name>.credential_kind --apply
agent-bridge config-unset accounts.<name>.token_env --apply
agent-bridge config-set accounts.<name>.credential file --apply
agent-bridge config-set accounts.<name>.credential_kind api_key --apply
agent-bridge account-set-token <name>
agent-bridge account-verify <name>
```

These sequences were run against a scratch project through the same code as `config-set` and
`config-unset`. When every account is moved, delete
the env file (`~/.config/agent-bridge/env` in earlier versions of this guide) and the line that
sources it from `~/.zshenv` or `~/.bashrc`, then open new shells and restart Claude Code so no
process keeps the old variables.

`agent-bridge config-check` warns about each account that still uses `token_env`.

## Roles, quota and limits (both machines)

Roles decide who plans, executes and reviews. Put a different account on the reviewer than on the
planner so one account's quota does not stall both:

```bash
agent-bridge config-set roles.planner.agents "[agent-<name1>]" --apply
agent-bridge config-set roles.reviewer.agents "[agent-<name2>]" --apply
```

Stop an account at a share of its provider window (subscription accounts only):

```bash
agent-bridge project-account-limits <name> --five-hour-pct 80 --weekly-pct 80
```

Move a task that hits QUOTA to the next eligible agent on its own, up to the configured number
of switches. Without it the task waits for you:

```bash
agent-bridge config-set routing.quota_fallback.enabled true --apply
```

Window percentages come from the limits the Claude CLI reported on its last run through the
bridge, so a limit has no effect before an account's first run.

## Remove an account

Order matters, because each command refuses to leave the configuration invalid. The agent must go
before the account, and a role must not be left empty:

1. Point every role that uses the agent at another agent (`config-set roles.<role>.agents ...`).
2. `agent-bridge project-agent-remove <agent> --apply` (it lists unfinished tasks pinned to it).
3. `agent-bridge project-account-remove <name>` (you type the name to confirm). The auth home is
   kept; delete it yourself if it should go.
4. A `credential: file` or `credential: keychain` secret is deleted with the account.

## When something does not work

| Symptom | Cause | Fix |
|---|---|---|
| `orchestrator.yaml is missing: start one with config-init first` | `init` does not write it | `agent-bridge config-init <example>` |
| `refused: ... orchestrator.yaml exists` | `config-init` never overwrites | It is already there: run `config-check`, do not delete it |
| `agent-bridge: command not found` over SSH, but fine when logged in | `~/.local/bin` is not on `PATH` for non-interactive shells | Part 2, step 2 |
| `needs credential: file` from `account-set-token` | The account uses `--token-env` | [Move it to the file store](#move-from-token_env-to-the-file-store) |
| `problem: ... group- or world-readable` | The file's mode lets others read it | `chmod 600 ~/.agent-bridge/credentials/<name>` |
| `problem: ... must be a 0700 directory` | The store directory is shared or a symlink | `chmod 700 ~/.agent-bridge/credentials` (replace a symlink with a directory) |
| `problem: the macOS Keychain is not available` | `credential: keychain` on Linux | Use `credential: file` |
| `account-verify` is `ok: true` but a task stops with an auth error | `account-verify` does not call Anthropic | The token is revoked or expired: issue a new one |
| `AUTH` pause | Missing or wrong-prefix secret | Fix the secret, then `bridge_resume_task`; the bridge never switches account on AUTH |

## What was verified

The Linux VM steps were first run on a real VM whose login shell is zsh with `--token-env`:
`config-init`, `project-account-add`, `project-agent-add`, `account-verify`, `agents`,
`config-set` for roles and `routing.quota_fallback.enabled`, and removing agents and accounts.
The `credential: file` steps (Part 2, steps 4 to 6, and the migration) are covered by the kit's
tests on macOS and were not rerun on the VM for this page. The non-dumpable flag and the Landlock
row of the table are Linux behaviour that the tests check only on Linux.
The Mac steps follow the documented behavior of `--credential keychain` and `account-set-token`
and were not run for this page. The bash variants are untested.
