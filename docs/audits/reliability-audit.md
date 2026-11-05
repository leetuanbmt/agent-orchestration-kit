# Reliability and correctness audit

Audited revision: `3571546`, 2026-11-04. Reproductions: [`evidence/test_audit_repro.py`](evidence/test_audit_repro.py).
Confidence levels as in [security-audit.md](security-audit.md). Current status of each finding
(REL-02 half fixed, REL-03 and REL-06 fixed): "Revalidation" in
[implementation-plan.md](implementation-plan.md).

## Summary

| ID | Title | Severity | Confidence | Priority |
|---|---|---|---|---|
| REL-03 | Redaction of serialized event JSON strands tasks in `APPROVED` | High | Confirmed | P1 |
| REL-01 | Git failures are swallowed; evidence silently becomes empty (fail-open) | Medium | Confirmed (simulated) | P1 |
| REL-02 | Cancel and shutdown do not stop verification; its processes are untracked | Medium | Confirmed | P2 |
| REL-06 | Audit events are written before, and outside, the action they record | Low | Confirmed (code) | P2 |
| REL-07 | A read-only task may run beside a writer and inherit its changes as violations | Low | Confirmed (code) | P3 |
| REL-04 | Task recovery trusts a bare pid (no start time), unlike workflow recovery | Low | Confirmed (code) | P3 |
| REL-05 | A failed rescue transition leaves a task active in a live server | Low | Hypothesis | P3 |

SEC-04 (processes left behind by the agent) and SEC-05 (approval not bound to content) are also
lifecycle defects; they are described in the security audit.

---

## REL-03: Redaction of serialized event JSON strands tasks in `APPROVED`

- **Severity** High (ordinary input, unrecoverable without cancelling) · **Confidence** Confirmed.
- **Location** `bridge/state.py:180` (`redact(json.dumps(detail), 8000)`), `bridge/state.py:86-96`
  (`_row` keeps the raw string when JSON parsing fails), `bridge/service.py:840-843`
  (`(e["detail"] or {}).get(...)`), also `:545` (`resume_task` reads review notes the same way).
- **Root cause** Two independent ways to corrupt the stored JSON:
  1. The generic `key=value` secret regex (`bridge/security.py:117-120`) matches `password=hunter2"`
     up to the next comma and consumes the closing JSON quote.
  2. A detail longer than 8000 characters is cut to its tail with a `…` prefix. Review notes may
     be 8000 characters (`ReviewDecision.notes`), so a long review always overflows.

  The corrupted detail is returned as a string, and gate code calls `.get` on it.
- **Impact** Reproduced with notes `"ok, verified. db password=hunter2"` and with 7990 `x`s:
  `bridge_review_task(approve)` commits `REVIEW → APPROVED`, then `_close → _assert_gates` raises
  `AttributeError` (not converted by `server._safe`, so the client sees a generic error). The task
  sits in `APPROVED` with `needs_human = 0`; `agent-bridge approve` raises the same error. Only
  cancelling ends it. Any review that quotes a config line (`token=…`, `api_key: …`) can trigger
  it.
- **Reproduction** `test_rel03_review_notes_break_gate[...]` (both parameter cases).
- **Fix**
  1. Redact string leaves before serialization (walk dicts/lists) and truncate individual long
     strings, never the serialized document.
  2. Gate decisions must not depend on parsing the audit log: record the approving review on the
     attempt row (a `review` column, or a `reviews` table) inside the same transaction as the
     `REVIEW → APPROVED` move, and have `_assert_gates` read that.
  3. Readers that still consume event details (`resume_task`, `task_result`, `progress.py`)
     tolerate a non-dict detail.
- **Regression tests** Approve with notes containing `password=x"`-shaped text and with 8000
  characters → `DONE`; `resume_task` after a long rework note picks the notes up.

## REL-01: Git failures are swallowed (fail-open evidence)

- **Severity** Medium · **Confidence** Confirmed by simulation (`git status` made to fail);
  real-world triggers not reproduced.
- **Location** `bridge/review.py:22-25` (`_git` returns `""` on any non-zero exit),
  `:51-64`, `:67-84`, `:87-105`.
- **Root cause** Every git call degrades to an empty string. An empty `git status` means "no
  dirty files", an empty `write-tree` means "no patch".
- **Impact** If `git status` fails after the run (corrupt index, `safe.directory` change, a
  filesystem error, a git upgrade mid-session), the attempt reports no changed files and no
  violations and can pass all gates. A total git failure is partly caught today only because
  `rev-parse HEAD` also returns empty and `head_changed` becomes true by accident.
- **Reproduction** `test_rel01_git_status_failure_yields_empty_evidence`.
- **Fix** `_git` raises on failure inside `snapshot`/`diff`; `_run_inner` turns an evidence error
  into `PAUSED` with failure `EVIDENCE_ERROR: …` (environment class, no budget consumed), never
  `VERIFYING`.
- **Regression tests** Failing `status` or `write-tree` → `PAUSED`/`EVIDENCE_ERROR`, no
  `REVIEW`.

## REL-02: Cancel and shutdown do not stop verification

- **Severity** Medium · **Confidence** Confirmed.
- **Location** `bridge/review.py:141-166` (no cancellation hook, pid not recorded),
  `bridge/service.py:860-869` (`cancel_task` waits at most 15 s), `:1050-1054` (`shutdown`
  awaits every run), `:1022-1048` (`recover` kills only `codex_pid`).
- **Root cause** `run_verification` only knows a timeout (`verify_timeout_s`, default 900 s per
  command). Its process group id is never stored.
- **Impact** Reproduced: a task cancelled during `VERIFYING` still ran its command to completion
  and wrote a file afterwards. With a long test suite, `cancel_task` returns after 15 s while
  verification keeps changing the worktree; MCP shutdown may wait up to 900 s per command; a
  crashed runner leaves the verification group orphaned and `recover` cannot find it.
- **Reproduction** `test_rel02_cancel_does_not_stop_verification`.
- **Fix** Pass `should_cancel` into `run_verification`, poll it like the agent watchdog, kill
  the group on cancel/shutdown; store the running verification pid and start time on the task
  row (reuse `codex_pid`/`codex_started`, which already mean "the process group to kill") so
  `recover` kills it too.
- **Regression tests** Cancel during a 30 s verification → command killed within ~2 s, state
  `CANCELLED`, no late write; runner killed during verification → next start kills the group.

## REL-06: Audit events written outside the action they record

- **Severity** Low · **Confidence** Confirmed by code.
- **Location** `bridge/service.py:810` (`review` event logged before `_assert_gates` and the
  transition, separate transaction), `:855` (`human_approval` logged before `_close`).
- **Impact** A rejected approval still leaves an `approve`/`human_approval` event; the audit log
  overstates what happened, and because `_assert_gates` reads those events, a stray `approve`
  event counts toward a later gate.
- **Fix** Pass the review detail into the transition (it is already the transition's `detail`)
  and drop the separate pre-logging, or log inside the same `tx()`. Folded into REL-03 fix 2.
- **Regression tests** Rejected approval leaves no `review: approve` event.

## REL-07: Read-only task beside a writer

- **Severity** Low · **Confidence** Confirmed by code, not executed.
- **Location** `bridge/state.py:374-380` (only writer+writer is refused).
- **Impact** A read-only task's diff covers the whole worktree, so a concurrent writer's changes
  appear as its scope violations (spurious `REWORK`). The writer's own diff is unaffected.
- **Fix** Refuse any second active task in a worktree (the README already says one runner per
  worktree), or skip scope evaluation for read-only Codex runs. The first is simpler and matches
  the documented model.

## REL-04: Task recovery trusts a bare pid

- **Severity** Low · **Confidence** Confirmed by code.
- **Location** `bridge/service.py:1028` (tasks) vs `:1040-1042` (workflows check
  `runner_started`).
- **Impact** If the runner died and its pid was reused by an unrelated live process, the task is
  treated as owned and stays `RUNNING` until that process exits.
- **Fix** Add `runner_started` to `tasks` (the schema already migrates columns in
  `Store.__init__`) and compare it the way workflows do.

## REL-05: Failed rescue transition leaves a task active

- **Severity** Low · **Confidence** Hypothesis.
- **Location** `bridge/service.py:613-621`: the rescue transition is wrapped in
  `contextlib.suppress(Exception)`.
- **Scenario** The run fails because SQLite stayed busy past the 10 s timeout; the rescue
  transition fails the same way and is suppressed; the task stays `RUNNING` with a live
  `runner_pid` (the MCP server), so `recover` skips it until the server restarts.
- **Fix** Retry the rescue transition with backoff; if it still fails, record the failure in the
  attempt row and let `recover` treat "runner alive but `_runs` has no entry" as interrupted when
  the runner is this process.

---

## State machine and concurrency review

Checked and sound:

- Every task transition goes through `Store.transition`, one `BEGIN IMMEDIATE` transaction that
  re-reads the row, validates `(src, dst, actor)` against `models.TRANSITIONS`, enforces the
  single-writer and per-account limits, writes the event and promotes dependents. CLI and MCP
  server share the database under WAL safely.
- Account slots are enforced twice: in the database per worktree and by `AccountLock`
  (`flock` on per-slot files) across worktrees and processes. The lock is held only while an
  agent process runs and released in `finally` (`bridge/service.py:622-623`).
- Environment failures (`quota`, `auth`, `network`, `missing_binary`) give the attempt back
  (`attempts=used - 1`); code failures consume it; `_start` blocks a task whose budget is spent.
- Session checkpoints are written as soon as the CLI reports a session id, so an interrupted
  run resumes the same session.
- Workflow driving is sequential and bounded by task budgets; errors from one task are recorded
  in `waiting` and do not abort the workflow.
- Task ids use `COUNT(*) + 1` inside the creating transaction; rows are never deleted, so ids do
  not collide.

## Test coverage

Baseline on this revision: `.venv/bin/python -m pytest -q` → **359 passed, 5 skipped** in 173 s
(the 5 skips are the opt-in real-CLI smoke tests, `CODEX_SMOKE`, `CLAUDE_SMOKE` and the other
adapters' flags). There is no CI configuration in the repository (`.github/` absent);
`scripts/check.sh` is the only gate.

Covered well: transitions and actors, routing and quota fallback, account locks and concurrency,
recovery after a killed runner, storage migration, adapters' argv and event parsing, the web
dashboard's host/token checks, path validation.

Not covered (each maps to a finding above):

| Gap | Finding |
|---|---|
| Verification changing out-of-scope or protected files | SEC-01 |
| Protected-file violation outcome (`REWORK` vs `BLOCKED`) and persistence | SEC-02 |
| Writes under ignored directories | SEC-03 |
| Processes left behind after a normal exit | SEC-04 |
| Worktree change between verification and approval | SEC-05 |
| Patch truncation behaviour | SEC-06 |
| Git command failure during snapshot | REL-01 |
| Cancel/shutdown during verification | REL-02 |
| Review notes with secret-shaped text or maximum length | REL-03 |
| pid reuse during recovery | REL-04 |

Not executed in this audit: real-CLI smokes (need accounts), Windows, SQLite lock contention
beyond the existing concurrency tests, disk-full behaviour.
