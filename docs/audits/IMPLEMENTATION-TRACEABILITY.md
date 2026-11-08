# Audit implementation traceability

Maps the findings of the 2026-11-08 audit (`MASTER-AUDIT-REPORT.md` and the phase reports, left
unchanged) to the changes on branch `feat/audit-driven-improvements`. Each fix was written by the
executor agent through agent-bridge, verified by the bridge (full test suite) and reviewed before
commit.

## Fixed

| Finding | Severity | Root cause | Fix | Commit | Tests |
|---|---|---|---|---|---|
| SEC-02 | High | Web actions ran `python -m bridge` with the project as cwd, so a `bridge/` package in the worktree shadowed the kit | Run with `python -P`, which keeps the cwd off `sys.path` (`bridge/webactions.py`) | 9eb9276 | `tests/integration/test_webactions.py::test_worktree_bridge_package_is_not_executed` |
| SEC-01 (approval forgery part) | High | Verification commands did not carry `AGENT_BRIDGE_AGENT`, so code they ran could start a `Bridge` and approve as a human | Verification env sets `AGENT_BRIDGE_AGENT=1` (`bridge/review.py`) | 25ee14d | `tests/integration/test_verification_env.py` |
| SEC-09 | Medium | `redact` missed quoted JSON credentials, URL passwords and several token prefixes | New patterns in `bridge/security.py` | 25ee14d | `tests/unit/test_core.py` |
| SEC-10 | Medium | Env scrubbing let `SECRET_KEY`, `*_KEY`, `*_PAT`, `NETRC`, `*_AUTH` through | Added to `ALWAYS_DENY_ENV` | 25ee14d | `tests/unit/test_core.py` |
| FUN-01 | Medium | `recover()` charged an attempt for a dead runner, contrary to "PAUSED does not consume the budget" | Refund the attempt when the task becomes PAUSED (`bridge/service.py`) | 4f762ce | `tests/integration/test_bridge.py::test_recovery_refunds_attempt_and_resumes` |
| REL-02 | Medium | `kill_stale` sent SIGTERM only; an agent ignoring it kept running | Escalate to SIGKILL after a grace period (`bridge/executor.py`) | 4f762ce | `tests/unit/test_core.py::test_kill_stale_escalates_to_sigkill` |
| SEC-07 (HEAD part) | Medium | `verified_head` was stored but never compared at approval | Approval refuses when HEAD moved or cannot be read (`bridge/service.py`) | fccdb4a | `tests/integration/test_approval_binding.py` |
| CFG-01 | High | `protected_paths` and `env_deny` in `.ai/config.json` replaced the defaults entirely | Built-in entries are a floor; project values are added to them (`bridge/config.py`) | 0cfc630 | `tests/unit/test_config_floor.py`, `tests/integration/test_protected_paths.py` |
| SEC-04 (config part) | Medium | Same root cause as CFG-01 | Same fix | 0cfc630 | same |
| FUN-02 | Medium | A cancelled or blocked dependency left dependents in BACKLOG and the workflow in WAITING | Workflow finishes INCOMPLETE with `detail.unreachable` | d5718dc | `tests/integration/test_orchestration.py::test_unreachable_dependencies` |
| CFG-08 | Medium | Tests inherited `AGENT_BRIDGE_HOME` from the shell | `tests/conftest.py` unsets it | d5718dc | whole suite |
| UX-01 | High | `syncNode` copies the keydown handler onto the live row, but the handler compared `event.target` with the discarded node | Compare with `event.currentTarget` (`bridge/static/app.js`) | 43311cc, 9dd9a77 | `tests/qa/web/dashboard.cjs` drawer_layout check "Enter on a task row opens the drawer" (fails on the old code) |
| SEC-05 | Medium | Git stores a symlink as a blob, so writes through a link under an allowed path left no trace | Task creation refuses allowed paths covering a link that leaves the project; an attempt that creates or retargets one gets a scope violation, also for links in git-ignored paths; coverage follows each pattern's literal prefix (`bridge/review.py`, `bridge/security.py`, `bridge/service.py`, `bridge/runner.py`) | 7decda2, f685ab3, 814e5c9 | `tests/integration/test_symlink_scope.py` |
| SEC-01 (unconfined part, step 1) | High | Without Landlock, `verify_sandbox: auto` ran checks unconfined while `health` stayed OK | `health` reports the `verify_unconfined` problem (NOT OK) until a human sets `verify_sandbox: none`, which is a warning; `ok` is now derived from `problems` (`bridge/service.py`) | 36407e3 | `tests/integration/test_health_diagnostics.py` |
| AUTH-01 | High | `env_allow` applied to agents and to verification commands alike | Verification commands ignore `env_allow`; agents keep it (`bridge/review.py`) | 36407e3 | `tests/integration/test_verification_env.py::test_verification_ignores_env_allow` |
| UX-03 | Medium | The CLI list, the TUI and "Needs you" silently showed only the newest 200 tasks | CLI and TUI report N of M; the TUI looks up an exact task id and builds Needs you from a per-state query; `health` counts tasks with one query (`bridge/state.py`, `bridge/service.py`, `bridge/cli.py`, `bridge/render.py`, `bridge/tui.py`) | b408f25 | `tests/integration/test_task_list_limits.py` |

## Verification

- Bridge-run full suite after the last code change (814e5c9): 640 passed, 114 skipped.
- `QA_E2E=1 pytest tests/qa` at 36407e3: 104 passed, 2 skipped.
- `QA_E2E=1` web suite after the UX-01 fix: 30 passed, 1 skipped; the new keyboard check fails
  with the pre-fix `app.js` and passes with the fix.
- `docs/audits/evidence/run_audit_repro.sh docs/audits/evidence/test_audit_2026_11_08_security.py`:
  the reproductions for SEC-01 (forgery), SEC-02, SEC-04 (config), SEC-05, SEC-07 (HEAD) and SEC-09/10 now
  fail, meaning the findings are gone; the others still pass (still present, see below).

## Not addressed on this branch

| Finding | Reason |
|---|---|
| SEC-01 (step 2: confinement on macOS) | Needs a `sandbox-exec` profile or another confinement and a probe; step 1 makes the gap visible in `health` |
| SEC-04 (`.ai/orchestrator.yaml` part), SEC-07 (untracked files), SEC-08 | Approval binding beyond HEAD needs a whole-worktree snapshot that ignores other tasks' changes |
| SEC-03, SEC-12 | Same-user human gate is a documented limitation |
| SEC-06, SEC-11, SEC-13 | Lower severity; separate changes |
| AUTH-04 | `*_BASE_URL` is kept on purpose for proxies |
| PERF-01, PERF-02 | Need a benchmark before changing the snapshot cache |
| UX-02, REL-01, BILL-01 | Design decisions (the dashboard never reads account homes) or larger changes |
| Remaining Medium/Low findings | Out of scope for this pass; see `08-improvement-roadmap.md` |
