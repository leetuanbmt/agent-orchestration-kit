# Audit implementation traceability

Maps the findings of the 2026-11-08 audit (`MASTER-AUDIT-REPORT.md` and the phase reports, left
unchanged) to the changes on branch `feat/audit-driven-improvements`. Each fix was written by the
executor agent through agent-bridge, verified by the bridge (full test suite) and reviewed before
commit, unless its row says it was written directly by Claude while no executor had quota.

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
| SEC-01 (unconfined part, step 2) | High | macOS had no verification sandbox | Seatbelt (`sandbox-exec`) profile confines writes to the worktree, TMPDIR and `verify_writable`; strict also denies TCP; AppleEvents, LaunchServices and `open`/`launchctl`/`osascript` are denied; `health` calls it best effort. macOS process start times come from libproc because setuid `ps` cannot run inside the sandbox (`bridge/sandbox.py`, `bridge/security.py`) | a0a2700, 9bc95d6 | `tests/unit/test_sandbox_seatbelt.py` |
| UX-02 | Medium | The dashboard showed quota only from the bridge's own DB, so Codex rollout limits never appeared | The overview reads the newest `rate_limits` from each openai account's rollouts (30 s cache) when newer than the DB row (`bridge/progress.py`) | 6da250e | `tests/unit/test_progress_quota.py` |
| REL-01 | Medium | The slot flock lived only in the runner, so a killed runner freed the account while its agent kept running; read-only views showed RUNNING forever | The lock fd is inherited by the agent (`pass_fds`) and `release()` unlocks explicitly; `status`/`list` carry `runner_alive` and the dashboard flags `runner_dead` with the resume command, without writing (`bridge/accounts.py`, `bridge/osutil.py`, `bridge/executor.py`, `bridge/runner.py`, `bridge/service.py`, `bridge/progress.py`, `bridge/static/app.js`) | 9f984d6, d3c9e88 | `tests/integration/test_orphan_slot.py`, `tests/integration/test_runner_liveness.py`, `tests/unit/test_progress.py::test_snapshot_dead_runner` |
| SEC-08 | Low | With a review stage, ignored files the agent changed were only listed | An agent-written ignored file outside `allowed_paths` (tool caches excepted) is a violation in every workflow; ignored files left only by verification stay report-only with a reviewer (`bridge/review.py`, `bridge/runner.py`) | 840bba7 | `tests/integration/test_ignored_files.py` |
| SEC-07 (untracked part) | Medium | Approval bound only the attempt's own files | Approval is refused when new untracked, non-ignored files appeared since verification, except files later attempts of other tasks recorded and protected files (`bridge/review.py`, `bridge/service.py`, `bridge/state.py`) | d6c925c | `tests/integration/test_approval_binding.py` (BIND-009..013) |
| SEC-04 (`.ai/orchestrator.yaml` part) | Medium | `protected_dirty` used git status, which cannot see the git-ignored orchestrator config, so a tampered file stayed invisible after the task was BLOCKED | Attempts stopped by a protected-file violation record what each file held (`protected_after`); `health` lists the file in `protected_dirty` (NOT OK) while it still holds that content, and a restore or a human edit clears it (`bridge/review.py`, `bridge/runner.py`, `bridge/service.py`, `bridge/state.py`). Written directly by Claude with the user's approval because no executor had quota left | 2f0a631 | `tests/integration/test_protected_tamper.py` |
| BILL-01 | Medium | A CLI-reported `total_cost_usd` was stored as if it were the bill, and `budget_usd` gated subscription logins on list prices | CLI-reported costs are recorded with source `reported` and counted as estimates; `config-check` warns when `budget_usd` is set on an anthropic or openai account (`bridge/quota.py`, `bridge/state.py`, `bridge/cli.py`). Written directly by Claude (no executor quota) | 0201379 | `tests/unit/test_usage_ledger.py`, `tests/integration/test_limit_hardening.py`, `tests/unit/test_quota_limits.py` |
| AUTH-02 | Medium | One global `CLAUDE_CODE_OAUTH_TOKEN` overrode every Claude account's login | An anthropic account may name its own `token_env` (`AGENT_BRIDGE_CLAUDE_<NAME>_TOKEN`); only that account's runs get it, and only when it looks like a `setup-token` value; a missing or wrong value reads as logged out; Claude agents cannot read `.ai/orchestrator.yaml` (`bridge/accounts.py`, `bridge/orchestrator.py`, `bridge/service.py`, `bridge/executor.py`). Written directly by Claude (no executor quota) | a5903ae, 22a0ece | `tests/unit/test_account_tokens.py` |
| CI flake | - | `test_reassign` failed on CI when background git maintenance removed `maintenance.lock` during a copy | The repo fixture turns off `gc.auto` and `maintenance.auto` (`tests/conftest.py`) | 76769ea | CI green on 76769ea and a5903ae |
| PERF-01, PERF-02 | Medium | Every dashboard request ran `git worktree list` and `git rev-parse` for every project | Worktree lists, store paths and the project list are cached for 10 s; task rows keep their mtime cache and runner liveness is never cached (`bridge/progress.py`). In-process measurement on this machine (49 projects, 252 tasks): `snapshot` 36 → 0 `subprocess.run` per request, p50 800 → 81 ms; `task_detail` 10 → 0, p50 192 → 14 ms. Written directly by Claude (no executor quota) | (this commit) | `tests/unit/test_progress.py::test_topology_cached_liveness_not` |
| TEST-03 | Medium | The keyboard e2e pressed Enter before any poll had re-rendered the rows | Both Enter checks wait for an `/api/overview` response first; with the UX-01 bug put back, "Enter on a task row opens the drawer" fails | (this commit) | `tests/qa/web/dashboard.cjs` |
| TEST-04 | Medium | Sandbox claims rested on one-off probes; nothing flagged a newer CLI | `health` warns `cli_unprobed` when codex or claude is newer than `PROBED_VERSIONS` (`bridge/service.py`, `docs/REPORT.md`). The probes themselves still need a human run with real accounts | (this commit) | `tests/integration/test_health_diagnostics.py::test_unprobed_cli_is_warning` |
| TEST-01 | Medium | Confirmed findings had no regression test | Every row in this table names the test that fails without its fix; the audit repro covers the original reproductions | - | this table, `docs/audits/evidence/` |
| FUN-03 | Low | `execute` wrote a route (agent, account, event) onto a task before checking it was READY | `execute_task` refuses a non-READY task first (`bridge/service.py`). Written directly by Claude | (P2 commit) | `tests/integration/test_orchestration.py::test_execute_terminal_task_is_not_routed` |
| FUN-05 | Low | The first slot retry ignored a `slot_wait_s` below 5 s | The first retry is capped by the deadline (`bridge/service.py`). Written directly by Claude | (P2 commit) | `tests/integration/test_slot_wait.py::test_slot_wait_expires` |
| CFG-04 | Low | `config-check` passed account homes that routing later refused | `config-check` runs the same isolation check and exits 1 with `errors` (`bridge/cli.py`). Written directly by Claude | (P2 commit) | `tests/integration/test_limit_hardening.py::test_config_check_rejects_shared_homes` |
| CFG-06 | Low | A broken registry entry was reported against `.ai/orchestrator.yaml` | Errors in an account that only the registry declares name `accounts.json` (`bridge/orchestrator.py`). Written directly by Claude | (P2 commit) | `tests/unit/test_orchestrator.py::test_registry_error_names_registry_file` |
| TEST-02 | Low | A hung test could hold a CI job for GitHub's 6-hour default | Both CI jobs have `timeout-minutes: 30` (`.github/workflows/ci.yml`) | (P2 commit) | CI |

## Verification

- Full suite after the last code change (2f0a631, run directly because no executor had quota):
  693 passed, 114 skipped. Before that, the bridge-run suite at d6c925c: 689 passed, 114 skipped (macOS; the Seatbelt real-execution tests skip there
  because the bridge's own checks already run inside Seatbelt).
- `tests/unit/test_sandbox_seatbelt.py` run outside the nested sandbox: 14 passed.
- `QA_E2E=1` web suite after the REL-01 dashboard change (d3c9e88): 30 passed, 1 skipped (axe-core
  not installed).
- `QA_E2E=1 pytest tests/qa` at d6c925c: 105 passed, 1 skipped (axe-core not installed).
- `docs/audits/evidence/run_audit_repro.sh docs/audits/evidence/test_audit_2026_11_08_security.py`
  at 2f0a631: 9 failed, 6 passed. The reproductions for SEC-01 (forgery), SEC-02, SEC-04 (config
  and `.ai/orchestrator.yaml`), SEC-05, SEC-07 (untracked file), SEC-08
  (`test_old_sec03_with_review_stage`) and SEC-09/10 fail, meaning the findings are gone. Of the
  ones that still pass, two are documented or deferred findings: the same-user TTY gate (SEC-03)
  and a `setsid` child writing after the run (SEC-06). One is a known residual of an earlier
  audit: `python3 -c` and `npx` remain allowlisted. The other three assert controls that work:
  web auth, the old REL-01 git-failure path, and the SEC-10 pattern refusals.

## Not addressed on this branch

| Finding | Reason |
|---|---|
| SEC-03, SEC-12 | Same-user human gate is a documented limitation |
| SEC-06, SEC-11, SEC-13 | Lower severity; separate changes |
| AUTH-04 | `*_BASE_URL` is kept on purpose for proxies |
| Remaining Medium/Low findings | Out of scope for this pass; see `08-improvement-roadmap.md` |
