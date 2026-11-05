# End-to-end QA report

Date: 2026-11-05. Build under test: `tuanvm/arch-p1-p2-refactor` at `19d3873` (`main` plus the
Track B refactor in PR #50). Test cases and per-case results: [e2e-test-cases.md](e2e-test-cases.md).

## Status after fixes (go-live check)

Final state of branch `claude/charming-heisenberg-n2y2f6` (PR #56), 2026-11-05. Every finding of
this QA run and every open finding of the audit (`docs/audits/`) is fixed. Each fix is one commit
with its own regression tests.

Evidence from the final run (container, Linux, Landlock ABI 7):

| Check | Result |
|---|---|
| `scripts/check.sh --e2e` (unit, integration, E2E with Chromium) | 635 passed, 0 failed, 0 xfailed; 5 skipped (real-CLI smokes that need real accounts) |
| E2E suite alone (`tests/qa`) | 63 cases, 88 runs, 88 pass ([e2e-test-cases.md](e2e-test-cases.md)) |
| Audit reproductions (`docs/audits/evidence/test_audit_repro.py`) | 10 of 11 now fail at the line that asserted the defect, which means the defects are gone. `test_rel01` still passes only because it replaces `review._git` with a stub returning `""`, bypassing the fix; KIT-UNIT-048 and KIT-REL-EVID-001/002 make real git exit 128 and show the fix |
| `ruff check --select F bridge` | clean apart from an unused `install` import in `progress.py` that predates this work and that tests reach through `progress.install` |

### QA findings

| ID | Severity | Status | Fix |
|---|---|---|---|
| QA-BUG-001 | High | Fixed | `9cc0b3a`: verification confined with Landlock (`workflow.verify_sandbox`) |
| QA-BUG-006 | High | Fixed | `e88a2cb`: Claude agents denied reads of Claude logins, account homes and `.env*`/keys |
| QA-BUG-009 | High | Fixed (found during the fixes) | `3efeeb1`: an interrupted attempt that left out-of-scope changes blocks the task |
| QA-BUG-008 | High | Fixed (found during the fixes) | `a590f07`: git hooks and `.git/config` are in the snapshot |
| QA-BUG-002 | Medium | Fixed | `0181ccc`: `usage`, `account-list` and `health` use the start gate's reserve |
| QA-BUG-005 | Medium | Fixed | `2c66bb1`: dashboard fits a phone screen |
| QA-BUG-007 | Medium | Fixed (found during the fixes) | `06d1c4c`: race-free schema migration when processes open a store at once |
| QA-BUG-003 | Low | Fixed | `62e11a8`: one-line errors for unreadable spec files |
| QA-BUG-004 | Low | Fixed | `d434f9e`: shell operators refused in verification commands |

QA-BUG-007 to 009 were found during this work. 007 appeared as the E2E parallel-create test
failing 2 of 3 runs. 008 and 009 came out of reviewing the verification and recovery paths; 009
was reproduced first: a quota pause, then a resume, closed a task DONE with an out-of-scope file
in place.

### Audit findings

| Finding | Status | Commit |
|---|---|---|
| SEC-01 (High) | Fixed | `3101afd` (diff after verification, process groups ended), `9cc0b3a` (Landlock) |
| SEC-02 (High) | Fixed | `98ff857` |
| SEC-03 | Fixed | `2428763` |
| SEC-04 | Fixed | `3101afd` |
| SEC-05 | Fixed | `1e4ccb8` |
| SEC-06 | Fixed | `c49f21c` |
| SEC-07, SEC-09, SEC-10 | Fixed | `9a4aa5b` |
| SEC-08 | Mitigated and documented | `89e8341`: the Python API refuses inside agents; the trust model says human-only steps rely on agent confinement, since everything runs as one user |
| REL-01 | Fixed | `09d6574` |
| REL-02 | Fixed | cancel half in `950113b`; recovery half `8cc5f9b` |
| REL-03, REL-06 | Fixed earlier | PR #42 |
| REL-04, REL-05, REL-07 | Fixed | `fd2f52c` |
| CI (PR-12) | Added | `7fad951`: needs a human review (sensitive path) |

### Production readiness, re-evaluated (proposal for `docs/audits/executive-summary.md`)

| Use | Verdict |
|---|---|
| A developer orchestrating trusted agents on their own repositories, reading diffs before approving | **Go** |
| Containing a misled or prompt-injected executor (untrusted repositories, issue text or dependencies as input) | **Go on Linux with Landlock** (`verify_sandbox: auto` or `strict`), with Codex as the executor. On macOS and Windows checks run unconfined unless `strict` refuses them; keep a human reviewer there |
| Gates-only workflows (no reviewer) closing tasks automatically | **Go**: verification-time writes, ignored files, interrupted attempts and protected paths now gate without a reviewer |

### Residual risks (accepted, documented)

- Everything runs as one user: the Python API guard and `.claude/settings.json` denies are process
  discipline, not OS boundaries (SEC-08).
- Confined checks can still read your files, and under `auto` they keep the network (`strict`
  cuts TCP).
- Not verified here: the real Codex, Claude, opencode and agy CLIs. Their opt-in smoke tests
  (`CODEX_SMOKE=1` and so on) need real accounts and were skipped. One manual probe is worth doing
  on a real Claude CLI: the new `Read(//…)` denies for account homes. CI on macOS runs in GitHub
  Actions once PR #56 is pushed.

The findings below are as found on 2026-11-05, before the fixes.

## Verdict at the first QA run (before the fixes)

**NO-GO** for multi-account setups with Claude CLI agents, and for running tasks or plans whose
verification commands come from an untrusted source. **Conditional GO** for single-user use on
trusted repositories with Codex executors.

The orchestration core is solid. The task lifecycle, failure handling, failover, concurrency
limits, crash recovery, the MCP surface and the dashboard's HTTP guards all behaved as documented
under real runs. Two High security defects decide the verdict: one new (QA-BUG-006) and one known
from the audit (SEC-01, here QA-BUG-001).

## Summary

| | Count |
|---|---|
| Test cases (unique) | 63 |
| Executions (with parameters) | 88 |
| PASS | 82 |
| FAIL (confirmed defects, kept as strict `xfail`) | 6 |
| BLOCKED (not executable here, see below) | 6 |

| Area | Cases | Pass | Fail |
|---|---|---|---|
| Task lifecycle | 11 | 11 | 0 |
| Failures, invalid input, boundaries | 15 (32 runs) | 30 | 2 |
| Multi-account, limits, failover | 7 | 6 | 1 |
| Multi-bridge, parallelism, recovery | 6 | 6 | 0 |
| MCP server | 5 | 5 | 0 |
| Web dashboard (HTTP + Playwright) | 5 (13 runs) | 12 | 1 |
| Security | 8 | 6 | 2 |
| Workflows, reviewer agent, shutdown, storage, TUI entry | 6 | 6 | 0 |

## Scope and method

Every test drives the product the way a person or Claude Code does:
- the `agent-bridge` CLI as subprocesses, after `agent-bridge init`;
- the MCP server started from the project's `.mcp.json`, over stdio;
- `agent-bridge web` through HTTP and in Chromium (Node Playwright 1.63).

Results are checked against command output, the store (SQLite, read-only), attempt logs, files in
the worktree, process tables and the browser DOM. The agent CLIs are the repository's
deterministic fakes (`tests/fakes`). They can play every outcome: ok, quota, auth, network, crash,
hang, slow, commit, out-of-scope write, missing JSON, planner and reviewer replies, reported cost
and utilisation.

Isolation: each test gets its own `HOME`, `TMPDIR`, lock directory and git repository under
pytest's temporary directory. No real account, home, project or production data is touched. The
`.ai/` files written by tests belong to those throwaway repositories.

Run it: `scripts/check.sh --e2e` (full suite plus E2E), or `QA_E2E=1 uv run pytest tests/qa`.

Coverage of the requested dimensions:

| Dimension | Covered by |
|---|---|
| Normal flows | QA-E2E-001..011, 040, 090, 092 |
| Abnormal (bad input, API errors, timeout, lost connection, crash) | 020..035, 062, 071 |
| Boundary (limits, empty data, quota, volume, concurrency) | 033, 034, 041, 044, 051, 052, 074 |
| Edge (races, repeats, restart, recovery) | 004, 009, 050, 053, 054, 093 |
| UX (navigation, validation, messages, UI states) | 030..032, 073 (load, drawer, no_token, search, language, offline, mobile), 095 |
| Security (auth, authorisation, isolation, secrets) | 045, 060, 064, 070, 080..087 |
| Multi-account, multi-bridge, parallel, quota, failover | 040..046, 050..055 |

## Defects

| ID | Severity | Area | Summary |
|---|---|---|---|
| QA-BUG-006 | High | Security | A Claude agent can read Claude logins, other accounts' Claude homes and `.env` |
| QA-BUG-001 | High (known: audit SEC-01) | Security | Verification commands run unsandboxed: any task or plan can run code as the user |
| QA-BUG-002 | Medium | Accounts / UX | `usage` reports an account `ok` while routing and the start gate refuse it |
| QA-BUG-005 | Medium | Web UX | The dashboard scrolls 541 px sideways on a 375 px phone screen |
| QA-BUG-003 | Low | CLI UX | `create` with a missing spec file prints a Python traceback |
| QA-BUG-004 | Low | Validation | Shell operators in verification commands are accepted and silently become arguments |

### QA-BUG-006: Claude agents can read Claude credentials and `.env` (High)

- **Evidence:** `test_claude_cannot_read_claude_credentials`. The `--disallowedTools` value passed
  to every Claude run contains four Read denies: `~/.codex/auth.json`, `~/.codex-accounts/**`,
  `~/.agent-bridge/homes/**` and `~/.gemini/antigravity-cli/**`. It has no deny for
  `~/.claude/.credentials.json`, for the configured anthropic account homes (`~/.claude-accounts/*`
  in every shipped example) or for `./.env`. Writes to `.env` are denied; reads are not.
- **Reproduce:**
  1. Configure two `claude-cli` accounts with homes outside `~/.agent-bridge/homes`.
  2. Run any task.
  3. Inspect the agent argv (the fake logs it): Read is allowed, and none of those paths is denied.
- **Impact:** every Claude executor or reviewer has the Read tool. A prompt injected through
  repository content can make it read another account's OAuth token or the project's secrets, and
  the content reaches the provider and possibly the diff. This breaks the kit's own rule: "never
  read `.credentials.json` / account-home credentials".
- **Root cause:** `executor._claude_credential_denies()` copies only the static `Read(...)` rules
  from the kit's `.claude/settings.json`, and those rules list the Codex, agent-bridge and agy
  paths only.
- **Fix:** add `Read(~/.claude/.credentials.json)`, `Read(./.env)`, `Read(./.env.*)`,
  `Read(./**/.env*)` and the key patterns to the kit denies. In `ClaudeAdapter.build`, also deny
  `Read(<home>/**)` for every configured account home other than the run's own. Add a probe
  against a real Claude CLI.

### QA-BUG-001: unsandboxed verification commands (High, known SEC-01)

- **Evidence:** `test_verification_cannot_write_outside`. A verification command
  `python3 -c "open('$HOME/written-by-a-check','w')..."` creates the file outside the worktree.
- **Impact:** verification commands come from the task spec, which a planner agent can write.
  `python3`, `node` and similar interpreters are on the allowlist, so a spec can run any code with
  the user's permissions, network and credentials. The Codex sandbox only covers the agent itself.
- **Root cause:** `review.run_verification` uses `create_subprocess_exec` with a scrubbed
  environment but no OS sandbox. This is the open audit finding SEC-01 (fix 3 is PR-9 in
  `docs/audits/implementation-plan.md`).
- **Fix:** carry out PR-9: run the checks under the Codex sandbox or an OS sandbox with the
  network off, writes limited to the worktree and `/tmp`. Until then, require human confirmation
  of planner-written verification commands.

### QA-BUG-002: `usage` disagrees with the start gate (Medium)

- **Evidence:** `test_usage_agrees_with_start_gate`. Setup: `budget_usd.day` 1.0, `reserve_usd` 0.5,
  one run that cost 0.7. Results:
  - `usage` reports status `ok` with $0.30 remaining.
  - `agents` reports claude1 as "over limit: $0.70 spent+reserved, $0.50 reserve > $1.00".
  - `execute` refuses the task.
- **Impact:** during a quota incident, the screen the docs point to says the account is fine.
- **Root cause:** `Bridge.usage_report` calls `_evaluate(n, expire=False)` with `reserve=0`.
  Routing (`_unavailable`) and the start gate (`_reserve`) add the next run's `reserve_usd`.
- **Fix:** evaluate `usage` with the same reserve as routing, or add a "next run" column that shows
  whether the next start would be refused and why.

### QA-BUG-005: dashboard not usable at phone width (Medium)

- **Evidence:** the `mobile` scenario in `test_web_browser`. At a 375 px viewport,
  `scrollWidth - clientWidth` is 541 px. Screenshot: the filter row, the status chips and the
  six-column table keep their desktop widths.
- **Fix:** let the filter row and chips wrap (`flex-wrap`), and put the table inside an
  `overflow-x: auto` container, or switch to a card layout below about 640 px.

### QA-BUG-003: traceback for a missing spec file (Low)

- **Reproduce:** `agent-bridge create /nonexistent.json` prints a full Python traceback
  (`FileNotFoundError`) instead of an `error:` line. Invalid JSON is already reported cleanly.
- **Fix:** in `cli._run` for `create`, catch `OSError` and report `error: cannot read spec: <path>:
  <reason>`.

### QA-BUG-004: shell syntax accepted in verification commands (Low)

- **Reproduce:** a verification command `python3 -c 'print(1)' && python3 -c 'raise SystemExit(1)'`
  is accepted. The command runs without a shell, so `&&` and everything after it become arguments
  of the first command, and the second command never runs. The check passes even though the
  author expected it to fail.
- **Fix:** in `review.parse_command`, reject the tokens `&&`, `||`, `;`, `|`, `>`, `<`, backticks
  and `$(`, with a message saying commands run without a shell.

### Observations (no defect filed)

- `status T9999` prints `error: 'unknown task: T9999'`, with quotes from a `KeyError` repr. This is
  cosmetic.
- The `storage ... --apply` guard counts every `agent-bridge` process on the machine, including
  other users' sessions and other `HOME`s. Here it refused `prune --apply` because this
  environment's own MCP server was running. That is safe but coarse.
- The "Tasks (n)" tab counts open tasks, while the "All" chip counts every task. This is
  consistent, but the first visit shows "Tasks (0)" next to "All (1)".

## Evaluation

- **Correctness:** every documented state transition was observed with real processes, including
  the human gate for high-risk work and sensitive paths. The bridge's own checks, not the agent's
  self-report, decided REVIEW versus REWORK. The fake agent reports no changed files while writing
  one; the bridge's diff still found the change. An agent that committed by itself was sent to
  REWORK.
- **Stability:** no flaky result across two full E2E runs plus the per-area runs. Twenty parallel
  `create`s produced distinct ids with no lock error.
- **Performance** (100+ tasks, this container):
  - `create` median 193 ms (p95 229 ms).
  - `list` 188 ms, `health` 280 ms.
  - A fake-agent `execute` takes 261 ms end to end.
  - `/api/overview` median 7 ms (max 37 ms, 38 KB).
  - The browser table with 300 tasks renders in under 3 s.
- **Security:** the web dashboard holds up:
  - token required, kept in `sessionStorage` and removed from the URL;
  - Host check against DNS rebinding;
  - GET only;
  - traversal-proof routes;
  - CSP and nosniff headers;
  - no XSS through task text.

  Other passing checks:
  - Secrets are redacted in stored output.
  - Provider keys never reach agents or checks.
  - State lives outside the worktree with 0700/0600 permissions.
  - MCP exposes no approval or account tools.
  - Agents cannot start the bridge.

  The open issues are QA-BUG-006 and QA-BUG-001.
- **Recovery:**
  - A runner killed with SIGKILL is detected on the next write command: the task becomes PAUSED
    (INTERRUPTED), the orphaned agent is killed, and a resume continues the same session.
  - Closing the MCP session mid-run parks the task and gives the attempt back.
  - Cancelling during an agent run or during verification stops it in under 20 s and kills the
    process group.
  - A timeout kills the agent and its children at 30 s.

## Blocked

| Item | Why |
|---|---|
| `agent-bridge approve`, `reassign`, `account-add/login/remove` | Human-only by the project's hard rules; not run. The test verified that the MCP surface lacks them and that `init` denies them to Claude Code |
| Real Codex / Claude / opencode / agy CLIs and their OS sandboxes | Needs real accounts and quota. Opt-in smoke tests exist in `tests/e2e` |
| `storage prune --apply` success path | Refused because an `agent-bridge` MCP server of this environment was running. The refusal itself was verified |
| Interactive TUI flows | Need a terminal. Covered by the Textual pilot tests in `tests/integration/test_tui.py`; the E2E check covers the non-terminal entry only |
| macOS and Windows | Linux container only |
| Concurrency across different users or machines | Single machine, single user |

## Recommended actions, in order

1. Fix QA-BUG-006 (Claude credential and `.env` read denies, per-account home denies) and add a
   real-CLI probe.
2. Carry out PR-9 (SEC-01). Until then, require human confirmation for planner-written
   verification commands and document the risk prominently.
3. Make `usage` use the start gate's reserve (QA-BUG-002).
4. Responsive dashboard layout (QA-BUG-005).
5. Clean CLI errors for unreadable spec files, and reject shell operators in verification
   commands (QA-BUG-003, QA-BUG-004).
6. Run `scripts/check.sh --e2e` in CI once PR-12 adds CI. When a defect is fixed, remove its
   `xfail` marker; the strict marker makes the test fail until then.
