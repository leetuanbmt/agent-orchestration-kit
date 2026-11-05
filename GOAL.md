# Goal: fix the audit findings

This file holds two independent tracks. "Work on GOAL.md" means the audit track below.
"Work on the architecture refactor in GOAL.md" means the last section, "Track B: architecture
refactor", which has its own rules and ignores the audit procedure.

Implement the remediation plan in `docs/audits/implementation-plan.md`, one PR per session,
until every finding in `docs/audits/security-audit.md` and `docs/audits/reliability-audit.md` is
fixed or deliberately deferred. When asked to "work on GOAL.md", do exactly one step: the first
unchecked item in the progress list below, then stop.

## Progress

Update the checkbox and the note when a step finishes (branch, date, final status). Do not
reorder; PR-1, PR-2, PR-4, PR-10 and PR-12 are independent of each other, the rest follow the
dependencies in the plan.

- [x] PR-1 Review decisions out of the audit log (REL-03, REL-06) — done in
  `tuanvm/audit-pr-1-review-record` (branched from the former `develop`; every new PR branches
  from and targets `main`), PR #42, 2026-11-04,
  VERIFIED (T0053-T0056 DONE). Deferred review follow-ups: total event-detail size cap, private
  key split across list items, `task_result` without per-attempt review, legacy fallback event
  limits. Push as leetuanbmt over HTTPS.
- [x] PR-2 Close the evidence window (SEC-01 fixes 1-2, SEC-04) — done in `claude/charming-heisenberg-n2y2f6` (3101afd), 2026-11-05, VERIFIED (KIT-SEC-EVID-001..005, KIT-UNIT-047; test_sec01/test_sec04 now fail).
- [x] PR-3 Fail-closed evidence (REL-01) — done in `claude/charming-heisenberg-n2y2f6` (09d6574), 2026-11-05, VERIFIED (KIT-UNIT-048, KIT-REL-EVID-001/002; test_rel01 still passes because it stubs review._git, see the QA report).
- [x] PR-4 Contain protected-path writes (SEC-02) — done in `claude/charming-heisenberg-n2y2f6` (98ff857), 2026-11-05, VERIFIED (KIT-SEC-PROT-001/002; test_sec02 now fails); agent instruction files sensitive through a constant, since .ai/config.example.json must not change.
- [x] PR-5 Patch truncation and evidence schema (SEC-06) — done in `claude/charming-heisenberg-n2y2f6` (c49f21c), 2026-11-05, VERIFIED (KIT-UNIT-049; test_sec06 now fails).
- [x] PR-6 Bind approval to verified content (SEC-05) — done in `claude/charming-heisenberg-n2y2f6` (1e4ccb8), 2026-11-05, VERIFIED (KIT-SEC-BIND-001..004; test_sec05 now fails); bound to file content, not HEAD.
- [x] PR-7 Ignored-file evidence (SEC-03), measurement first — done in `claude/charming-heisenberg-n2y2f6` (2428763), 2026-11-05, VERIFIED (KIT-SEC-IGN-001..003, KIT-UNIT-050; test_sec03 now fails); measured 80 ms here, about 220 ms for 100 000 ignored files; enforced without a review stage instead of an ignored_scope switch.
- [x] PR-8 Recoverable verification (REL-02 recovery half; the cancel half landed in `950113b`) — done in `claude/charming-heisenberg-n2y2f6` (8cc5f9b), 2026-11-05, VERIFIED (KIT-REL-RECOVER-001).
- [x] PR-10 Input hardening (SEC-07, SEC-09, SEC-10) — done in `claude/charming-heisenberg-n2y2f6` (9a4aa5b), 2026-11-05, VERIFIED (KIT-UNIT-054; test_sec07 now fails).
- [x] PR-11 Trust-model documentation (SEC-08) — done in `claude/charming-heisenberg-n2y2f6` (89e8341), 2026-11-05, VERIFIED (KIT-UNIT-055; README, README.vi, REPORT and the security rules match the code).
- [x] PR-12 CI, approval before creating the workflow file — done in `claude/charming-heisenberg-n2y2f6` (7fad951), 2026-11-05, IMPLEMENTED: .github/workflows/ci.yml awaits a human review.
- [x] PR-9 Sandboxed verification (SEC-01 fix 3), probe first — done in `claude/charming-heisenberg-n2y2f6` (9cc0b3a), 2026-11-05, VERIFIED with Landlock instead of the Codex probe, which needs a Codex CLI not available here (KIT-SEC-SBX-001..004, KIT-UNIT-052/053).
- [x] PR-13 Recovery details (REL-04, REL-05, REL-07) — done in `claude/charming-heisenberg-n2y2f6` (fd2f52c), 2026-11-05, VERIFIED (KIT-REL-REC-001..003).
- [x] Final check — 2026-11-05: every finding fixed or (SEC-08) mitigated and documented; 10 of 11 reproductions fail; `scripts/check.sh --e2e` 635 passed, 0 failed; proposed readiness table in `docs/qa/e2e-qa-report.md`. Also fixed: QA-BUG-007 (`06d1c4c`), QA-BUG-008 (`a590f07`), QA-BUG-009 (`3efeeb1`).

## Inputs

Read only what the current step needs:

- First the "Revalidation" section at the top of `docs/audits/implementation-plan.md`. It
  records which findings are fixed, open or changed at `643ec2f` and where code has moved since
  the audit. It overrides the PR sections where they disagree.
- `docs/audits/implementation-plan.md`: the PR section (objective, design, tests, acceptance,
  compatibility, rollback) and its rows in the regression test matrix.
- The findings it fixes in `docs/audits/security-audit.md` or
  `docs/audits/reliability-audit.md` (location, root cause, fix).
- `docs/audits/evidence/test_audit_repro.py`: the reproduction test for each finding.

The audit was written at revision `3571546`. Treat it as a hypothesis. Before changing anything,
re-check each finding against the current code (line numbers may have drifted) and run its
reproduction:

```bash
PYTHONPATH=. .venv/bin/python -m pytest docs/audits/evidence/test_audit_repro.py -k <id> \
  -p tests.conftest --rootdir=. -o addopts="" --basetemp=<scratchpad dir> -q
```

A pass means the defect still exists. If a finding no longer reproduces, or the planned design no
longer fits the code, stop and report instead of improvising. Exception: the Revalidation section
already records `test_rel02` as failing (the cancel half is fixed). PR-8 has no reproduction, and
its acceptance is the new recover test.

## Procedure for one PR

1. Create branch `tuanvm/audit-pr-<n>-<short-slug>` from `main` before any change. Keep
   `docs/audits/` and this file available in the working tree.
2. Follow `CLAUDE.md` and `.claude/rules/`: the executor writes the code through the agent-bridge
   MCP (`bridge_create_task` → `bridge_execute_task` → `bridge_task_status(wait_s=55)` →
   `bridge_task_result` → `bridge_review_task`). These PRs change quality-gate code, so every task
   uses `risk_level: "high"`, the narrowest `allowed_paths` for the modules the plan names plus
   the new test files under `tests/`, and `verification_commands` that run the new tests and the
   full suite (`.venv/bin/python -m pytest -q`). Split a change over ~400 lines into several tasks.
3. Tests: turn each reproduction for this PR into a regression test under `tests/` that asserts
   the fixed behaviour, plus the other tests the plan lists. Never weaken, skip or delete existing
   tests; when an existing test asserted the old behaviour (for example `REWORK` instead of
   `BLOCKED`), change only that assertion and say why.
4. Review: read every hunk of `review.py`, gate and approval code in `service.py`, `state.py` and
   `executor.py` before approving. Approve only with evidence from `bridge_task_result`.
5. The task stops at `APPROVED`. Give the user the exact `agent-bridge approve` command and wait;
   never run it.
6. After human approval, run `scripts/check.sh` and this PR's reproductions again. The
   reproductions must now fail (defect gone). Report both outputs.
7. Do not commit, push or open a PR until the user says so. Then: English commit messages, no AI
   attribution trailers (`commit-rules` skill).
8. Update the progress list above. In `docs/audits/` change nothing except one line under the PR
   heading in `implementation-plan.md`: `Status: done in <branch>, <date>`.

## Step-specific rules

- **PR-7**: before implementing, measure the cost of `git ls-files --others --ignored
  --exclude-standard -z` plus `os.stat` on this repository and on a repository with a large
  `node_modules`/`.venv` that the user names. Report the timings and wait for a go. The default
  mode stays `"report"`.
- **PR-9**: probe only, no production code. On the installed `codex-cli`, determine whether Codex
  can run an arbitrary argv under its workspace-write sandbox (`codex sandbox macos …` or the
  version's equivalent) with network off, `/tmp` excluded and the exit code preserved, and whether
  `pytest`, `uv run pytest` and `npm test` work inside it. Use a throwaway git repo in the
  scratchpad and no real credentials. Propose a `docs/REPORT.md` section, show it to the user, and
  stop with a go/no-go.
- **PR-12**: a workflow under `.github/workflows/` is externally visible once pushed and touches a
  sensitive path. Show the workflow file and wait for approval before creating the task.
- **Final check** (read-only, no code changes, no commits): for every finding report fixed (PR and
  regression test name), deliberately deferred (with the plan's reason) or still open. Run
  `scripts/check.sh` and the whole reproduction file; every reproduction of a fixed finding must
  fail, list any that still pass. Re-evaluate the "Production readiness" table in
  `docs/audits/executive-summary.md` against the current code and propose an updated table
  without editing it.

## Rules for every step

- One PR per session; one writer per worktree. Parallel work needs separate worktrees.
- Track B rewrites the same code in `bridge/service.py` that PR-2 to PR-8 and PR-13 change. Do
  not start one of those PRs while a Track B PR is open; after Track B merges, find the moved
  methods in `bridge/runner.py` and `bridge/gate.py`.
- Never run human-only commands (`agent-bridge approve`, `reassign`, `account-*`), never edit
  `.ai/`, never touch account homes or global configuration (`CLAUDE.md` hard rules).
- Keep everything local: do not publish reports or pages to claude.ai or any other service.
- Respond to the user in Vietnamese; code, commits and files in English.

## Report at the end of each step

Files changed, tests added, `check.sh` result, reproduction result, any deviation from the plan
and why, the next unchecked item, and one final status: VERIFIED, IMPLEMENTED, BLOCKED or
INCOMPLETE.

# Track B: architecture refactor (P1 + P2)

Follow-up to PR #48 (`tuanvm/arch-p0-store-boundary`, which moved the task rebind SQL into
`Store.rebind` and the run gate into `models.run_failure_state` / `models.verdict`). Done means one
PR into `main` is open, its whole diff has been re-reviewed and every problem found is fixed, and
`scripts/check.sh` passes.

## Progress

- [x] B1 Pure account-state reads — `tuanvm/arch-p1-p2-refactor`, 2026-11-05. `_account_state`
  keeps its name and lifting behaviour (KIT-FAILOVER-009 and KIT-HARDEN-018 call it and expect
  the `account_cooldown_elapsed` event); listings use the non-writing view.
- [x] B2 `AccountGate` and `TaskRunner` behind the `Bridge` facade — same branch. `_account_quota`
  stays on `Bridge` (tests replace it there and expect the gate to see it).
- [x] B3 Project registry module, no import cycles around `storage` — same branch. `storage` now
  imports only `install`, `osutil`, `registry` and `models`; `find_root` moved to `registry` too
  (re-exported from `config`). With the user's approval, `test_statuses` and
  `test_prune_with_remember` now patch `registry` instead of `accounts`/`progress`.
- [ ] B4 Optional: split `Store` into task, account and usage parts — skipped: the PR diff is
  already about 1,550 changed lines, over the 600-line limit.

## Mode and branch

- Edit the code directly in this session; do NOT delegate through agent-bridge. The user
  authorised this, and it overrides hard rule 1 in `CLAUDE.md`. Every other rule in `CLAUDE.md`
  and `.claude/rules/` still applies.
- If PR #48 is merged, branch from the latest `main`. If not, branch from
  `tuanvm/arch-p0-store-boundary` and say in the PR body that this PR depends on #48. Branch name:
  `tuanvm/arch-p1-p2-refactor`. Each step is its own commit; open one PR against `main`.

## No AI references (mandatory)

Nothing that reaches the repository or GitHub may mention AI, an assistant or model, generated
code or the tools used to write it. That covers code comments, docstrings, commit messages, PR
title, body and comments, review comments, CHANGELOG and docs. No `Co-Authored-By` or
"Generated with" trailers. Write as an engineer would: what changed and why. Before every push,
read the added lines of `git diff <base>...HEAD` and the commit messages for such references and
remove them. The existing `claude-cli` adapter, `.claude/` and `CLAUDE.md` are product code and
stay as they are.

## Constraints

- No behaviour change. Keep the signatures of `Bridge` public methods, the MCP tools
  (`bridge/server.py`), the CLI, `.ai/orchestrator.yaml` config, the DB schema and event names.
- Never edit `.ai/`, `.mcp.json` or `.claude/settings*.json`. Never weaken, skip or delete a test.
  If a test depends on behaviour a step must change, stop and report instead of editing the test.
- English commits and PR text in the style of `git log` (short lowercase subject, no emoji, no
  markdown in the body).
- No speculative abstractions: no interface or Protocol with a single implementation, no new
  config.

## Steps

**B1 Pure account-state reads.** `Bridge._account_state` (`bridge/service.py`, around line 185)
calls `store.set_account_state` and logs `account_cooldown_elapsed` from a read path that
`health`, `list_accounts`, `_unavailable`, `_start` and `_quota_fallback` all use. Split it into
(a) a pure function that computes the effective state (a quota mark whose cooldown elapsed or
whose window reset counts as available) and (b) `refresh_account_marks(name)`, the only place
that writes the lifted mark. Call (b) only where work starts (`_start`, routing through
`_pick`/`_unavailable`, `_quota_fallback`). `health` and `list_accounts` output must not change.
First grep `tests/` for `account_cooldown_elapsed` and `set_account_state`. Add a unit test for
(a) with the `@pt.*` metadata used in `tests/unit/test_core.py`, numbered after the highest
`KIT-UNIT-*` id in use.

**B2 Facade split.** Move into `bridge/gate.py` a class `AccountGate` holding `_account_quota`,
`_account_state`, `_remaining`, `_limits`, `_usage_key`, `_period_starts`, `_rate_limits`,
`_evaluate`, `_limit_report`, `_reserve`, `_settle_open`, `_record_account`, `_unavailable`,
`_unscopable`, `_pick`, `_executors_on`, `_quota_fallback`, `_route`, `_unroute` and the
`_reports` cache. Move into `bridge/runner.py` a class `TaskRunner` holding `_start`, `_run`,
`_run_inner`, `_park_if_stopping` and `_review_stage`. `Bridge.__init__` builds both and
delegates. Before moving, grep `tests/` for monkeypatching, direct calls and `@pt.target` of
these private methods. Keep thin delegates on `Bridge` for every one the tests use, so the tests
stay unchanged.

**B3 Registry module.** `bridge/storage.py` lazily imports `config` (around line 133),
`accounts` and `progress` (around lines 218 and 368), and `_prune` uses the private
`accounts._projects_lock` and rewrites `projects.json` itself. Move the project registry
(`known_projects`, `remember_project`, `_projects_lock`, the `projects.json` write) into
`bridge/registry.py`, which `accounts` and `storage` both import. Keep the names re-exported from
`accounts` so existing imports still work. Afterwards `storage` imports neither `accounts` nor
`progress`.

**B4 Optional Store split.** Only if the PR diff is still under about 600 lines: split `Store`
(`bridge/state.py`) into task, account and usage-ledger parts that share the same connections,
lock and DB files, keeping every public `Store` method. Otherwise skip it and say so in the PR
body.

## Verification

`uv sync`, then `scripts/check.sh` must pass in full after every step (baseline on `main` at
`643ec2f`: 504 passed, 5 skipped, including `KIT-UNIT-012..014`). Run `uvx ruff check --select F` on the changed
files. For B3, confirm with `python -c "import bridge.storage"` and grep that the cycles are gone.

## Review and report

After opening the PR, re-read the whole diff against the base branch. Compare every moved branch
of logic with the original (`git show <base>:bridge/service.py`): exceptions and messages, the
order of lock release and reservation settlement, event details. Fix everything found and push.
Post one short PR comment with the review result, following the no-AI rule. Tick the progress
boxes above in the same PR. Finally report the PR link, the commits, the test results, any
skipped step and why, and one final status: VERIFIED, IMPLEMENTED, BLOCKED or INCOMPLETE.
