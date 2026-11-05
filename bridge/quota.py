"""Read account quota snapshots from Codex session rollouts."""

from __future__ import annotations

import json
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


def _numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def remaining_pct(rate_limits: dict, now: float) -> float | None:
    used = []
    for name in ("primary", "secondary"):
        window = rate_limits.get(name)
        if not isinstance(window, dict) or not _numeric(window.get("used_percent")):
            continue
        reset = window.get("resets_at")
        used.append(0.0 if _numeric(reset) and reset <= now else window["used_percent"])
    return max(0.0, 100.0 - max(used)) if used else None


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
                candidates.append((info.st_mtime, path))
    now = time.time() if now is None else now
    for mtime, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:20]:
        latest = None
        latest_codex = None
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
                        limits = payload["rate_limits"]
                        if remaining_pct(limits, now) is None:
                            continue
                        latest = limits
                        # Legacy snapshots without a bucket ID describe Codex quota.
                        if limits.get("limit_id", "codex") == "codex":
                            latest_codex = limits
        except OSError:
            continue
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
        return value if _numeric(value) else 0

    cached_rate = (pricing.cached_input_per_mtok if pricing.cached_input_per_mtok is not None
                   else pricing.input_per_mtok)
    fresh_input = count("input_tokens") + count("cache_creation_input_tokens")
    return (fresh_input * pricing.input_per_mtok
            + count("output_tokens") * pricing.output_per_mtok
            + count("cache_read_input_tokens") * cached_rate) / 1e6


def run_cost(cost_usd: object, usage: dict | None,
             pricing: TokenPricing | None) -> tuple[float | None, str]:
    """A run's cost and its source: provider-reported, estimated from tokens, or unknown."""
    if _numeric(cost_usd) and cost_usd >= 0:
        return float(cost_usd), "actual"
    estimate = estimate_cost(usage, pricing)
    if estimate is not None:
        return estimate, "estimated"
    return None, "unavailable"


def window_usage(rate_limits: dict | None, now: float) -> list[dict]:
    """Each reported window's usage; a window whose reset has passed counts as 0 % used."""
    result = []
    for slot in ("primary", "secondary"):
        window = (rate_limits or {}).get(slot)
        if not isinstance(window, dict) or not _numeric(window.get("used_percent")):
            continue
        reset = window.get("resets_at")
        reset = reset if _numeric(reset) else None
        passed = reset is not None and reset <= now
        minutes = window.get("window_minutes")
        result.append({
            "slot": slot,
            "window_minutes": minutes if _numeric(minutes) else None,
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
