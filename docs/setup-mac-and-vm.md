# Set up on a Mac and on a remote Linux VM

[README](../README.md) · [Configuration guide](configuration-guide.md) · [Configuration reference](configuration.md) · [CLI and MCP reference](cli-reference.md)

This page walks one project from nothing to a working setup with several Claude accounts, twice:
on a Mac, where secrets can live in the Keychain, and on a Linux VM you reach over SSH, where
they cannot. Every command is meant to be run by you in a terminal: the account commands are
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
| Linux VM | An environment variable of the process that runs the bridge | `--token-env AGENT_BRIDGE_CLAUDE_<NAME>_TOKEN` | You export the variable (see [Part 2](#part-2-a-linux-vm-over-ssh)) |

Add `--credential-kind api_key` for a Console API key; the default is `oauth_token`.
`account-set-token` refuses an account that does not use `credential: keychain`, so never run it
for a `--token-env` account.

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

Linux has no Keychain, so every Claude account on the VM uses `--token-env`. The two traps are
both about the shell that runs the bridge: an SSH command (`ssh host 'agent-bridge agents'`) and
Claude Code started from a remote session are **not interactive**, and non-interactive shells
skip most startup files.

### 1. Install and find the binary

Install as in Part 1, then ask an interactive shell where it landed (`uv tool install` puts it in
`~/.local/bin`):

```bash
ssh <host>
which agent-bridge
```

### 2. Make `PATH` and the secrets visible to non-interactive shells

Find the login shell:

```bash
getent passwd "$USER" | cut -d: -f7
```

For **zsh**, `~/.zshenv` is read by every zsh, interactive or not, so put both lines there:

```bash
grep -q '.local/bin' ~/.zshenv 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshenv
grep -q 'agent-bridge/env' ~/.zshenv 2>/dev/null || echo '[ -f ~/.config/agent-bridge/env ] && . ~/.config/agent-bridge/env' >> ~/.zshenv
```

For **bash**, put the same two lines at the very top of `~/.bashrc`, above any "if not running
interactively, return" guard (Ubuntu's file starts with one). This path was not tested for this
guide.

### 3. Create the project configuration

```bash
cd <PROJECT>
agent-bridge init
agent-bridge config-init claude-executor
agent-bridge config-check
```

### 4. Declare each account

The variable name must match `AGENT_BRIDGE_CLAUDE_<NAME>_TOKEN`; the namespace stops a tampered
config from pointing at another secret such as `GITHUB_TOKEN`. Use a name made of letters and
digits.

```bash
agent-bridge project-account-add <name> --provider anthropic --token-env AGENT_BRIDGE_CLAUDE_<NAME>_TOKEN
```

For a Console API key add `--credential-kind api_key`.

### 5. Store the secrets in a private file

Get each token on a machine where you can sign in (`claude setup-token` for a subscription, the
Console for an API key). On the VM, create the file with a prompt that does not echo. In **zsh**:

```bash
umask 077; mkdir -p ~/.config/agent-bridge; f=~/.config/agent-bridge/env; : > "$f"
for v in <NAME1> <NAME2>; do
  read -rs "t?token for $v: "; echo
  printf 'export AGENT_BRIDGE_CLAUDE_%s_TOKEN=%q\n' "$v" "$t" >> "$f"
done; unset t; wc -c "$f"
```

In **bash** the prompt flag differs; replace the `read` line with `read -rsp "token for $v: " t`.
`zsh` reads `-p` as something else, which leaves `t` empty and writes lines with no value: the
file then holds the right number of lines but only a few dozen bytes each. After the loop,
`wc -c` should show more than 100 bytes per account.

The file sits outside the repository with mode `600`. It is plaintext. A Claude agent with
`allow_bash` runs as you and can read it, so keep `allow_bash` off for work you do not trust, as
described in [Security](../README.md#security-model-and-limitations).

### 6. Check from the machine you will actually use

Run the checks through SSH, the same way the bridge will be started, not from an interactive
shell. The first prints only `set` and the length, never the token. In zsh:

```bash
ssh <host> 'for v in <NAME1> <NAME2>; do n="AGENT_BRIDGE_CLAUDE_${v}_TOKEN"; [ -n "${(P)n:-}" ] && echo "$v set, len ${#${(P)n}}" || echo "$v EMPTY"; done'
ssh <host> 'cd <PROJECT> && for a in <name1> <name2>; do agent-bridge account-verify "$a"; done'
ssh <host> 'cd <PROJECT> && agent-bridge agents | grep -E "\"agent\"|\"available\"|unavailable_reason"'
```

A subscription token is about 100 characters or more. If two accounts must have separate quota,
make sure their tokens differ without printing them:

```bash
ssh <host> 'for v in <NAME1> <NAME2>; do n="AGENT_BRIDGE_CLAUDE_${v}_TOKEN"; printf "%s " "$v"; printf %s "${(P)n}" | sha256sum | cut -c1-8; done'
```

Equal hashes mean the same token was pasted twice, and both accounts then share one quota.

### 7. Add the agents

Same as Part 1, step 4.

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
4. On a VM, delete that account's `export` line from `~/.config/agent-bridge/env`.

## When something does not work

| Symptom | Cause | Fix |
|---|---|---|
| `orchestrator.yaml is missing: start one with config-init first` | `init` does not write it | `agent-bridge config-init <example>` |
| `refused: ... orchestrator.yaml exists` | `config-init` never overwrites | It is already there: run `config-check`, do not delete it |
| `agent-bridge: command not found` over SSH, but fine when logged in | `~/.local/bin` is not on `PATH` for non-interactive shells | Part 2, step 2 |
| Account `logged out` over SSH, secret set in your own shell | The variable is only exported in that shell | Part 2, steps 2 and 5 |
| Account `logged out`, file has the right number of lines but is tiny | The `read` flag was wrong for the shell, so the tokens are empty | Recreate the file with Part 2, step 5 |
| `needs credential: keychain` from `account-set-token` | The account uses `--token-env` | Skip `account-set-token`; set the variable |
| `account-verify` is `ok: true` but a task stops with an auth error | `account-verify` does not call Anthropic | The token is revoked or expired: issue a new one |
| `AUTH` pause | Missing or wrong-prefix secret | Fix the secret, then `bridge_resume_task`; the bridge never switches account on AUTH |

## What was verified

The Linux VM steps were run on a real VM whose login shell is zsh: `config-init`, `project-account-add --token-env`,
`project-agent-add`, the environment file read by an SSH command, `account-verify`, `agents`,
`config-set` for roles and `routing.quota_fallback.enabled`, and removing agents and accounts.
The Mac steps follow the documented behavior of `--credential keychain` and `account-set-token`
and were not run for this page. The bash variants are untested.
