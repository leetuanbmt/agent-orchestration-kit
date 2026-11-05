# Plan: per-account quota limits and automatic failover

Status: implemented on `tuanvm/per-account-quota-limits` (branched from `main`). User-facing
reference: [configuration.md](../configuration.md#per-account-limits-opt-in).

## Audit: what already existed

| Concern | Where | State before this change |
|---|---|---|
| Provider usage | `quota.py` (`read_quota`, `remaining_pct`), `ClaudeAdapter.handle` (`rate_limit_event`) | Codex rollouts and Claude's last-run windows, reduced to one `remaining_pct = 100 - max(used)` |
| Unavailable marks | `Store.set_account_state`, shared `accounts.db` | QUOTA/AUTH marks shared machine-wide; a QUOTA mark lifts after a window resets with a snapshot |
| Concurrency | `AccountLock` (`flock` slots), `Store.transition` | Cross-process slots per account; one writer per worktree |
| Routing | `_pick`, `_unavailable`, `_route` | First eligible agent in role order; unpinned tasks bind at first run |
| Failover | `_quota_fallback`, `routing.quota_fallback` | Opt-in, one global `min_remaining_pct`; no per-window threshold, no loop guard |
| Cost | `RunResult.usage` | Token counts stored per attempt; no cost, no budget |

Gaps against the requirement: no per-account thresholds, no 5-hour/weekly distinction, no USD
budget, no reservation (two bridges could both start the last affordable run), no failover loop
guard, no cooldown for a QUOTA mark that has no readable reset time, no usage/limit report.

## Design

1. **Config** (`orchestrator.py`): optional `accounts.<name>.limits` (`AccountLimits`). `None`
   keeps today's behaviour. Lives in `orchestrator.yaml` or the machine-wide registry; both are
   human-owned, so agents cannot loosen a limit.
2. **Evaluation** (`quota.py`, pure): windows are classified by `window_minutes` (300 = five
   hour, 10080 = weekly); `any_window_pct` applies to every reported window. A window whose
   `resets_at` has passed counts as 0 % used, so recovery after reset needs no stored state.
   Breach = `used >= threshold`.
3. **Usage ledger** (`state.py`, shared `accounts.db`, keyed by `account_key`): one row per run
   with its cost and source (`actual`, `estimated`, `unavailable`). Budgets use UTC calendar
   periods (day, ISO week from Monday, month). A run on a budgeted account first reserves
   `reserve_usd` inside `BEGIN IMMEDIATE`, so concurrent bridges serialise on the check. A
   reservation whose owner process died is charged at its reserved amount, or as unknown cost
   when it reserved nothing.
4. **Sources**: provider-reported cost (`total_cost_usd` from Claude, `cost` from opencode) is
   `actual`; tokens times configured `pricing` is `estimated`; otherwise `unavailable`. No price
   table ships with the kit. Unknown cost inside a budget period blocks that account until the
   period ends (fail closed). Percentage windows the provider never reported are `unavailable`
   and do not block: the provider's own hard stop still pauses the task as QUOTA.
5. **Enforcement**: `_unavailable` (routing skips breached accounts, and the "no eligible agent"
   error lists every reason), `_start` (authoritative gate after the slot lock, with the
   reservation), `_consult` (planner and reviewer runs).
6. **Failover**: `_quota_fallback` also fires on a limit breach; it stays behind
   `routing.quota_fallback.enabled` (moving a bound task is the user's opt-in). `max_switches`
   caps moves per task; `keep_sessions` refuses to move a task that has a session.
7. **Cooldown**: `limits.cooldown_s` lifts a QUOTA mark after that long when no reset time is
   readable.
8. **Observability**: `limits` report per account in `bridge_list_accounts` / `account-list`,
   `agent-bridge usage`, `limit_blocked` and `usage_recorded` events, health summary.

## Out of scope

Machine-wide concurrency caps and the gaps in `multi-bridge-accounts.md`; killing a run
mid-way when its cost passes the budget (CLIs report cost only at the end); editing limits
from a UI (that would write `.ai/`, which is human-owned).
