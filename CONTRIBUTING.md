# Contributing

Thanks for helping improve agent-orchestration-kit. Bug reports, documentation fixes, tests and
new adapters are all welcome.

## Set up

```bash
git clone https://github.com/leetuanbmt/agent-orchestration-kit.git
cd agent-orchestration-kit
uv sync                     # project-local .venv, Python 3.12+
uv run agent-bridge --help
```

## Run the checks

```bash
scripts/check.sh            # full suite against fake agent CLIs: no logins, no quota
scripts/check.sh --e2e      # also the end-to-end suite (CLI, MCP server, web dashboard in Chromium)
```

The end-to-end browser tests need Node and Playwright; without them they are skipped. CI runs the
same as `.github/workflows/ci.yml`: `scripts/check.sh` on Ubuntu and macOS, and `--e2e` on Ubuntu.

Smoke tests against the real Codex, Claude, opencode and agy CLIs are opt-in and use your quota;
the commands are in the README under *Development and testing*.

## Conventions

- **Branches.** `main` is the only long-lived branch. Branch from the latest `main` and open the
  pull request against `main`.
- **Tests.** New behaviour comes with a test. Each test function carries the comment block used
  across `tests/` (`# @pt.id`, `@pt.item`, `@pt.condition`, `@pt.expected`, `@pt.target`,
  `@pt.method`, `@pt.legacy`). Use the fake CLIs in `tests/fakes/` instead of real agents.
- **Changelog.** Add an entry under *Unreleased* in `CHANGELOG.md`.
- **Docs.** `README.md` is canonical; keep `README.vi.md` in the same sections and order. Do not
  document behaviour that does not exist yet; mark plans as plans.
- **Security-sensitive code.** Changes to path checks, verification, approvals, account handling
  or process control need a test that fails without the change. Never weaken a check to make a
  test pass.
- **No credentials.** Never commit tokens or auth files, and do not paste `agent-bridge health`
  output from a real machine into issues: it lists your account names.

## Working on this repository with Claude Code

The repository uses its own bridge: `CLAUDE.md` and `.claude/rules/` tell a Claude Code session
to delegate code changes to the configured executor through `agent-bridge` and to review the
evidence before approving. If you use Claude Code here, that workflow applies to you too.

## Reporting bugs

Open a [GitHub issue](https://github.com/leetuanbmt/agent-orchestration-kit/issues) with what you
ran, what you expected and what happened, plus your OS and the versions from `agent-bridge health`
(the `adapters` section). Remove account names, paths and anything token-like first.
