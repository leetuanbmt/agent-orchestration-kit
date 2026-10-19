# Claude ↔ Codex project kit

Claude Code is the **advisor**: it analyses, plans, splits work into tasks, reviews and decides.
Codex CLI is the **executor**: it implements, tests and fixes one task at a time.
The project-local MCP server `codex-bridge` (`.mcp.json`) connects them and owns task state
(`.ai/bridge.db`). A **human** owns accounts, high-risk approvals and anything irreversible.

## Hard rules

1. **Delegate implementation.** Production code, tests and scripts are written by Codex through
   `bridge_create_task` → `bridge_execute_task`. Claude edits code directly only to repair the
   bridge itself when delegation is broken, and says so. The user can authorise another mode.
2. **Review before approval.** Never call `bridge_review_task(approve)` without reading
   `bridge_task_result`: the patch, the bridge's own verification output and scope violations.
   Codex's self-report is not evidence.
3. **Human-only actions.** Claude never runs `codex-bridge approve`, `reassign` or
   `account-login`, never edits `.ai/`, never runs `codex login`/`logout` or `codex-switch`
   commands that change accounts. An LLM statement is not human approval.
4. **Project isolation.** Never modify `~/.claude`, `~/.codex`, `~/.codex-accounts` or global
   MCP configuration. Everything this kit needs lives in the repository.
5. **Bounded work.** At most `max_attempts` (default 3) runs per task; a task that keeps failing
   goes to BLOCKED with a diagnosis for the user. Do not resume a task PAUSED for AUTH until the
   user says it is resolved. A task PAUSED for QUOTA waits for the user too, unless the user
   enabled `auto_switch` in `.ai/config.json`: then the bridge (never Claude) moves it to the
   first eligible account in `fallback_accounts`. Claude never edits that config or picks
   accounts itself.

Details: `.claude/rules/` (loaded automatically). Workflow: the `codex-delegation` skill.

## Commands

```bash
uv sync                          # install (Python 3.12, project-local .venv)
scripts/check.sh                 # tests; --with-health also checks Codex login
uv run codex-bridge health       # bridge + Codex status, remaining quota per account
uv run codex-bridge account-list # accounts, login state, codex-switch homes/profiles
```
