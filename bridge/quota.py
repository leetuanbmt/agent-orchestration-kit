"""Read account quota snapshots from Codex session rollouts."""

from __future__ import annotations

import json
import math
import os
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from .accounts import Account

if TYPE_CHECKING:
    from .orchestrator import AccountLimits, TokenPricing

# Window lengths that identify a provider window's kind; they are not limits.
FIVE_HOUR_MINUTES = 300
WEEKLY_MINUTES = 10080
BUDGET_PERIODS = ("day", "week", "month")
_TOKEN_FIELDS = ("input_tokens", "cache_creation_input_tokens", "output_tokens",
                 "cache_read_input_tokens")


def codex_home_for(account: Account) -> Path:
    if account.codex_home is not None:
        return account.codex_home
    return Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else Path.home() / ".codex"


def numeric(value: object) -> bool:
    """A real number: int or float, never bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def remaining_pct(rate_limits: dict, now: float) -> float | None:
    used = []
    for name in ("primary", "secondary"):
        window = rate_limits.get(name)
        if not isinstance(window, dict) or not numeric(window.get("used_percent")):
            continue
        reset = window.get("resets_at")
        used.append(0.0 if numeric(reset) and reset <= now else window["used_percent"])
    return max(0.0, 100.0 - max(used)) if used else None


def effective_account_state(state: dict, quota: dict | str, cooldown_s: float | None,
                            now: float) -> tuple[dict, str | None]:
    """Pure: the account state work should see, and why a stored QUOTA mark lapsed.

    A QUOTA mark lapses after `cooldown_s` when no readable window reset lies ahead
    ("cooldown"), or once a window open at mark time has reset while quota remains ("reset").
    Returns the stored state and None when it stands."""
    if state["state"] != "quota" or state.get("updated_at") is None:
        return state, None
    lifted = {**state, "state": "available", "last_error": None}
    if cooldown_s is not None and now - state["updated_at"] >= cooldown_s:
        windows = ([quota.get(w) for w in ("primary", "secondary")]
                   if isinstance(quota, dict) else [])
        resets = [w.get("resets_at") for w in windows if isinstance(w, dict)]
        if not any(numeric(r) and r > now for r in resets):
            # No reset time is readable: lift the mark once the cooldown has elapsed.
            return lifted, "cooldown"
    if not isinstance(quota, dict):
        return state, None
    limits = {window: quota.get(window) for window in ("primary", "secondary")}
    remaining = remaining_pct(limits, now)
    if remaining is None or remaining <= 0:
        return state, None
    for window in limits.values():
        reset = window.get("resets_at") if isinstance(window, dict) else None
        if numeric(reset) and state["updated_at"] < reset <= now:
            return lifted, "reset"
    return state, None


# path -> ((st_mtime_ns, st_size), rate_limits payloads in file order); independent of `now`.
_ROLLOUT_CACHE: dict[str, tuple[tuple[int, int], tuple[dict, ...]]] = {}
_CACHE_CAP = 256


def _parse_rollout(path: Path) -> tuple[dict, ...] | None:
    """Every token_count rate_limits payload in a rollout, in order; None if unreadable."""
    found = []
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "event_msg":
                    continue
                payload = event.get("payload")
                if (isinstance(payload, dict) and payload.get("type") == "token_count"
                        and isinstance(payload.get("rate_limits"), dict)):
                    found.append(payload["rate_limits"])
    except OSError:
        return None
    return tuple(found)


def read_quota(home: Path, now: float | None = None) -> dict | None:
    sessions = home / "sessions"
    if sessions.is_symlink():
        return None
    candidates = []
    for directory, _, names in os.walk(sessions, followlinks=False):
        for name in names:
            if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                continue
            path = Path(directory) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                candidates.append((info.st_mtime, path, info.st_mtime_ns, info.st_size))
    now = time.time() if now is None else now
    newest = sorted(candidates, key=lambda item: item[0], reverse=True)[:20]
    prefix = str(sessions) + os.sep
    keep = {str(path) for _, path, _, _ in newest}
    for stale in [k for k in _ROLLOUT_CACHE if k.startswith(prefix) and k not in keep]:
        del _ROLLOUT_CACHE[stale]
    if len(_ROLLOUT_CACHE) > _CACHE_CAP:
        for stale in [k for k in _ROLLOUT_CACHE if k not in keep]:
            del _ROLLOUT_CACHE[stale]
    for mtime, path, mtime_ns, size in newest:
        key = str(path)
        cached = _ROLLOUT_CACHE.get(key)
        if cached is not None and cached[0] == (mtime_ns, size):
            payloads = cached[1]
        else:
            payloads = _parse_rollout(path)
            if payloads is None:
                continue
            _ROLLOUT_CACHE[key] = ((mtime_ns, size), payloads)
        latest = None
        latest_codex = None
        for limits in payloads:
            if remaining_pct(limits, now) is None:
                continue
            latest = limits
            # Legacy snapshots without a bucket ID describe Codex quota.
            if limits.get("limit_id", "codex") == "codex":
                latest_codex = limits
        if latest_codex is not None:
            latest = latest_codex
        if latest is not None:
            result = {"remaining_pct": remaining_pct(latest, now), "observed_at": mtime}
            for name in ("primary", "secondary"):
                window = latest.get(name)
                result[name] = ({key: window.get(key) for key in
                                 ("used_percent", "window_minutes", "resets_at")}
                                if isinstance(window, dict) else None)
            plan = latest.get("plan_type")
            result["plan_type"] = plan if isinstance(plan, str) else None
            return result
    return None


def period_bounds(period: str, now: float) -> tuple[float, float]:
    """The UTC calendar period containing `now`, as (start, next_start) epoch seconds."""
    moment = datetime.fromtimestamp(now, UTC)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        start, end = day, day + timedelta(days=1)
    elif period == "week":
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=7)
    elif period == "month":
        start = day.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))
    else:
        raise ValueError(f"unknown budget period {period!r}; expected one of {BUDGET_PERIODS}")
    return start.timestamp(), end.timestamp()


def estimate_cost(usage: dict | None, pricing: TokenPricing | None) -> float | None:
    """USD estimate from token counts and user-supplied pricing; None when it cannot be made.

    `cached_input_tokens` (Codex, a subset of `input_tokens`) is ignored on purpose: those
    tokens are priced at the full input rate, an overestimate."""
    if pricing is None or not isinstance(usage, dict):
        return None
    if not any(key in usage for key in _TOKEN_FIELDS):
        return None

    def count(key: str) -> float:
        value = usage.get(key)
        return value if numeric(value) and math.isfinite(value) and value >= 0 else 0

    cached_rate = (pricing.cached_input_per_mtok if pricing.cached_input_per_mtok is not None
                   else pricing.input_per_mtok)
    fresh_input = count("input_tokens") + count("cache_creation_input_tokens")
    return (fresh_input * pricing.input_per_mtok
            + count("output_tokens") * pricing.output_per_mtok
            + count("cache_read_input_tokens") * cached_rate) / 1e6


def run_cost(cost_usd: object, usage: dict | None,
             pricing: TokenPricing | None) -> tuple[float | None, str]:
    """A run's cost and its source: CLI-reported, estimated from tokens, or unknown. A CLI's
    figure (Claude's total_cost_usd) is its own list-price estimate, not a bill: `reported`."""
    if numeric(cost_usd) and cost_usd >= 0:
        return float(cost_usd), "reported"
    estimate = estimate_cost(usage, pricing)
    if estimate is not None:
        return estimate, "estimated"
    return None, "unavailable"


def window_usage(rate_limits: dict | None, now: float) -> list[dict]:
    """Each reported window's usage; a window whose reset has passed counts as 0 % used."""
    result = []
    for slot in ("primary", "secondary"):
        window = (rate_limits or {}).get(slot)
        if not isinstance(window, dict) or not numeric(window.get("used_percent")):
            continue
        reset = window.get("resets_at")
        reset = reset if numeric(reset) else None
        passed = reset is not None and reset <= now
        minutes = window.get("window_minutes")
        result.append({
            "slot": slot,
            "window_minutes": minutes if numeric(minutes) else None,
            "used_pct": 0.0 if passed else float(window["used_percent"]),
            "resets_at": None if passed else reset,
        })
    return result


def _resets(resets_at: float | None) -> str:
    if resets_at is None:
        return ""
    stamp = datetime.fromtimestamp(resets_at, UTC).strftime("%Y-%m-%dT%H:%MZ")
    return f" (resets {stamp})"


def _reason(check: dict, unknown: int, reserve: float) -> str:
    name, threshold, used = check["limit"], check["threshold"], check["used"]
    tail = _resets(check["resets_at"])
    if check["unit"] == "pct":
        return f"{name}: {used:.1f}% used >= {threshold:g}%{tail}"
    if unknown > 0:
        return f"{name}: {unknown} run(s) with unknown cost; configure pricing or wait{tail}"
    if used >= threshold:
        return f"{name}: ${used:.2f} spent+reserved >= ${threshold:.2f}{tail}"
    return (f"{name}: ${used:.2f} spent+reserved, ${reserve:.2f} reserve > "
            f"${threshold:.2f}{tail}")


def evaluate_limits(limits: AccountLimits | None, rate_limits: dict | None,
                    spend: dict | None, now: float, reserve: float = 0.0) -> dict:
    """Pure check of an account's configured limits against provider usage and spend.

    `spend` maps a period to {"spent", "reserved", "unknown", "estimated"}; `reserve` is what
    the next run would reserve. Returns {"status", "reason", "checks"}."""
    if limits is None:
        return {"status": "none", "reason": None, "checks": []}
    windows = window_usage(rate_limits, now)
    checks: list[dict] = []
    unknowns: list[int] = []
    for name, minutes in (("five_hour_pct", FIVE_HOUR_MINUTES), ("weekly_pct", WEEKLY_MINUTES),
                          ("any_window_pct", None)):
        threshold = getattr(limits, name)
        if threshold is None:
            continue
        matching = [w for w in windows if minutes is None or w["window_minutes"] == minutes]
        if matching:
            top = max(matching, key=lambda w: w["used_pct"])
            used = top["used_pct"]
            check = {"limit": name, "unit": "pct", "threshold": threshold, "used": used,
                     "remaining": max(0.0, threshold - used), "resets_at": top["resets_at"],
                     "source": "actual", "breached": used >= threshold}
        else:
            check = {"limit": name, "unit": "pct", "threshold": threshold, "used": None,
                     "remaining": None, "resets_at": None, "source": "unavailable",
                     "breached": False}
        checks.append(check)
        unknowns.append(0)
    budget = limits.budget_usd
    for period in BUDGET_PERIODS:
        threshold = getattr(budget, period) if budget is not None else None
        if threshold is None:
            continue
        s = (spend or {}).get(period) or {}
        used = s.get("spent", 0.0) + s.get("reserved", 0.0)
        unknown = s.get("unknown", 0)
        if unknown > 0:
            source, breached = "unavailable", True
        else:
            source = "estimated" if s.get("estimated", False) else "actual"
            breached = used >= threshold or used + reserve > threshold
        checks.append({"limit": f"budget_usd.{period}", "unit": "usd", "threshold": threshold,
                       "used": used, "remaining": max(0.0, threshold - used),
                       "resets_at": period_bounds(period, now)[1], "source": source,
                       "breached": breached})
        unknowns.append(unknown)
    if not limits.enabled:
        for check in checks:
            check["breached"] = False
        return {"status": "disabled", "reason": None, "checks": checks}
    for check, unknown in zip(checks, unknowns, strict=True):
        if check["breached"]:
            return {"status": "limited", "reason": _reason(check, unknown, reserve),
                    "checks": checks}
    return {"status": "ok", "reason": None, "checks": checks}
