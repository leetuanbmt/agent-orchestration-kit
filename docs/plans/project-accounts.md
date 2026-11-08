# Plan: project-level account and credential management

Status: **draft for review**. Nothing is implemented yet. Source: the request in `prompts.md`.

It builds on branch `feat/audit-driven-improvements` (per-account `token_env`, commit 22a0ece).
The Phase 1 audit came first, as the request asks.

## 1. What already exists

| Requirement (`prompts.md`) | Today | Where |
|---|---|---|
| Many accounts per project, several per provider | Yes. Accounts are declared in `.ai/orchestrator.yaml`; machine-wide ones live in `~/.agent-bridge/accounts.json` (registry) and are usable by name in every project | `bridge/orchestrator.py` (`AccountProfile`, `parse`), `bridge/accounts.py` (`register`) |
| Add / list / remove an account | `account-add`, `account-list`, `account-remove` (human only) | `bridge/cli.py` |
| Login with the provider's own CLI | `account-login`: `claude auth login` / `codex login [--device-auth]` inside the account's home | `accounts.login_argv` |
| Use existing CLI login | An account without `home` uses the global login, read only | `accounts.resolve_profile` |
| Global login never changed | Checked before and after every login | `cli.py` (`global_login_marker`) |
| Login state | `account-list`, `health`, `agents` | `accounts.login_status` |
| Default account and per-execution override | `roles.<role>.default`, then role order; a task pins `agent` or `account`; `agent-bridge reassign` (human) moves a task | `bridge/gate.py` (`_route`), `.claude/rules/orchestration.md` |
| No silent fallback when auth fails | AUTH pauses the task; quota fallback is opt-in and never triggers on AUTH | `gate._quota_fallback` |
| Process isolation | Each run gets its account's `CODEX_HOME` / `CLAUDE_CONFIG_DIR` / `HOME` in its own env. Nothing global is switched | `accounts.account_env` |
| Concurrency | Machine-wide slot locks per auth home, `max_concurrency` per account | `accounts.AccountLock` |
| setup-token per account | `token_env: AGENT_BRIDGE_CLAUDE_<NAME>_TOKEN`; only a value starting with `sk-ant-oat` is forwarded to that account's runs | `accounts.account_env`, `orchestrator.AccountProfile` |
| Provider adapters | One adapter class per CLI; the provider is chosen by the adapter | `bridge/executor.py` (`ADAPTERS`) |
| Secrets kept out of logs and agents | Env scrub (`*_TOKEN`, `*_API_KEY`, …), output redaction, read denies on credential files and `.ai/orchestrator.yaml` | `bridge/security.py`, `bridge/executor.py` |

## 2. Gaps

These come from the Phase 1 audit (`docs/audits/03-settings-auth-providers.md` §2).

| # | Gap | Effect today |
|---|---|---|
| G1 | **No secure storage for a token or key.** `token_env` points at an environment variable. | The token sits in a shell file. The MCP server only sees it after Claude Code restarts from a shell that exported it. |
| G2 | **No per-account Anthropic API key.** `ANTHROPIC_API_KEY` is scrubbed. `env_allow` would hand it to every adapter and to every verification command (AUTH-01). | API-key accounts for Claude cannot be used. |
| G3 | **No OpenAI API-key login through the kit.** Codex has its own mechanism (`codex login --with-api-key`, the key read on stdin, stored in that `CODEX_HOME`), but `account-login` does not offer it. | A human has to run it by hand with `CODEX_HOME` set. |
| G4 | **No single-account views.** There is no `show`, `auth-status` or `verify` for one account. | `account-list` shows everything; `health` is per project. |
| G5 | **No command to change the default.** Defaults live in `.ai/orchestrator.yaml`, which only a human edits. | Working as designed; only the UX is missing. |
| G6 | **Refresh and revoke.** setup-token cannot be refreshed: it is valid for a year and a new one means a new `setup-token` run. Codex refreshes its own ChatGPT token. Claude OAuth refresh is internal to the CLI. Logout is `claude auth logout` / `codex logout` inside the home. | Nothing for the bridge to build. It should state "provider-managed" per method instead of offering a fake `refresh`. |

The spec's own "Principles" rule out these:
- An OAuth flow of our own.
- A second settings tree (`.agent-bridge/settings/...`): `.ai/orchestrator.yaml` and the registry already split project metadata from machine-wide accounts.
- A verb-noun command tree that duplicates the existing hyphenated `account-*` commands. The spec allows following the existing convention.

## 3. Proposed design (smallest change)

### 3.1 Credential references (G1, G2)

Status: implemented on this branch for the `oauth_token` kind (group 1). `token_env` stays as it is
(backward compatible). A second, mutually exclusive field picks the Keychain:

```yaml
accounts:
  printless:
    provider: anthropic
    home: /Users/me/.agent-bridge/homes/printless
    credential: keychain        # cannot be combined with token_env
```

- **Storage key.** `keychain` means a macOS Keychain generic password with service `agent-bridge` and account `<provider>:<account key>`. The account key is the auth home, so the same account in two projects shares one entry. Nothing is copied between projects; an account must be added in each project that uses it.
- **Reading.** `security find-generic-password -s agent-bridge -a <key> -w`, run with the user's real `HOME`. For google accounts `account_env` replaces `HOME`, which already causes "Keychain Not Found".
- **Writing.** A new human-only `agent-bridge account-set-token <name>` runs `security add-generic-password -U -s agent-bridge -a <key> -w`. Verified with `security help add-generic-password`: "Specify -w as the last option to be prompted". `security` prompts on the TTY itself, so the secret never passes through the bridge, its argv or its logs. The command refuses to run without a TTY, like the other human-only commands.
- **Deleting.** `account-remove` also deletes the Keychain item (`security delete-generic-password`).
- **Where the value goes.** It reaches only that account's agent process, under the variable named by `credential_kind`. Validation is the same as today: `sk-ant-oat` for `oauth_token`, `sk-ant-api` for `api_key`. A missing or invalid item makes the account read as logged out, which is G4's `verify`.
- **Who can read the item (unverified).** An item `security add-generic-password` creates without
  `-T` trusts `/usr/bin/security`, so any process running as the user can likely read it without a
  prompt. Not probed: a throwaway keychain would change the user's keychain search list. So:
  - Verification commands cannot reach the Keychain under Seatbelt. Probed: with only the
    `/usr/bin/security` exec denied, a `ctypes` call into Security.framework still reached it. So
    the profile also denies `mach-lookup` of `com.apple.SecurityServer` / `securityd` and reads of
    `~/Library/Keychains`. `git`, Python `ssl`, `node`, `uv` and `curl` behaved the same with and
    without it.
  - The docs say the Keychain keeps the token off disk and out of git, not away from
    unsandboxed same-user code (a Claude agent with `allow_bash`, or Codex until a probe says otherwise).
- **Linux.** `secret-tool` (libsecret) if present. Otherwise only the `env:` form; no home-made encrypted file. "Encrypted local storage" would need a key stored somewhere, which only moves the problem.
- **To verify:** how `claude -p` chooses between `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` and a login in `CLAUDE_CONFIG_DIR`. The audit (D1) says the API key always wins in `-p`. Inject only the one variable the account names, and probe it with a real CLI before claiming it.

### 3.2 Codex API key (G3)

`account-login <name> --with-api-key` reads the key with `getpass` and pipes it to `codex login --with-api-key` on stdin, with that account's `CODEX_HOME`. The key ends up in that home's `auth.json`, exactly where Codex keeps its own logins. The bridge does not store it. Human only.

### 3.3 Single-account views (G4, G5)

- `account-show <name>`: provider, home, credential reference (never the value), login state, quota, limits, which agents use it, and whether it is a role default.
- `account-verify <name>`: the existing `login_status`, plus "credential reference present and well formed".
- The default stays in `roles.<role>.default`. The views print it; changing it stays a human edit of `.ai/orchestrator.yaml` (a CLI that writes `.ai/` would be a new human-only path, not worth it now).

### 3.4 Not built

- OAuth of our own.
- Refresh and revoke commands. The views state per method who refreshes: the provider CLI, or nobody for a year-long setup-token.

## 4. Tests (Phase 4 mapping)

- **Keychain isolation.** Every Keychain test uses an injected store; none touches the real Keychain, and CI is Linux.
- **Cases 1, 2, 9.** Two projects on two accounts, two accounts of one provider, and concurrent runs: the existing `test_parallel` and `test_conflicts`, plus a per-account credential case.
- **Cases 3, 4.** Default switch and per-task override: existing routing tests.
- **Cases 5, 6, 8.** Invalid key, expired or revoked token, missing reference: logged out, PAUSED AUTH, no fallback.
- **Case 7.** OAuth refresh failure: provider-managed, so it surfaces as AUTH.
- **Case 10.** Missing or broken settings: `config-check` errors (CFG-04, CFG-06 already on the audit branch).
- **Case 11.** Existing CLI login keeps working: accounts without `home`.
- **Case 12.** No leaks. The value never appears in:
  - argv (checked with a fake `security` that records its argv);
  - the stored output or events;
  - git, since there is no file at all.

## 5. Open questions for the user

1. Is Keychain plus the `env:` fallback enough, or is Linux `secret-tool` needed now?
2. Should the new human-only commands (`account-set-token`, `account-login --with-api-key`) exist? Or should these stay documented manual steps?
