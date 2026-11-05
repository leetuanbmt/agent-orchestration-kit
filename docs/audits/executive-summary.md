# Executive summary

Audit of `agent-orchestration-kit` at revision `3571546`, 2026-11-04. Details:
[architecture](architecture-assessment.md) · [security](security-audit.md) ·
[reliability](reliability-audit.md) · [plan](implementation-plan.md) ·
[reproductions](evidence/test_audit_repro.py). Status changed since then; see "Revalidation" in
the plan.

## Overall assessment

A well-built local tool with a clear state machine, a single service layer, conservative
process handling and an honest README. The existing suite passes (359 passed, 5 opt-in smokes
skipped). The weaknesses sit in one place: **the evidence that gates a task does not cover
everything that runs in the worktree, is not contained when it shows a violation, and is not
bound to what is finally approved.** Those gaps let a misled executor agent get around the
scope check and the Codex sandbox it is meant to complement. One ordinary-input bug (REL-03) can
also leave tasks permanently stuck.

Every finding marked Confirmed below was reproduced by an executed test against a throwaway
repository and the project's fake CLIs; 11 of 11 reproductions pass on the audited revision.

## Top confirmed risks

1. **SEC-01 (High, P0).** Verification runs executor-written code (tests, `conftest.py`) with full
   user privileges, after the scope check. Reproduced: verification wrote an out-of-scope file and
   rewrote `.env`, no violation was recorded, and a gates-only workflow closed the task `DONE`.
2. **SEC-02 (High, P1).** A protected-file violation is only a rework; the file stays modified
   and the next bridge start loads it. Reproduced with a tampered `.ai/config.json` that empties
   `protected_paths`.
3. **REL-03 (High, P1).** Review notes containing `password=…` or about 8000 characters corrupt the
   stored event JSON; approval then crashes and the task is stuck in `APPROVED` (also for the
   human command).
4. **SEC-05 (Medium, P1).** Approval checks stored records, not the worktree: a file changed
   after verification is closed `DONE` by `agent-bridge approve`.
5. **SEC-03/SEC-04/SEC-06 (Medium).** Writes under git-ignored directories, writes by processes
   the agent left running, and the first files of a large patch are all absent from review
   evidence.

## Quick wins (small, low-risk, high value)

- Kill the agent's process group after every run, not only on timeout (SEC-04).
- Take a second snapshot after verification and check the union (SEC-01, first layer).
- Protected-path violation → `BLOCKED`, not `REWORK` (SEC-02).
- Redact event details per string before serializing; store review decisions on the attempt
  (REL-03).
- `_git` fails closed (REL-01); patch truncated per file with an omitted-files list (SEC-06).

## Major tradeoffs

- **Sandboxed verification** (PR-9) is the only real fix for executor code running as the user,
  but it depends on an unprobed Codex feature and breaks tests that need network or `/tmp`;
  it is planned opt-in after a probe.
- **Ignored-file scanning** (PR-7) closes SEC-03 but costs time on large `node_modules`/`.venv`
  trees; it starts in report-only mode.
- **Human approval** stays a workflow control, not an access control against same-user code
  (SEC-08). Making it one would need OS-level separation, out of proportion for a local tool;
  the plan documents this instead and closes the bridge-started paths.
- **Architecture**: no rewrite and no new infrastructure. Three structural changes ride with the
  fixes (an `evaluate_attempt` function, an evidence schema, review decisions stored in columns).
  Router/workflow extraction waits for the parallel-worktrees plan.

## Recommended order

PR-1 (REL-03) → PR-2 (evidence window, P0) → PR-3 (fail-closed) → PR-4 (contain protected
writes) → PR-5 (patch) → PR-6 (approval binding) → PR-7 (ignored files) → PR-8 (cancellable
verification) → PR-10/PR-11/PR-12 → PR-9 after its probe → PR-13. PR-1, PR-2, PR-4, PR-10 and
PR-12 have no dependencies on each other.

## Production readiness

| Use | Verdict |
|---|---|
| A developer orchestrating trusted agents on their own repositories, reading diffs before approving | **Go**, with REL-03 fixed soon (it can strand tasks in normal use) |
| Relying on the scope check / sandbox to contain a misled or prompt-injected executor (untrusted repositories, issue text or dependencies as agent input) | **No-go** until PR-2, PR-4 and PR-6 land; PR-7 and PR-9 complete the picture |
| Gates-only workflows (no reviewer) closing tasks automatically | **No-go** until PR-2 and PR-3 land: today SEC-01 lets such a task close itself after unchecked writes |

## Limits of this audit

Not executed: real Codex/Claude/opencode/agy runs, Windows, the proposed Codex sandbox runner,
large-repository performance, disk-full and long SQLite contention. Findings that depend on them
are marked Hypothesis or Likely in the detailed reports.
