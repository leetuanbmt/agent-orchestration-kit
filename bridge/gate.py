"""Account gate: whether an account can take work, its quota and limits, and task routing."""

from __future__ import annotations

import contextlib
import copy
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from .accounts import AccountLock, account_key, login_status
from .executor import RunResult
from .models import Actor
from .orchestrator import AccountLimits, AgentConfig
from .quota import BUDGET_PERIODS, effective_account_state, evaluate_limits, period_bounds, \
    run_cost
from .security import redact, rule_unsafe
from .state import ConflictError, LimitExceeded

if TYPE_CHECKING:
    from .service import Bridge
    from .state import Store

# How long a limit report is reused by routing and listings (the start gate never reuses one).
LIMIT_REPORT_TTL_S = 2.0


class AccountGate:
    """Account state, quota, limits and the usage ledger, and the routing built on them.

    Config, store, account resolution and provider quota (`_account_quota`) come from the
    Bridge, so a method replaced on the Bridge instance applies here too."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        # (name, reserve) -> (monotonic time, store usage revision, report)
        self._reports: dict[tuple[str, float], tuple[float, int, dict[str, Any]]] = {}

    @property
    def store(self) -> Store:
        return self.bridge.store

    # ---- account state ---------------------------------------------------------------------

    def _effective_account_state(
            self, name: str, *, key: str | None) -> tuple[dict[str, Any], str | None]:
        """The account state with a lapsed QUOTA mark counted as available; writes nothing."""
        state = self.store.account_state(name, key=key)
        if state["state"] != "quota":
            return state, None
        configured = self._limits(name)
        cooldown = configured.cooldown_s if configured is not None else None
        return effective_account_state(state, self.bridge._account_quota(name), cooldown,
                                       time.time())

    def _refresh_account_marks(self, name: str, *, key: str | None) -> dict[str, Any]:
        """The account state before work starts on it; the only place that stores a lapsed
        QUOTA mark as available (shared with the other projects using the account)."""
        state, lifted = self._effective_account_state(name, key=key)
        if lifted is None:
            return state
        self.store.set_account_state(name, "available", actor=Actor.BRIDGE, key=key)
        if lifted == "cooldown":
            limits = self._limits(name)
            self.store.log(None, Actor.BRIDGE, "account_cooldown_elapsed",
                           {"name": name, "cooldown_s": limits.cooldown_s if limits else None})
        return self.store.account_state(name, key=key)

    def _remaining(self, name: str) -> float | None:
        quota = self.bridge._account_quota(name)
        return quota.get("remaining_pct") if isinstance(quota, dict) else None

    # ---- limits and the usage ledger -------------------------------------------------------

    def _limits(self, name: str) -> AccountLimits | None:
        profile = self.bridge.orch.accounts.get(name)
        return profile.limits if profile is not None else None

    def _usage_key(self, name: str) -> str:
        """Ledger key: the machine-wide account identity, else the name in this project."""
        return self.bridge._key(name) or f"name:{name}"

    @staticmethod
    def _period_starts(now: float) -> dict[str, float]:
        return {p: period_bounds(p, now)[0] for p in BUDGET_PERIODS}

    def _rate_limits(self, name: str) -> dict[str, Any] | None:
        quota = self.bridge._account_quota(name)
        if not isinstance(quota, dict):
            return None
        return {"primary": quota.get("primary"), "secondary": quota.get("secondary")}

    def _evaluate(self, name: str, reserve: float = 0.0, *, expire: bool = True) -> dict[str, Any]:
        now = time.time()
        spend = self.store.usage_spend(self._usage_key(name), self._period_starts(now),
                                       expire=expire)
        return {**evaluate_limits(self._limits(name), self._rate_limits(name), spend, now,
                                  reserve), "spend": spend}

    def _next_run_reserve(self, name: str) -> float:
        """What the next run on `name` would reserve against its budget (0 without a budget).
        Routing, the start gate and every view judge the account with it, so they agree."""
        limits = self._limits(name)
        return limits.reserve_usd if limits is not None and limits.budget_usd else 0.0

    def _limit_report(self, name: str, reserve: float = 0.0) -> dict[str, Any]:
        """Limit checks with spend for routing and listings, reused for LIMIT_REPORT_TTL_S
        unless this process wrote the ledger or quota meanwhile. Not the start gate: _reserve."""
        key, revision = (name, reserve), self.store.usage_revision
        cached = self._reports.get(key)
        if (cached is not None and cached[1] == revision
                and time.monotonic() - cached[0] < LIMIT_REPORT_TTL_S):
            return copy.deepcopy(cached[2])
        report = self._evaluate(name, reserve)
        # The read may have settled a dead owner's reservation: key on the revision after it.
        self._reports[key] = (time.monotonic(), self.store.usage_revision, report)
        return copy.deepcopy(report)

    def _reserve(self, name: str, task_id: str | None) -> int | None:
        """Authoritative limit gate before a run: the open reservation id on a budgeted account.

        Raises LimitExceeded with the reason when the account is over one of its limits."""
        found = self._limits(name)
        if found is None or not found.enabled:
            return None
        limits: AccountLimits = found
        now = time.time()
        rate_limits = self._rate_limits(name)

        def check(spend: dict[str, dict[str, Any]]) -> str | None:
            return evaluate_limits(limits, rate_limits, spend, now, limits.reserve_usd)["reason"]

        if limits.budget_usd is None:
            if reason := check({}):
                raise LimitExceeded(reason)
            return None
        return self.store.reserve_usage(name, key=self._usage_key(name), task_id=task_id,
                                        reserve=limits.reserve_usd,
                                        starts=self._period_starts(now), check=check)

    def _settle_open(self, usage_id: int | None, cost: float | None, source: str) -> None:
        """Settle a reservation that is still open; a settled one is left as it is."""
        if usage_id is not None:
            with contextlib.suppress(Exception):
                self.store.settle_usage(usage_id, cost, source)

    def _record_account(self, name: str, res: RunResult, usage_id: int | None = None,
                        task_id: str | None = None) -> bool:
        """Record a run's account marks and cost; False when the ledger write failed."""
        if res.rate_limits:
            self.store.save_quota(name, res.rate_limits, key=self.bridge._key(name))
        # Runs only start on an available account, so a mark seen after a successful run came from
        # another run meanwhile. account-clear lifts marks; a quota mark may also lapse
        # once a window open at mark time has reset and remaining quota is positive.
        if res.outcome in {"quota", "auth"}:
            self.store.set_account_state(name, res.outcome, res.error, actor=Actor.BRIDGE,
                                         key=self.bridge._key(name))
        limits = self._limits(name)
        cost, source = run_cost(res.cost_usd, res.usage, limits.pricing if limits else None)
        if res.outcome == "missing_binary":
            cost, source = 0.0, "actual"  # nothing ran
        elif res.outcome in {"auth", "quota"} and res.cost_usd is None and not res.usage:
            cost, source = 0.0, "estimated"  # the provider refused the request
        elif cost is None and limits is not None and limits.reserve_usd > 0:
            cost, source = limits.reserve_usd, "estimated"
        try:
            if usage_id is not None:
                self.store.settle_usage(usage_id, cost, source)
            else:
                # Gated: charged against a budget (a network-retried attempt on a budgeted
                # account), so an unknown cost blocks it as an unsettled reservation would.
                gated = (limits is not None and limits.enabled
                         and limits.budget_usd is not None)
                self.store.record_usage(name, key=self._usage_key(name), task_id=task_id,
                                        cost_usd=cost, source=source, gated=gated)
        except sqlite3.Error as e:
            # The run's outcome stands; an open reservation is settled (fail closed) when the
            # run ends.
            self.store.log(task_id, Actor.BRIDGE, "usage_record_failed",
                           {"account": name, "error": redact(str(e), 500)})
            return False
        self.store.log(task_id, Actor.BRIDGE, "usage_recorded",
                       {"account": name, "cost_usd": cost, "source": source})
        return True

    # ---- routing ---------------------------------------------------------------------------

    def _unavailable(self, name: str, need: str) -> str | None:
        """Why agent `name` cannot take work needing `need` right now; None when it can."""
        b = self.bridge
        agent = b.orch.agents[name]
        if not agent.enabled:
            return "disabled"
        if agent.adapter == "client":
            return None if need == "read" else "the client cannot be started by the bridge"
        if not agent.can(need):
            return f"cannot {need}"
        if not b._adapter(agent.adapter).available():
            return f"{agent.adapter} binary not found"
        try:
            account = b._account(agent.account or "")
        except ValueError as e:
            return str(e)
        state = self._refresh_account_marks(account.name, key=account_key(account))["state"]
        if state != "available":
            return f"account {account.name} {state}"
        limits = self._limits(account.name)
        if limits is not None:
            report = self._limit_report(account.name, reserve=self._next_run_reserve(account.name))
            if report["status"] == "limited":
                return f"account {account.name} over limit: {report['reason']}"
        if self.store.active_on_account(account.name) >= b._slots(account.name):
            return f"account {account.name} busy"
        # Other bridge processes on this machine share the account's slot lock files.
        try:
            lock = AccountLock(account, b.cfg.lock_dir, b._slots(account.name))
        except ValueError as e:
            return str(e)
        if not lock.free():
            return f"account {account.name} busy in another bridge process"
        return None

    def _unscopable(self, agent: AgentConfig, spec: dict[str, Any]) -> list[str]:
        """Paths a claude-cli writer cannot turn into permission rules for this task."""
        cfg = self.bridge.cfg
        if (agent.adapter != "claude-cli" or not agent.writes
                or (spec.get("sandbox") or cfg.sandbox) != "workspace-write"):
            return []
        return rule_unsafe([*spec["allowed_paths"], *spec.get("excluded_paths", []),
                            *cfg.protected_paths])

    def _ineligible(self, name: str, need: str, spec: dict[str, Any] | None) -> str | None:
        """Why agent `name` cannot take this work (including scoping `spec`'s paths)."""
        reason = self._unavailable(name, need)
        if reason is None and spec and (bad := self._unscopable(self.bridge.orch.agents[name],
                                                                spec)):
            reason = f"cannot scope writes to {bad}"
        return reason

    def _pick(self, role: str, need: str, spec: dict[str, Any] | None = None) -> str:
        """The agent of `role` for new work (able to scope `spec`'s paths): the first eligible
        one in configured order, or with strategy quota the best-ranked eligible one."""
        orch = self.bridge.orch
        if orch.role(role).strategy == "quota":
            return self._ranked(role, need, spec)[0][0]
        reasons = []
        for name in orch.role(role).agents:
            reason = self._ineligible(name, need, spec)
            if reason is None:
                return name
            reasons.append(f"{name}: {reason}")
        raise ValueError(f"no eligible {role} agent ({'; '.join(reasons)})")

    def _ranked(self, role: str, need: str,
                spec: dict[str, Any] | None = None) -> list[tuple[str, float | None]]:
        """Every eligible agent of `role` with its account's remaining quota percent, most
        remaining first; unknown quota (or no account) after every known value; ties keep list
        order. Raises the same ValueError as _pick when none is eligible."""
        orch = self.bridge.orch
        reasons, eligible = [], []
        for name in orch.role(role).agents:
            reason = self._ineligible(name, need, spec)
            if reason is not None:
                reasons.append(f"{name}: {reason}")
                continue
            account = orch.agents[name].account
            eligible.append((name, self._remaining(account) if account else None))
        if not eligible:
            raise ValueError(f"no eligible {role} agent ({'; '.join(reasons)})")
        return sorted(eligible, key=lambda c: (c[1] is None, -(c[1] or 0.0)))

    def _executors_on(self, account: str) -> list[str]:
        orch = self.bridge.orch
        ordered = dict.fromkeys([*orch.role("executor").agents, *orch.agents])
        return [n for n in ordered if orch.agents[n].account == account
                and orch.agents[n].enabled and orch.agents[n].can("write")]

    def _quota_fallback(self, task_id: str) -> None:
        """Opt-in: move a task off an account in QUOTA state, below the quota threshold or over
        one of its configured limits. Never on AUTH; capped per task by max_switches."""
        b = self.bridge
        policy = b.orch.routing.quota_fallback
        if not policy.enabled:
            return
        row = self.store.get(task_id)
        current = b._agent_of(row)
        if current is None:
            return
        account = b.orch.agent(current).account or ""
        state = self._refresh_account_marks(account, key=b._key(account))["state"]
        if state == "auth":
            return
        limit = None
        limits = self._limits(account)
        if limits is not None:
            report = self._limit_report(account, reserve=self._next_run_reserve(account))
            if report["status"] == "limited":
                limit = report["reason"]
        remaining = 0 if state == "quota" else self._remaining(account)
        low = remaining is not None and remaining < policy.min_remaining_pct
        if not low and limit is None:
            return
        reason = ("quota" if state == "quota" and low else
                  "limit" if limit is not None else "below_threshold")
        switches = self.store.count_events(task_id, "account_auto_switched")
        if switches >= policy.max_switches:
            self.store.log(task_id, Actor.BRIDGE, "auto_switch_skipped", {
                "account": account,
                "reason": f"switch limit reached ({switches}/{policy.max_switches})"})
            return
        if policy.keep_sessions and row["session_id"]:
            self.store.log(task_id, Actor.BRIDGE, "auto_switch_skipped", {
                "account": account, "reason": "keep_sessions: the task has an agent session"})
            return
        for name in policy.agents or b.orch.role("executor").agents:
            candidate = b.orch.agents[name]
            if name == current or candidate.account == account:
                continue
            if self._unavailable(name, "write"):
                continue
            if login_status(b.cfg, b._account(candidate.account or "")) is not True:
                continue
            to_remaining = self._remaining(candidate.account or "")
            if to_remaining is not None and to_remaining < policy.min_remaining_pct:
                continue
            b._rebind(task_id, name, Actor.BRIDGE, "account_auto_switched", {
                "reason": reason, "remaining_pct": remaining, "to_remaining_pct": to_remaining,
                **({"limit": limit} if reason == "limit" else {}),
            })
            return
        self.store.log(task_id, Actor.BRIDGE, "auto_switch_skipped", {
            "account": account, "remaining_pct": remaining, "reason": "no eligible fallback",
            **({"limit": limit} if limit is not None else {}),
        })

    def _route(self, task_id: str) -> bool:
        """Bind an unpinned task to an eligible executor (the first in order, or with strategy
        quota the one with the most remaining quota); later runs keep that agent.

        True when this call bound the task (so a lost slot race can undo it)."""
        row = self.store.get(task_id)
        if self.bridge._agent_of(row) is not None:
            return False
        extra: dict[str, Any] = {}
        if self.bridge.orch.role("executor").strategy == "quota":
            ranked = self._ranked("executor", "write", row["spec"])
            name = ranked[0][0]
            extra = {"strategy": "quota", "remaining_pct": ranked[0][1],
                     "candidates": [{"agent": n, "remaining_pct": r} for n, r in ranked]}
        else:
            name = self._pick("executor", "write", row["spec"])
        account = self.bridge.orch.agents[name].account
        self.store.update(task_id, agent=name, account=account)
        self.store.log(task_id, Actor.BRIDGE, "agent_routed",
                       {"role": "executor", "agent": name, "account": account, **extra})
        return True

    def _unroute(self, task_id: str, error: ConflictError | LimitExceeded) -> None:
        """Undo a fresh routing whose account slot was taken, or whose account went over its
        limit, between the check and the start."""
        row = self.store.get(task_id)
        if row["session_id"]:
            return
        name, account = row["agent"], row["account"]
        self.store.update(task_id, agent=None, account=None)
        self.store.log(task_id, Actor.BRIDGE, "agent_unrouted",
                       {"agent": name, "account": account, "reason": redact(str(error), 500)})
