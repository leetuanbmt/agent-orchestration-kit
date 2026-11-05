# E2E test cases

Generated from the `@pt.*` metadata in `tests/qa/` and the JUnit results of the
run on 2026-11-05 (`scripts/check.sh --e2e tests/qa`). A known failure is an `xfail(strict=True)`
test tied to a defect in [the QA report](e2e-qa-report.md#defects); it turns into a failure once
the defect is fixed, so the marker must then be removed.

## Task lifecycle

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-001-happy-path | Create, execute, verify, review and close a task from the CLI | fresh project after `agent-bridge init`; the executor writes x = 2; one verification command | READY -> REVIEW -> DONE; the file changed, the patch and the bridge's own check are in the result, the review is recorded | PASS |
| QA-E2E-002-rework-loop | Rework continues the same agent session | a task in REVIEW is sent back with notes, then resumed from the CLI | REWORK, then the resume reuses the session (codex exec resume) with the notes in the prompt, then REVIEW and DONE | PASS |
| QA-E2E-003-block | A blocked task is terminal | review --decision block, then resume and execute | BLOCKED with the notes in failure; resume and execute fail with a clear message; no further agent run | PASS |
| QA-E2E-004-dedup | Creating the same spec twice | the identical spec file is submitted twice, then a spec that differs by one criterion | the second call returns the same task with duplicate=true; the changed spec gets a new id | PASS |
| QA-E2E-005-dependencies | depends_on keeps a task in BACKLOG until its dependency is DONE | T2 depends on T1; T2 is executed early, then T1 is completed | T2 is BACKLOG and execute refuses naming the dependency; after T1 is DONE, T2 is READY and runs | PASS |
| QA-E2E-006-attempt-budget | A task that keeps failing its checks ends BLOCKED | max_attempts 2 and a verification command that always fails | attempt 1 -> REWORK naming the failed check; attempt 2 -> BLOCKED; a third resume is refused without running the agent | PASS |
| QA-E2E-007-high-risk | A high-risk task waits for a human after approval | risk_level high; the client approves the review | APPROVED (not DONE), needs_human_approval true, and the next step names `agent-bridge approve <id>`; the human approval itself is not run (human-only) | PASS |
| QA-E2E-008-sensitive-path | A change under a sensitive path needs a human | a low-risk task allowed to write migrations/ writes a migration file | after approval the task stops at APPROVED with needs_human_approval | PASS |
| QA-E2E-009-cancel-running | Cancelling a running task from another terminal | terminal A runs `execute` on a hanging agent; terminal B runs `cancel` | B returns CANCELLED within the cancel wait; A exits; the agent and its child process are gone | PASS |
| QA-E2E-010-failed-check | Failing verification sends the task to REWORK with the check output | the agent completes but the verification command exits 1 with a message | REWORK; failure names the check; the check output is in the result | PASS |
| QA-E2E-011-list-views | Task listings for a person in a terminal | two tasks, one in REVIEW; `list --state REVIEW`, `list --limit 1`, `tasks` across projects | the state filter and limit apply; `tasks` groups this project with its counts | PASS |

## Failures, invalid input, boundaries

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-020-quota-pause | An agent hitting its usage limit pauses the task and marks the account | the executor reports a usage limit; then another task on the same account; then account-clear and resume | PAUSED with QUOTA, attempt given back; account-list shows quota; a new execute is refused naming account-clear; after account-clear the resume reaches REVIEW | PASS |
| QA-E2E-021-auth-pause | An expired login pauses the task and never moves it | the executor answers 401 | PAUSED with AUTH; the account is marked auth; resume is refused until the mark is cleared | PASS |
| QA-E2E-022-network-retry | A dropped connection is retried once | the first agent call loses the stream, the second succeeds | one retry event and the task reaches REVIEW; two agent calls | PASS |
| QA-E2E-023-crash | An agent crash fails the attempt; resume recovers | the agent exits 139; then resume with a healthy agent | FAILED with CRASH and one attempt used; resume reaches REVIEW | PASS |
| QA-E2E-024-timeout | A hanging agent is killed at its timeout | timeout_s 30 (the minimum) and an agent that hangs with a child process | FAILED with TIMEOUT after about 30 s; the agent and its child are killed | PASS |
| QA-E2E-025-nojson | An agent that does not return its final JSON | the agent prints plain text as its final message | REWORK because the agent status is unknown; the raw text is kept in the result | PASS |
| QA-E2E-026-missing-binary | The executor CLI is not installed | codex_bin points to a missing program | health is NOT OK (exit 1); execute pauses the task with MISSING_BINARY and gives the attempt back | PASS |
| QA-E2E-027-scope-violation | An agent writing outside its allowed paths | allowed_paths src/**; the agent also writes docs/notes.md, then a run that writes .env | REWORK with a scope violation naming the file each time; never REVIEW | [docs/notes.md] PASS<br>[.env] PASS |
| QA-E2E-028-head-moved | An agent committing on its own | the agent runs git commit | REWORK with head_changed true | PASS |
| QA-E2E-030-invalid-specs | Invalid task specs are refused at creation | each bad spec in BAD_SPECS | exit 1 with a message naming the problem, no traceback, and no task is created | [protected-path] PASS<br>[traversal] PASS<br>[absolute] PASS<br>[git-dir] PASS<br>[ai-config] PASS<br>[not-allowlisted] PASS<br>[objective-too-long] PASS<br>[timeout-too-low] PASS<br>[timeout-too-high] PASS<br>[attempts-too-high] PASS<br>[unknown-field] PASS<br>[bad-risk] PASS<br>[empty-objective] PASS<br>[no-paths] PASS<br>[danger-sandbox] PASS |
| QA-E2E-031-bad-files | Spec files that are missing or not JSON | a path that does not exist, a non-JSON file and a JSON list | exit 1 with a one-line error and no Python traceback | [None] FAIL (known: QA-BUG-003)<br>[not json] PASS<br>[[1, 2]] PASS |
| QA-E2E-035-shell-syntax | Verification commands written with shell operators | a verification command "python3 -c 'print(1)' && python3 -c 'raise SystemExit(1)'" | refused at creation: commands run without a shell, so the second part would never run | FAIL (known: QA-BUG-004) |
| QA-E2E-032-unknown-ids | Commands on a task that does not exist, or in the wrong state | status/result/execute/cancel/resume on T9999; review on a READY task; an invalid decision and state filter | each exits non-zero with a short message and no traceback | PASS |
| QA-E2E-033-boundaries | Boundary values a user can hit | empty project; a 4000-character Vietnamese objective (the maximum); max_attempts 1 and 5; timeout_s 30 | empty listings; the long objective is stored whole and shown cut to 200 characters; the limits are accepted | PASS |
| QA-E2E-034-many-tasks | Listing and creating at volume | 150 tasks created through the CLI | list returns 50 by default and at most 200 with --limit 500; each create stays under 2 s; list stays under 2 s | PASS |

## Multi-account, limits, failover

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-040-config-views | A two-account setup as the user sees it | two claude-cli executors on cl1 and cl2, cl1 with a five-hour limit | config-check OK; agents lists both available in order; usage shows cl1's limit as ok and cl2 without limits | PASS |
| QA-E2E-041-route-around-limit | Work moves to the next account once one is over its usage limit | cl1 stops at five_hour_pct 80; its first run reports 90 % used | task 1 runs on cl1; afterwards agents shows claude1 over limit and usage shows limited; task 2 is routed to claude2 and runs in cl2's home | PASS |
| QA-E2E-042-quota-failover | A task paused on QUOTA moves to the next account when the user enabled fallback | quota_fallback enabled; claude1 reports a usage limit on its first run | PAUSED (QUOTA); resume moves it to claude2 (account_auto_switched, reason quota) and finishes in cl2's home | PASS |
| QA-E2E-043-no-fallback | Without fallback a QUOTA pause waits for the user | quota_fallback disabled; claude1 reports a usage limit | PAUSED; resume is refused naming account-clear; no account_auto_switched event; claude2 never runs | PASS |
| QA-E2E-044-budget | A daily budget stops an account; the next account takes the work | cl1 has budget_usd day 1.0 and reserve_usd 0.5; each run costs 0.7 | run 1 on cl1 spends 0.7; usage shows the spend and agents shows claude1 over limit; a task pinned to claude1 is refused with the limit message; an unpinned task runs on claude2 | PASS |
| QA-E2E-045-account-isolation | Every run uses its own account's auth home and never sees provider keys | two Codex accounts with their own homes; OPENAI_API_KEY and ANTHROPIC_API_KEY set in the user's shell | each run gets CODEX_HOME of its account; the agent process has no OPENAI_API_KEY | PASS |
| QA-E2E-046-usage-agrees-with-gate | `usage` tells the user an account will refuse its next run | cl1 has budget_usd day 1.0 and reserve_usd 0.5; one run cost 0.7, so the next run (0.7 + 0.5 > 1.0) is refused | usage reports cl1 as limited (or otherwise not ok), matching agents and execute | FAIL (known: QA-BUG-002) |

## Multi-bridge, parallelism, recovery

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-050-same-task-twice | Two terminals start the same task at the same moment | two `execute` processes on one READY task while the agent takes 3 s | exactly one runs the agent and reaches REVIEW; the other exits 1 with a clear message; one attempt is recorded | PASS |
| QA-E2E-051-concurrency-across-bridges | An account's concurrency limit holds across bridges in two worktrees | a second git worktree with its own bridge; cx1 and cx2 allow one run each (machine-wide lock dir); worktree A runs a slow task pinned to codex1; worktree B then starts a task pinned to codex1 and an unpinned one; a second task in worktree A while its first runs | B's pinned task is refused as busy and stays READY; B's unpinned task is routed to codex2; A refuses a second writer in its own worktree; A's task finishes normally | PASS |
| QA-E2E-052-parallel-creates | Many terminals creating tasks at once | 20 `create` processes in parallel with distinct specs | all succeed with 20 distinct ids; no database-locked error | PASS |
| QA-E2E-053-runner-killed | The terminal running a task is killed (kill -9) mid-run | `execute` on a hanging agent is SIGKILLed; the user then runs `list` and a write command | the next writing command parks the task PAUSED (INTERRUPTED) and kills the orphaned agent; resume reaches REVIEW on the same session | PASS |
| QA-E2E-054-cancel-during-verification | Cancelling while the bridge runs the verification commands | a verification command that sleeps 30 s; cancel from a second terminal once VERIFYING | CANCELLED within the cancel wait; the verification process is killed; the runner exits | PASS |
| QA-E2E-055-two-projects | One person, two projects | tasks in two repositories on the same machine | `tasks` lists both projects with their own tasks; each project's `list` shows only its own | PASS |

## MCP server

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-060-mcp-tools | The MCP surface Claude Code sees | the server started from the project's .mcp.json | exactly the 15 documented tools; none can approve, reassign or manage accounts | PASS |
| QA-E2E-061-mcp-lifecycle | A full task through MCP, as the client role does it | create, execute (background), status with wait, result, review approve | REVIEW after the wait with the patch and checks in the result; DONE after approve; the CLI sees the same task | PASS |
| QA-E2E-062-mcp-errors | Bad requests through MCP | a protected path, an unknown task, an invalid review decision, a high-risk task approved by the client | each bad call is an error result with a readable message and no task is created; the high-risk task stays APPROVED waiting for a human | PASS |
| QA-E2E-063-mcp-and-cli-together | The MCP server and a terminal share one project | the server is running; the user creates and runs a task from the CLI meanwhile | the MCP list and status show the CLI's task and its REVIEW state without restarting the server | PASS |
| QA-E2E-064-no-recursion | An agent the bridge started cannot start the bridge | AGENT_BRIDGE_AGENT=1 in the environment, as every agent process gets | `serve` and other CLI commands refuse to run and exit non-zero | PASS |

## Web dashboard (HTTP + Playwright)

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-070-web-http-guards | The dashboard's HTTP guards | requests without a token, with a wrong token, with a foreign Host header, with POST/DELETE, and with traversal or malformed ids | 401, 401, 421, 405, 404; the real token gets 200 JSON; security headers on every response; the token is long and random | PASS |
| QA-E2E-071-web-port-busy | Starting a second dashboard on a port in use | `agent-bridge web --port <port of the running one>` | exit 1 with a hint to use --port 0, no traceback | PASS |
| QA-E2E-072-web-log | Reading an attempt's log from the dashboard API | the REVIEW task's first attempt log, paged | 200 with log lines; a bad query is 404 | PASS |
| QA-E2E-073-web-browser | The dashboard in a real browser | one scenario each: load, drawer, no_token, xss, live, language, search, mobile, offline (see tests/qa/web/dashboard.cjs) | every check of the scenario passes and the page raises no JavaScript error | [load] PASS<br>[drawer] PASS<br>[no_token] PASS<br>[xss] PASS<br>[live] PASS<br>[language] PASS<br>[search] PASS<br>[offline] PASS<br>[mobile] FAIL (known: QA-BUG-005) |
| QA-E2E-074-web-performance | Overview at volume | 300 tasks in the project | /api/overview answers in under 1 s; the browser table with every task is ready in under 3 s | PASS |

## Security

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-080-redaction | Secrets printed by a verification command are not stored | a check prints a file holding an OpenAI key, a GitHub token and a full private key block | neither the result nor the database holds the raw values | PASS |
| QA-E2E-081-verification-env | Verification commands do not see the user's credentials | OPENAI_API_KEY, GITHUB_TOKEN and AWS_SECRET_ACCESS_KEY exported in the user's shell; a check prints its environment's variable names | none of the three names reaches the check | PASS |
| QA-E2E-082-state-isolation | Bridge state lives outside the worktree, private to the user | after a run with a log | the database and logs are under $HOME/.agent-bridge, not in the project; git status shows no bridge files; directories 0700, logs 0600 | PASS |
| QA-E2E-083-codex-sandbox-flags | What the Codex executor is allowed to do | a normal run | the argv asks for the workspace-write sandbox, never danger-full-access, and the run happens in the project | PASS |
| QA-E2E-084-claude-permissions | What a Claude CLI executor is allowed to do | a claude-cli writer with allowed_paths src/** | strict MCP config (no servers), no settings sources, dontAsk mode, Edit/Write scoped to ./src/**, the kit's credential read denies and protected-path write denies passed, no Bash | PASS |
| QA-E2E-085-human-only-guard | The kit `init` installs keeps human-only commands away from Claude Code | the .claude/settings.json written by `agent-bridge init` | approve, reassign, account-add/login/remove and config-init are denied; no secret values in the installed files | PASS |
| QA-E2E-086-verification-sandbox | Verification commands are confined to the project | a task whose verification command (an allowlisted interpreter) writes a file in $HOME, outside the worktree | the write is refused, or the file at least shows up as a violation | FAIL (known: QA-BUG-001) |
| QA-E2E-087-claude-credential-reads | A Claude agent cannot read Claude logins or the project's .env | a claude-cli executor on an account whose home is outside ~/.agent-bridge/homes (as in every shipped example: ~/.claude-accounts/<name>) | --disallowedTools holds Read denies for ~/.claude/.credentials.json, every configured anthropic account home and ./.env | FAIL (known: QA-BUG-006) |

## Workflows, reviewer agent, shutdown, storage, TUI

| ID | Case | Condition | Expected | Result |
|---|---|---|---|---|
| QA-E2E-090-planner-workflow | Plan an objective and drive the workflow, the client reviewing | a Codex planner returns two dependent tasks; run-workflow with the client as reviewer | the workflow is READY with both tasks (step two in BACKLOG); run-workflow runs step one to REVIEW and stops WAITING; after the review, run-workflow finishes step two | PASS |
| QA-E2E-091-bad-plan | A planner that returns an invalid plan | the plan's second task depends on a later task, and another plan asks for a protected path | the workflow is FAILED with the reason and no task is created | PASS |
| QA-E2E-092-reviewer-agent | A reviewer agent decides after the bridge's checks | a Codex reviewer; first it approves, then on another task it asks for rework | the approved task ends DONE with a review recorded as the agent's; the other goes to REWORK with the reviewer's notes | PASS |
| QA-E2E-093-session-closed-mid-run | The Claude Code session (MCP server) closes while a task runs | a task started through MCP on a hanging agent; the client disconnects | the task is PAUSED (INTERRUPTED) with its attempt given back, the agent is killed, and a resume from the CLI reaches REVIEW | PASS |
| QA-E2E-094-storage-commands | Storage housekeeping a user can run | storage status, prune (preview, then apply after a project folder was deleted), gc preview | status names the central store; prune lists the deleted folder and removes it only with --apply, which refuses with the pids while any agent-bridge process runs; gc previews without deleting | PASS |
| QA-E2E-095-tui-without-terminal | Running the dashboard command without a terminal | `agent-bridge` with no subcommand and stdout piped | a short usage text instead of a broken screen, no traceback | PASS |
