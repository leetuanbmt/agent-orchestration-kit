# Security audit

Audited revision: `3571546` (branch `tuanvm/architecture-diagram-layout`), 2026-11-04.
Current status of each finding: "Revalidation" in [implementation-plan.md](implementation-plan.md).
Method: source reading of every module on the execution path, plus executable reproductions in
[`evidence/test_audit_repro.py`](evidence/test_audit_repro.py) against throwaway git repos and the
repository's fake CLIs (`tests/fakes/`). No real credentials, real agent CLIs or network were used.

Confidence levels:

- **Confirmed**: reproduced by an executed test, or a deterministic code path with no branch
  that could avoid it.
- **Likely**: follows from confirmed behaviour plus ordinary OS semantics, not executed.
- **Hypothesis**: depends on behaviour of a real third-party CLI that was not probed.

## Threat model used

The kit's own documents define the boundary it defends: an executor agent (possibly misled by
prompt injection in the repository it reads) may try to change files outside `allowed_paths`,
touch protected files, commit, or reach credentials; the bridge must detect this, and only a
reviewer and, for high-risk work, a human may close a task. A same-user process that is *not*
started by the bridge (the user's shell) is trusted. The README already lists some limitations
(verification runs outside the sandbox; deny rules and the TTY check are not a boundary; nested
ignored secrets are not hashed). Findings below either go beyond those statements or show a
consequence the documents do not draw.

## Summary

| ID | Title | Severity | Confidence | Priority |
|---|---|---|---|---|
| SEC-01 | Verification runs executor-authored code unsandboxed, after the scope check | High | Confirmed (file writes); Likely (DB/credential access) | P0 |
| SEC-02 | Scope violations are detected but not contained; tampered config is loaded next start | High | Confirmed | P1 |
| SEC-03 | Writes to git-ignored paths are invisible to the snapshot | Medium | Confirmed | P1 |
| SEC-04 | Processes left behind by the agent keep writing after the snapshot | Medium | Confirmed (fake CLI); Hypothesis (real Codex) | P0 (same fix site as SEC-01) |
| SEC-05 | Approval is not bound to the verified worktree content | Medium | Confirmed | P1 |
| SEC-06 | Stored patch keeps the tail; early hunks vanish from review evidence | Medium | Confirmed | P1 |
| SEC-07 | Verification allowlist is not a boundary; planner-authored commands run unreviewed | Medium | Confirmed (parsing); Likely (planner path) | P2 |
| SEC-08 | Human-only approval relies on client compliance; Python API path is unguarded | Medium | Confirmed (design, partly documented) | P2 |
| SEC-09 | Environment scrubbing misses common credential carriers | Low | Confirmed | P2 |
| SEC-10 | "Over-broad path" guard accepts `**/*`; agent-instruction files are not sensitive | Low | Confirmed | P3 |

---

## SEC-01: Verification runs executor-authored code unsandboxed, after the scope check

- **Severity** High · **Confidence** Confirmed for out-of-scope and protected file writes;
  Likely for database tampering, credential reads and network exfiltration (not executed).
- **Location** `bridge/service.py:690` (diff taken), `:717` (scope check), `:722` (verification
  run), `:733-742` (decision); `bridge/review.py:141-166` (`run_verification`).
- **Root cause** The attempt pipeline is `agent run → diff → scope_violations → run_verification
  → decide`. Verification commands execute code the executor just wrote (tests, `conftest.py`,
  `Makefile`, `package.json` scripts, modules imported by tests) with the bridge's full user
  privileges, network access and no sandbox. Nothing is snapshotted after verification, so
  anything that code changes is never checked.
- **Impact** The Codex OS sandbox applies only while the agent runs; one test file in
  `allowed_paths` gives the same agent unsandboxed execution a few seconds later. Reproduced:
  verification code created `OUTSIDE.txt`, rewrote the protected `.env`, the attempt recorded
  `scope_violations: []`, and in a gates-only workflow the task closed itself as `DONE`.
  By the same privileges the code can read `~/.codex/auth.json` and other credential files, use
  the network, edit `~/.agent-bridge/projects/*/bridge.db` (task state, events, `needs_human`),
  or start a detached process that waits for the task to reach `APPROVED` and closes it through
  `Bridge.human_approve` (see SEC-08). Environment scrubbing does not help: these are files,
  not variables.
- **Preconditions** An executor that is malicious or prompt-injected (the threat the sandbox and
  the scope check exist for); any task whose `verification_commands` run project code, which is
  nearly every real task (`pytest`, `npm test`, `make test`).
- **Reproduction** `test_sec01_verification_mutation_is_unchecked` (fake Codex writes
  `src/check.py`; the task's verification command runs it).
- **Documented?** Partly: README "Verification commands run outside the Codex sandbox, with your
  permissions". The consequences (sandbox nullified, post-verification writes unchecked, approval
  state reachable) are not stated.
- **Fix (root cause, in order of cost)**
  1. Take a second snapshot after verification and compute scope violations over the union of
     the agent diff and the verification diff (anything verification changes outside caches is a
     violation). Cheap, catches file tampering in non-ignored paths.
  2. Kill each verification command's process group when it exits (detached children), and the
     agent's group when the agent exits (SEC-04).
  3. Optional, then default when probed: run verification inside the executor's sandbox. Codex
     ships a sandbox runner for arbitrary commands (`codex sandbox macos …` / `codex debug
     seatbelt` depending on version); this must be probed before relying on it, like every other
     CLI behaviour in `docs/REPORT.md`.
  4. Documentation: state that verification is trusted execution of executor-written code and
     that, for untrusted work, the reviewer should read the diff before verification runs.
- **Regression tests** Verification that writes an out-of-scope file → REWORK/BLOCKED with a
  violation naming the file; verification that writes a protected file → BLOCKED; verification
  that leaves a detached child → child killed, no late write.

## SEC-02: Scope violations are detected but not contained

- **Severity** High · **Confidence** Confirmed.
- **Location** `bridge/service.py:733-742` (violation → `REWORK` while budget remains);
  `bridge/config.py:107-111` and `bridge/orchestrator.py` loader (config read from the worktree on
  every start); `bridge/review.py:79` (the bridge's own config files are hashed, i.e. detected).
- **Root cause** A violation only changes task state. The out-of-scope file stays in the
  worktree, a protected-file violation is treated like any other rework, and every new `Bridge`
  (each CLI call, each MCP server start) re-reads `.ai/config.json` and `.ai/orchestrator.yaml`
  from that worktree. Codex `workspace-write` can write both (REPORT.md line 11 confirms `.ai/` is
  writable).
- **Impact** Reproduced: the executor rewrote `.ai/config.json` to
  `{"protected_paths": [], "verify_allowlist": ["sh","bash"]}`; the task went to `REWORK` with the
  correct violation, and `load_config()` (what the next process uses) returned the tampered
  values. The same applies to `.ai/orchestrator.yaml` (drop the `review` stage, enable
  `allow_bash`), `.mcp.json` (the MCP server command Claude Code starts next session; it is
  pre-enabled by name in `.claude/settings.json`), `.claude/settings.json` (hooks), and
  non-protected agent-instruction files (`CLAUDE.md`, `.claude/rules/*.md`, `.claude/skills/**`)
  that steer the orchestrating client which approves work. Detection depends on someone reading
  a rework reason and reverting by hand.
- **Preconditions** Malicious or injected executor; the client resumes or the user restarts
  without restoring the file.
- **Reproduction** `test_sec02_protected_write_persists_and_is_loaded`.
- **Fix**
  1. A violation on a protected path (and on the bridge's own config files) moves the task to
     `BLOCKED` with failure `PROTECTED: <paths>`, never `REWORK`, and the message tells the human
     to inspect and restore.
  2. `health` and bridge start report protected files that differ from `HEAD` (or from the
     hash recorded when a human last ran a config command), so a tampered config is visible
     before it is used. Refusing to start is the stricter variant; reporting is the compatible
     first step.
  3. Add agent-instruction and auto-executed files to `sensitive_paths` (human approval when
     touched): `CLAUDE.md`, `AGENTS.md`, `.claude/**`, `.codex/**`, `.envrc`, `.vscode/**`. Not to
     `protected_paths`, because legitimate tasks edit them.
- **Regression tests** Protected-file write → `BLOCKED`, never `REWORK`; `health` lists a dirty
  `.ai/config.json`; a task touching `CLAUDE.md` needs human approval.

## SEC-03: Writes to git-ignored paths are invisible to the snapshot

- **Severity** Medium · **Confidence** Confirmed.
- **Location** `bridge/review.py:51-64` (`git status --porcelain` omits ignored files),
  `:67-84` (only top-level protected globs and the bridge config are added back).
- **Root cause** The snapshot is "dirty or untracked, minus ignored". The `ponytail:` comment
  accepts this for nested secrets; it also means any file under an ignored directory
  (`.venv/`, `node_modules/`, `build/`, `dist/`, caches) can be created or changed without
  appearing in `changes.files`, the patch or the scope check.
- **Impact** Reproduced: `build/sitecustomize.py` written by the executor, task `DONE`, file not
  listed. Realistic abuse: a `.pth` file or patched package in `.venv/…/site-packages` executes
  at every Python start of that environment, including the bridge's verification run (SEC-01)
  and, when the target project is this kit, the bridge itself.
- **Preconditions** Malicious or injected executor; an ignored directory in the worktree.
- **Reproduction** `test_sec03_ignored_paths_invisible`.
- **Fix** Record ignored files by `(path, size, mtime_ns)` (`git ls-files --others --ignored
  --exclude-standard -z` plus `stat`, no hashing) before and after a run; list changes as
  `changes.ignored_files` evidence shown to every reviewer, and treat changes outside
  `allowed_paths` as violations except a short built-in list of cache patterns
  (`**/__pycache__/**`, `.pytest_cache/**`, `.mypy_cache/**`, `.ruff_cache/**`). Measure the cost
  on a large `node_modules` before making enforcement default; start with evidence only.
- **Regression tests** Write under an ignored directory → listed in evidence; outside allowed
  paths and not a cache → violation; `__pycache__` noise → no violation.

## SEC-04: Processes left behind by the agent keep writing after the snapshot

- **Severity** Medium · **Confidence** Confirmed with a wrapper around the fake CLI;
  Hypothesis for real Codex/Claude (whether their tool processes stay in the agent's process
  group, or call `setsid`, was not probed).
- **Location** `bridge/executor.py:314-319`: the group is killed only when the watchdog fires
  (`timeout`/`cancelled`); a normal exit leaves the group alone.
- **Root cause** Process-group cleanup is tied to abnormal termination only.
- **Impact** Reproduced: a background child wrote `LATE_OUTSIDE.txt` two seconds after the task
  was already `DONE`; it is in no evidence. Combined with SEC-05, a late write lands after review.
- **Reproduction** `test_sec04_lingering_child_writes_after_snapshot`.
- **Fix** After `proc.wait()` returns, always `kill_group(proc)` (SIGTERM, grace, SIGKILL)
  before `diff()`; same in `run_verification` after each command. A double-forked `setsid`
  child escapes the group; the post-verification snapshot (SEC-01 fix 1) and approval binding
  (SEC-05) bound what such a child can achieve unnoticed.
- **Regression tests** Fake agent leaves a sleeping child → child is gone after the run, no late
  file appears.

## SEC-05: Approval is not bound to the verified worktree content

- **Severity** Medium · **Confidence** Confirmed.
- **Location** `bridge/service.py:834-856` (`_assert_gates`, `_close`, `human_approve` check only
  database records); `bridge/review.py:98-105` (the evidence stores file names, stat and patch, not
  the post-run hashes).
- **Root cause** "Quality gates re-checked" means re-reading the stored verdict. Nothing compares
  the worktree at approval time with what was verified.
- **Impact** Reproduced: a high-risk task reached `APPROVED`, `src/app.py` was then replaced, and
  `human_approve` closed it `DONE`. In normal use the gap is opened by the client or user editing
  files, by a later task in the same worktree (allowed once the first leaves `RUNNING/VERIFYING`),
  by SEC-04 children, or by SEC-01 code. The human approves a diff that is no longer the content.
- **Reproduction** `test_sec05_approval_not_bound_to_worktree`.
- **Fix** Store `after_hashes` (path → sha256 or `null` for deleted) for every changed file and
  `head` in the attempt's `changes`; on client/agent approve and on `human_approve`, recompute
  those hashes and refuse with `worktree changed since verification (files: …); resume to
  re-verify` on mismatch. Binding to the task's own files (not the whole tree) keeps sequential
  workflows working when later tasks touch other files.
- **Regression tests** Edit a verified file between REVIEW and approve → refused; edit an
  unrelated file → allowed; delete a verified file → refused.

## SEC-06: Stored patch keeps the tail; early hunks vanish

- **Severity** Medium · **Confidence** Confirmed.
- **Location** `bridge/review.py:104` (`redact(patch, PATCH_LIMIT)`), `bridge/security.py:135`
  (`redact` returns `"…" + text[-limit:]`), `bridge/executor.py:697-698` (reviewer prompt).
- **Root cause** `redact` was designed for logs, where the tail is most diagnostic; it is reused
  for patches, where it silently drops the first files in path order.
- **Impact** Reproduced: a small `a_evil.py` plus 48 KB of `z_padding.txt` → `a_evil.py` absent
  from the stored patch; the only signal is a leading `…`. A reviewer agent judges a patch it did
  not see; `.claude/rules/delegation.md` tells a client reviewer to read files when truncated,
  but nothing marks the truncation explicitly.
- **Reproduction** `test_sec06_patch_truncation_hides_first_file`.
- **Fix** Truncate per file with a per-file budget, keep the head of each file's hunks, and
  record `patch_truncated: true` and `patch_omitted_files: [...]`; the reviewer prompt lists the
  full changed-file list and says which files are not shown.
- **Regression tests** Large later file → earlier file's hunk still present; omitted files
  listed; flag set.

## SEC-07: Verification allowlist is not a boundary

- **Severity** Medium · **Confidence** Confirmed for parsing; Likely for the planner path.
- **Location** `bridge/review.py:130-138` (basename check), `bridge/config.py:61-64` (default
  allowlist), `bridge/service.py:931-952` (planned tasks created from planner output),
  `:971-982` (`run_workflow` runs them).
- **Root cause** The allowlist checks `os.path.basename(argv[0])`, so `/any/dir/pytest` passes;
  the default list contains interpreters and package runners (`python3 -c`, `node -e`,
  `npx --yes <package>`, `uv run --with`, `make`, `git -c`), each of which runs arbitrary code.
  Commands from a planner agent (read-only, and fed repository content that may carry
  injection) go straight from `bridge_plan` to `bridge_run_workflow`, unsandboxed.
- **Impact** A read-only planner can obtain user-privileged execution through the commands it
  plans. The allowlist gives a sense of control it does not provide.
- **Reproduction** `test_sec07_allowlist_is_not_a_boundary`.
- **Fix** Reject `argv[0]` containing a path separator (resolve bare names through `PATH`
  only); document that allowlisted interpreters make verification commands trusted input;
  show planned verification commands in `bridge_workflow_status` and in the skill's review step
  before `bridge_run_workflow`. Sandboxed verification (SEC-01 fix 3) is the real control.
- **Regression tests** `/x/pytest` rejected; bare `pytest` accepted; planned commands visible
  in workflow status.

## SEC-08: Human-only approval relies on client compliance

- **Severity** Medium · **Confidence** Confirmed (design; the PTY bypass is documented).
- **Location** `bridge/cli.py:28-34` (TTY + typed id), `bridge/service.py:850-856`
  (`human_approve` has no guard of its own), `.claude/settings.json` deny list.
- **Root cause** The only enforcement points are a TTY check in the CLI and glob deny rules in
  Claude Code settings. `uv run python -c "from bridge.service import Bridge;
  Bridge().human_approve('T0001')"` matches no deny rule and needs no TTY; any same-user process
  can also write the SQLite file. Verification code (SEC-01) is such a process. The deny rule
  `Bash(*sqlite3*)` also blocks harmless commands such as `grep sqlite3.connect` (observed during
  this audit), a usability cost for little protection.
- **Impact** "Human approval" is a workflow convention, not an access control, against anything
  running as the user. Acceptable for the stated local-tool model, but the documents present it
  more strongly than the code enforces.
- **Fix** Documentation first: state that the approval gate protects against honest mistakes and
  model overreach, not against code running as the user. Close the bridge-started paths: SEC-01
  fixes 1-3 and SEC-05 binding. A stronger gate (an approval secret readable only by a separate
  OS user or a hardware prompt) is out of proportion for this project and not recommended now.
- **Regression tests** None beyond SEC-01/SEC-05; a doc test is not useful.

## SEC-09: Environment scrubbing misses common credential carriers

- **Severity** Low · **Confidence** Confirmed.
- **Location** `bridge/config.py:76`, `bridge/security.py:138-143`.
- **Root cause** A denylist of name globs. `DATABASE_URL` (embedded password), `SSH_AUTH_SOCK`
  (live SSH agent), `KUBECONFIG`, `GOOGLE_APPLICATION_CREDENTIALS`, `*_DSN` pass through to agents
  and verification.
- **Impact** Under Codex the sandbox blocks network, so the practical exposure is verification
  (SEC-01) and Claude with `allow_bash`.
- **Fix** Extend the default denylist with `SSH_AUTH_SOCK`, `*_URL` containing `DATABASE`,
  `*_DSN`, `*CREDENTIALS*`, `KUBECONFIG`, `DOCKER_AUTH_CONFIG`; consider an allowlist mode later.
- **Regression tests** `clean_env` drops each new pattern; `env_allow` still re-admits.

## SEC-10: Over-broad path guard and agent-instruction files

- **Severity** Low · **Confidence** Confirmed.
- **Location** `bridge/security.py:98`.
- **Root cause** Only `.`, `*` and `**` are rejected; `**/*` (which matches every
  non-protected file) is accepted.
- **Fix** Reject a pattern whose regex matches both `a` and `a/b/c.x`. Agent-instruction files are
  handled in SEC-02 fix 3.
- **Regression tests** `**/*` and `**/**` rejected; `src/**` accepted.

---

## Controls verified as sound

These were traced and hold for the cases examined:

- Subprocesses start from argv lists only; no `shell=True` anywhere in `bridge/`.
- Task path validation rejects absolute paths, `~`, `..`, control characters, symlinked prefixes
  escaping the root, and protected paths (`bridge/security.py:77-100`).
- The transition table enforces actors; `CLIENT` cannot reach `DONE` on a `needs_human` task
  (`bridge/models.py:38-69`), and MCP exposes no approve-as-human tool (`bridge/server.py`).
- Claude writes are scoped through `Edit(./path)` rules; patterns that could smuggle extra rules
  (`,()"'` and whitespace) are refused (`bridge/security.py:45-48`, `bridge/executor.py:425-447`).
- Stale-process killing checks pid and start time (`bridge/executor.py:637-644`).
- The dashboard binds to 127.0.0.1, checks `Host` (DNS rebinding), requires a per-run token on
  `/api/*` compared with `hmac.compare_digest`, allows only GET/HEAD, and sends `nosniff`,
  `no-store`, `no-referrer` and a `default-src 'self'` CSP (`bridge/webui.py`). The token travels
  in the URL query and so lands in browser history: acceptable for a local tool.
- State lives outside the worktree (`~/.agent-bridge/projects/…`, 0700 dirs, 0600 files), so the
  Codex sandbox cannot forge it; the bridge home refuses symlinks, foreign owners and locations
  inside the worktree (`bridge/storage.py:24-34`).

## Not verified

- Real Codex/Claude process-group behaviour for tool subprocesses (SEC-04).
- Whether `codex sandbox` can run verification commands with the same policy (SEC-01 fix 3).
- Whether Claude Code's `Edit(./src/**)` rule resolves symlinks before matching (a symlink inside
  `src/` pointing at a protected file). Protected top-level files would still be caught by the
  hash snapshot; nested ignored ones would not (SEC-03).
- Windows paths (`osutil` has Windows branches; the test suite ran on macOS only).
