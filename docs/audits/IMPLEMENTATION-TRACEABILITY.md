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

## Verification

- Bridge-run full suite after the last Python change (d5718dc): 620 passed, 114 skipped.
- `QA_E2E=1` web suite after the UX-01 fix: 30 passed, 1 skipped; the new keyboard check fails
  with the pre-fix `app.js` and passes with the fix.
- `docs/audits/evidence/run_audit_repro.sh docs/audits/evidence/test_audit_2026_11_08_security.py`:
  the reproductions for SEC-01 (forgery), SEC-02, SEC-04 (config), SEC-07 (HEAD) and SEC-09/10 now
  fail, meaning the findings are gone; the others still pass (still present, see below).

## Not addressed on this branch

| Finding | Reason |
|---|---|
| SEC-01 (unsandboxed verification on macOS) | Needs a macOS sandbox design and a probe; the marker fix removes the forgery path only |
| AUTH-01 | Making `env_allow` per agent changes the config API; needs a decision |
| SEC-04 (`.ai/orchestrator.yaml` part), SEC-07 (untracked files), SEC-05, SEC-08 | Approval binding beyond HEAD needs a broader content snapshot |
| SEC-03, SEC-12 | Same-user human gate is a documented limitation |
| SEC-06, SEC-11, SEC-13 | Lower severity; separate changes |
| AUTH-04 | `*_BASE_URL` is kept on purpose for proxies |
| PERF-01, PERF-02 | Need a benchmark before changing the snapshot cache |
| UX-02, UX-03, REL-01, BILL-01 | Design decisions (the dashboard never reads account homes) or larger changes |
| Remaining Medium/Low findings | Out of scope for this pass; see `08-improvement-roadmap.md` |
