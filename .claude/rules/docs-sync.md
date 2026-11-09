# Docs stay in sync with code (this repository)

This file is not shipped by `agent-bridge init`; the shipped rules are `orchestration.md`,
`delegation.md` and `security.md`, which other projects receive. Keep those generic.

Every change to user-visible behaviour lands with its docs in the same PR. Map from what changed
to what must be updated:

| Change | Update |
|---|---|
| CLI command, flag, output or `--help` text | `docs/cli-reference.md`; README.md and README.vi.md where they show it |
| Config key, default, file layout, precedence, deprecation | `docs/configuration.md`; `.ai/config.example.json` for `BridgeConfig` keys; `examples/*.yaml` when they show it |
| Security behaviour (sandbox, scope, approval, credentials) | `.claude/rules/security.md`; the README security section; `docs/REPORT.md` if it states the old behaviour |
| States, roles, routing, workflow | `.claude/rules/orchestration.md`; `.claude/skills/agent-orchestration/SKILL.md` |
| Anything a user notices | `CHANGELOG.md` `[Unreleased]`, under its existing heading (one `### Added`, one `### Changed`, …) |

Rules:
- README.md and README.vi.md change together, at the same lines and with the same meaning.
- Before creating a task, and again before a PR, grep the docs (`README*`, `docs/*.md`,
  `CHANGELOG.md`, `.claude/rules`, `.claude/skills`) for every renamed, moved or removed name.
  Docs under `docs/audits/` and `docs/plans/` are dated records; leave them unchanged.
- A claim in the docs states verified behaviour: read the code path or run it before you write
  it. A command shown as run must have been run.
- `tests/unit/test_docs_sync.py` checks the mechanical part: every CLI command is in
  `docs/cli-reference.md`, every `BridgeConfig` key is in `docs/configuration.md` and every
  shipped example is listed there. A failure means docs are missing, so fix the docs and never
  the test.
