# Engineering roles (this repository)

This file is not shipped by `agent-bridge init`. Keep the shipped rules
(`orchestration.md`, `delegation.md` and `security.md`) generic.

Decisions, task specs and reviews in this kit are judged from the roles below. They are review
lenses, not agents to spawn. Before writing a spec or a review, pick the roles the change touches
(usually 3 to 5) and apply their checks. State a decision in a role's terms only when it changed
the outcome. When roles conflict, Security wins, then correctness (Distributed Systems and QA),
then the rest.

| # | Role | Owns | Checks in this kit |
|---|---|---|---|
| 1 | Principal Software Architect | Boundaries, modules, design decisions | One owner per setting (`accounts.yaml`, `orchestrator.yaml`, `config.json`). Bridge, CLI, MCP, TUI and web share `Bridge`; no second path to the same state. Reuse an existing helper before adding one. |
| 2 | Principal Agent Systems Architect | Agent lifecycle, orchestration, state machine | Transitions match `.claude/rules/orchestration.md`. The bridge, not an agent, decides REVIEW vs REWORK. Attempts, sessions and pins survive resume and recovery. |
| 3 | Senior Software Engineer | Implementation, code quality, maintainability | Smallest coherent diff; follows `bridge/` patterns (VIEWS, HUMAN_ACCOUNT_CMDS, atomic writes). No dead code, no silenced checks. |
| 4 | Distributed Systems Engineer | Concurrency, retries, locking, consistency | One writer per worktree; account slots via `AccountLock`. Config or state swaps never happen mid-run. Writes are atomic (tmp + `os.replace`). Several bridges and processes share `~/.agent-bridge`. |
| 5 | Security Engineer | Sandbox, permissions, secrets, isolation | Rules in `.claude/rules/security.md`. Human-only commands stay TTY-gated. No secret in `.ai/`, argv, logs or output. `.ai/config.json` changes are security changes. Claude CLI Bash is not path-confined, so prefer Codex for untrusted or security-sensitive work. |
| 6 | QA / Test Architect | Test strategy, regression, reliability | Every behaviour change has a test with the `# @pt.*` block. Tests that need a project use the `repo` fixture. Tests that list or run agents use `fake_codex` / `fake_claude`. The gate is the full suite (`uv run --frozen pytest -q -p no:cacheprovider`), including `test_docs_sync`. The executor can run the tests, or the spec says how the agent checks its work. An agent that runs pytest itself must pass `--basetemp` outside the repository, because the default `.pytest_tmp` inside the worktree counts as out-of-scope changes. Codex cannot run `uv` in its sandbox, so only the bridge's checks run there. Every spec tells an agent that cannot run tests to report `completed` and list what it could not check: the bridge runs the gate, so `blocked` for that reason only burns an attempt. |
| 7 | SRE / Reliability Engineer | Observability, recovery, availability | Failures surface in `health` with a fix line. Store events are logged for state changes. A bad config or a dead runner degrades to PAUSED or NOT OK, never a crash or silent stale state. |
| 8 | Developer Experience Engineer | CLI, MCP tools, onboarding, workflow | Every edit a user makes in `.ai/` has a CLI command. A terminal gets a readable view and a pipe gets JSON. Help text matches behaviour. Error messages name the file and the command that fixes them. |
| 9 | AI/LLM Systems Engineer | Adapters, context, token economics, tool calling | Adapter flags verified against the probed CLI versions. Prompts and task specs carry only what the executor cannot discover cheaply. The account and model are chosen for the work and the remaining quota. |
| 10 | Product / UX Architect | Workflows, dashboard, information architecture | The dashboard shows true state (live work vs stopped, orphaned). Design from measured use on the real app, not references. The web UI stays read-only unless asked. |
| 11 | Performance Engineer | Latency, throughput, resources | MCP tools stay off the event loop (`asyncio.to_thread`). Per-call work is cheap: hashing small files, cached reports. No unbounded scans of the state store or of every project on hot paths. |
| 12 | Technical Writer / Documentation Architect | Docs, references, guides | `.claude/rules/docs-sync.md`: docs change in the same PR. README.md and README.vi.md change together. Claims in docs are verified behaviour. |

Use in the workflow:
- **Spec:** list the roles the change touches in your own notes. Add each one's checks to `context`, `scope` or `acceptance_criteria` where they apply.
- **Review:** walk the same roles over `bridge_task_result`, in the order from `delegation.md`.
- **Report:** when the user asks for a decision, give it per role in one table row each, and only for the roles that decided something.
