# Plan: parallel writing tasks via per-task git worktrees

Status: **draft, for review** · Branch: `main` (`develop` no longer exists) · Nothing here is
implemented. Line numbers are from an earlier revision; locate code by symbol name.

## 1. Problem

Today a project has exactly one writer. Three places enforce it:

| Where | What it does |
|---|---|
| `Store.transition` (`bridge/state.py:307`) | refuses RUNNING when another `workspace-write` task is active in the store: `"already writing in this worktree"` |
| `Bridge._drive` (`bridge/service.py:926`) | runs a workflow's tasks one at a time (`# ponytail: tasks run one at a time`) |
| `Bridge.root` | a single path used by the adapters (`cwd`, `-C`), `snapshot`/`diff`, `run_verification`, attempt logs |

README lists "parallel writing tasks in one worktree" as unsupported and says to "use separate
worktrees, each with its own bridge state". That works by hand but gives no scheduling, no merge,
and no shared workflow. A workflow of N independent tasks takes N sequential runs.

## 2. Goal and non-goals

**Goal.** Opt-in: a workflow runs independent tasks concurrently, each in its own git worktree,
and the results are combined on a branch the human merges.

**Non-goals (v1).**
- No automatic merge into the user's branch, no push (human-only, same as `approve`).
- No conflict resolution by agents. Tasks that may conflict are not run in parallel.
- No change for users who do not opt in: default stays single-writer, byte-for-byte.
- No new dependencies (`git worktree` via subprocess, as `install.py` already does for `git`).

## 3. Design

### 3.1 Isolation mode (opt-in)

```yaml
workflow:
  isolation: worktree     # default: none (today's behaviour)
  max_parallel: 3         # default: 1
```

With `isolation: none`, none of the code below is reached.

### 3.2 One state store, many working directories

State stays in the **main** worktree's `.git/agent-bridge/bridge.db` (one dashboard, one audit
log, one set of account slots). A task row gains `workdir` and `branch`. Everything that used
`self.root` for a *task's* files takes `row["workdir"] or self.root`:

- `RunRequest` gets `cwd`; both adapters use it instead of `self.root` (`executor.py:250`, `:359`).
- `snapshot` / `diff` / `run_verification` (`service.py:576`, `:627`, `:659`) and
  `_attempt_log_path` get the task root.
- The single-writer check in `transition` is keyed by `workdir` instead of "any active task".
  The per-account limit is unchanged (`flock` slots already work across processes).
- `self.root` keeps meaning "the project": config, `.ai/`, state dir, protected paths.

### 3.3 Worktree lifecycle

| Event | Action |
|---|---|
| first `execute` of a task | `git worktree add <dir> -b bridge/<task-id> <base>` |
| DONE | merge into the integration branch (3.5), then remove the worktree, keep the branch |
| CANCELLED | remove the worktree and branch |
| BLOCKED / FAILED / PAUSED / REWORK | **keep** (the session and the evidence live there) |
| bridge start (`recover`) | list `git worktree list` entries named `bridge/*` with no live task; report, never delete automatically |

`<base>` is the integration branch head, so a dependent task sees its dependencies' work.
Location: `.ai/worktrees/<task-id>` (the directory is already git-ignored by `.ai/.gitignore`,
stays inside the repository per the project-isolation rule). **To confirm in the spike (4.0).**

### 3.4 Scheduling

`_drive` becomes a bounded loop: pick READY/REWORK tasks, start up to `max_parallel` at once,
await any, repeat. A task is startable only if:
1. all `depends_on` are DONE **and merged** (3.5);
2. its `allowed_paths` are **disjoint** from every running task's (glob overlap test; unknown or
   ambiguous overlap counts as overlapping);
3. its account has a free slot (existing `AccountLock`).

Rule 2 is what makes merges conflict-free by construction. Tasks that fail it simply wait; they
are not an error.

### 3.5 Integration branch

One branch per workflow, `bridge/wf-<id>`, checked out in its own worktree
(`.ai/worktrees/wf-<id>`). When a task reaches DONE the bridge `git merge --no-ff bridge/<task-id>`
there. Because of rule 2 a textual conflict should not happen; if it does the merge is aborted,
the task goes to BLOCKED with the conflict list, and the human decides.

Semantic conflicts (both tasks compile alone, not together) are not caught by disjoint paths.
After each merge the bridge re-runs the **union of the verification commands** of merged tasks
on the integration worktree; a failure blocks the workflow with the evidence.

The human then runs `git merge bridge/wf-<id>` (or opens a PR) themselves. The kit never touches
the user's checked-out branch and never pushes.

### 3.6 Security and gate consequences

- Scope check, `HEAD` unchanged check and protected-path denies run **per task worktree**, same
  functions, different root. A linked worktree's git dir lives under the main `.git/`, outside the
  task worktree, so the sandbox rule "state under `.git/` is not writable" still holds (**spike**).
- `risk_level: high` / sensitive-path tasks still stop at APPROVED; a high-risk task is never
  merged into the integration branch before `agent-bridge approve`.
- Claude CLI has no OS sandbox. Scoped `Edit(./src/**)` rules are relative to cwd, so they follow
  the task worktree, but this must be re-probed (the old probe showed unscoped Write escaping).
- Every worktree is project-local; no global config is read or written.

## 4. Work breakdown

Each item is one reviewable bridge task (rule: <= ~400 changed lines, verified by
`scripts/check.sh`). Order matters because of single-writer: they run sequentially.

| # | Task | Risk | Depends on |
|---|---|---|---|
| 0 | **Spike (read-only, no code merged).** In a throwaway repo: (a) Codex `workspace-write` in a linked worktree under `.ai/worktrees/`, can it write inside, can it write the main `.git/`; (b) does the `.git` path component in an ancestor trigger Codex's protected-dir rule; (c) Claude CLI scoped `Edit` in a linked worktree; (d) `git worktree add/remove` with a dirty or missing dir. Result goes into this file. | low | — |
| 1 | `RunRequest.cwd` + adapters use it; `Bridge._root_of(row)` helper; thread it through `snapshot`/`diff`/`run_verification`/logs. Behaviour unchanged when `workdir` is empty. | medium | 0 |
| 2 | DB migration: `workdir`, `branch` columns; `transition` single-writer keyed by `workdir`. Existing DBs upgrade on first start. | **high** (state machine) | 1 |
| 3 | `worktrees.py`: create / remove / list / prune; config keys `isolation`, `max_parallel`; schema validation in `config-check`. | medium | 0, 2 |
| 4 | Wire lifecycle into execute / done / cancel / `recover`. | medium | 3 |
| 5 | Parallel `_drive` + path-overlap test (`security.py`, unit-tested with tricky globs: `*`, `**`, nested, same dir). | **high** (scheduler, gates) | 4 |
| 6 | Integration branch: merge on DONE, union re-verification, BLOCKED on conflict. | **high** | 5 |
| 7 | CLI/TUI: `workdir`/`branch` in `tasks` and the detail pane; `agent-bridge worktrees` (list, prune). Docs: README (+`README.vi.md`), `.claude/rules/orchestration.md` ("One writer per git worktree" rewrite), CHANGELOG. | low | 6 |

Items marked high-risk touch the state machine, scope gates or merge paths: they get
`risk_level: high` and a human `agent-bridge approve`.

## 5. Tests

- **Unit** (`tests/unit`): overlap test; `transition` allows two writers with different `workdir`
  and still refuses two with the same; migration of a pre-change DB.
- **Integration** (`tests/integration`, fake agents from `tests/fakes`): two disjoint tasks run
  concurrently (assert overlapping RUNNING intervals) and both merge; two overlapping tasks run
  one after the other; a dependent task starts from the integration head and sees its
  dependency's file; forced merge conflict -> BLOCKED with the file list; cancel removes worktree
  and branch; `recover()` after a kill reports the orphan and deletes nothing.
- **Regression**: the whole existing suite with `isolation: none` must pass unchanged.
- **E2E (manual, needs real CLIs)**: one 3-task workflow on Codex, run once before release.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Codex treats a path containing `.git` as protected, or cannot write in a linked worktree | spike 0 decides the location; fallback is a sibling directory, which needs an explicit exception to "everything lives in the repository" |
| Disjoint paths still produce semantically broken merges | union re-verification after each merge (3.5) |
| Account slots exhausted: parallelism silently equals 1 | status shows "waiting: account slot"; README says `max_concurrency` bounds real parallelism |
| Disk and time cost of N checkouts (large repos) | opt-in; `max_parallel` default 1; README note |
| Orphan worktrees/branches after crashes | report in `recover` and `worktrees` command; never auto-delete |
| Hooks (`pre-commit`, `post-merge`) fire on bridge merges | run merges with `-c core.hooksPath=/dev/null`; to be confirmed in task 6 |
| Complexity creep in `service.py` (already ~1000 lines) | new logic lives in `worktrees.py`; `service.py` only calls it |

## 7. Decisions needed from you

1. **Worktree location**: `.ai/worktrees/` (recommended, in-repo, ignored) or a sibling directory
   outside the repo?
2. **Merge into the user's branch**: keep human-only (recommended), or add an opt-in
   `agent-bridge integrate --into <branch>` that fast-forwards after the human confirms?
3. **Overlap rule**: strict disjoint `allowed_paths` (recommended, simple) or also allow overlap and
   rely on git to merge?
4. **Scope of v1**: executor tasks only (recommended); parallel read-only planner/reviewer runs
   already work and are untouched.

## 8. Out of scope here

Checkpoint/rewind before each run and token budgets (the other two gaps from the SwarmPane
comparison) are separate plans; checkpoints can reuse the worktree machinery later.

## 9. Execution note

Per `CLAUDE.md`, implementation is delegated through `bridge_create_task` (one task per row in
section 4) and reviewed against the bridge's own verification output. The `agent-bridge` MCP server
timed out on connect in the planning session; confirm it connects (`claude mcp list`) before
starting.
