"""Per-account limits: config validation and pure limit evaluation helpers."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bridge import quota
from bridge.config import BridgeConfig
from bridge.orchestrator import (AccountLimits, AccountProfile, QuotaFallback, TokenPricing,
                                 UsdBudget, legacy, parse)
from bridge.quota import (estimate_cost, evaluate_limits, period_bounds, run_cost,
                          window_usage)


def ts(*args: int) -> float:
    return datetime(*args, tzinfo=UTC).timestamp()


NOW = ts(2026, 11, 5, 10, 0)  # a Thursday
LATER = ts(2026, 11, 5, 12, 0)
NEXT_WEEK = ts(2026, 11, 9, 0, 0)

BASE = """
version: 1
agents:
  client: {adapter: client}
  codex: {adapter: codex-cli, account: cx}
roles:
  executor: codex
  reviewer: client
"""


def windows(primary=None, secondary=None, p_minutes=300, s_minutes=10080,
            p_reset=LATER, s_reset=NEXT_WEEK):
    data = {}
    if primary is not None:
        data["primary"] = {"used_percent": primary, "window_minutes": p_minutes,
                           "resets_at": p_reset}
    if secondary is not None:
        data["secondary"] = {"used_percent": secondary, "window_minutes": s_minutes,
                             "resets_at": s_reset}
    return data


def check(result, name):
    return next(c for c in result["checks"] if c["limit"] == name)


# @pt.id KIT-LIMIT-001-config-defaults
# @pt.item AccountLimits, AccountProfile.limits and QuotaFallback defaults
# @pt.condition models built with no limit fields
# @pt.expected limits default to None; AccountLimits is enabled with no thresholds, no reserve; QuotaFallback max_switches=3, keep_sessions=False
# @pt.target bridge.orchestrator.AccountLimits, bridge.orchestrator.QuotaFallback
# @pt.method unit
# @pt.legacy none
def test_config_defaults():
    assert AccountProfile(provider="openai").limits is None
    limits = AccountLimits()
    assert limits.enabled is True
    assert (limits.five_hour_pct, limits.weekly_pct, limits.any_window_pct) == (None,) * 3
    assert limits.budget_usd is None and limits.pricing is None and limits.cooldown_s is None
    assert limits.reserve_usd == 0.0
    fallback = QuotaFallback()
    assert fallback.max_switches == 3 and fallback.keep_sessions is False
    assert TokenPricing(input_per_mtok=1, output_per_mtok=2).cached_input_per_mtok is None


# @pt.id KIT-LIMIT-002-config-bounds
# @pt.item AccountLimits, UsdBudget, TokenPricing and QuotaFallback field bounds
# @pt.condition values at and beyond each bound
# @pt.expected values inside the bounds validate; out-of-range values are rejected
# @pt.target bridge.orchestrator.AccountLimits
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("model, data", [
    (AccountLimits, {"five_hour_pct": 0}),
    (AccountLimits, {"weekly_pct": 100.1}),
    (AccountLimits, {"any_window_pct": -1}),
    (AccountLimits, {"cooldown_s": 59}),
    (AccountLimits, {"cooldown_s": 604801}),
    (AccountLimits, {"reserve_usd": -1, "budget_usd": {"day": 5}}),
    (UsdBudget, {"day": 0}),
    (UsdBudget, {"week": -2}),
    (TokenPricing, {"input_per_mtok": -1, "output_per_mtok": 1}),
    (TokenPricing, {"input_per_mtok": 1, "output_per_mtok": 1, "cached_input_per_mtok": -1}),
    (QuotaFallback, {"max_switches": 0}),
    (QuotaFallback, {"max_switches": 21}),
])
def test_config_bounds_rejected(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


# @pt.id KIT-LIMIT-003-config-bounds-ok
# @pt.item AccountLimits and QuotaFallback values at their inclusive bounds
# @pt.condition thresholds at 100, cooldown at 60 and 604800, max_switches 1 and 20
# @pt.expected all validate
# @pt.target bridge.orchestrator.AccountLimits
# @pt.method unit
# @pt.legacy none
def test_config_bounds_accepted():
    AccountLimits(five_hour_pct=100, weekly_pct=0.5, any_window_pct=100, cooldown_s=60)
    AccountLimits(cooldown_s=604800)
    TokenPricing(input_per_mtok=0, output_per_mtok=0, cached_input_per_mtok=0)
    QuotaFallback(max_switches=1)
    QuotaFallback(max_switches=20, keep_sessions=True)


# @pt.id KIT-LIMIT-004-config-unknown-keys
# @pt.item New limit models forbid unknown keys
# @pt.condition an unknown key on each model
# @pt.expected ValidationError
# @pt.target bridge.orchestrator.AccountLimits, UsdBudget, TokenPricing
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("model, data", [
    (AccountLimits, {"five_hour": 80}),
    (UsdBudget, {"day": 5, "year": 100}),
    (TokenPricing, {"input_per_mtok": 1, "output_per_mtok": 1, "currency": "usd"}),
    (QuotaFallback, {"max_moves": 2}),
])
def test_config_unknown_keys(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)


# @pt.id KIT-LIMIT-005-config-budget-reserve
# @pt.item UsdBudget and reserve_usd validators
# @pt.condition empty budget; reserve without budget; reserve above the smallest budget; reserve equal to it
# @pt.expected the first three are rejected; a reserve equal to the smallest budget validates
# @pt.target bridge.orchestrator.UsdBudget, bridge.orchestrator.AccountLimits
# @pt.method unit
# @pt.legacy none
def test_config_budget_and_reserve():
    with pytest.raises(ValidationError, match="at least one"):
        UsdBudget()
    with pytest.raises(ValidationError, match="at least one"):
        AccountLimits(budget_usd={})
    with pytest.raises(ValidationError, match="needs budget_usd"):
        AccountLimits(reserve_usd=1)
    with pytest.raises(ValidationError, match="smallest"):
        AccountLimits(budget_usd={"day": 5, "month": 100}, reserve_usd=5.01)
    limits = AccountLimits(budget_usd={"day": 5, "month": 100}, reserve_usd=5)
    assert limits.budget_usd.day == 5 and limits.budget_usd.week is None


# @pt.id KIT-LIMIT-006-config-registry
# @pt.item Registry account entry carrying limits
# @pt.condition orchestrator.parse with a registry entry that has limits; legacy() with the same registry
# @pt.expected the account's limits are parsed into AccountLimits; legacy() still validates; an invalid limit in the registry is reported
# @pt.target bridge.orchestrator.parse, bridge.orchestrator.legacy
# @pt.method unit
# @pt.legacy none
def test_config_registry_limits():
    registry = {"cx": {"provider": "openai", "home": "/tmp/cx",
                       "limits": {"five_hour_pct": 85, "budget_usd": {"day": 20},
                                  "reserve_usd": 1,
                                  "pricing": {"input_per_mtok": 1, "output_per_mtok": 4}}}}
    orch = parse(BASE, registry=registry)
    limits = orch.accounts["cx"].limits
    assert isinstance(limits, AccountLimits)
    assert limits.five_hour_pct == 85 and limits.budget_usd.day == 20
    assert limits.pricing.output_per_mtok == 4
    assert orch.routing.quota_fallback.max_switches == 3
    assert legacy(BridgeConfig(), registry).accounts["cx"].limits.reserve_usd == 1
    assert legacy(BridgeConfig()).accounts["default"].limits is None
    bad = {"cx": {"provider": "openai", "limits": {"weekly_pct": 150}}}
    with pytest.raises(ValueError, match="weekly_pct"):
        parse(BASE, registry=bad)


# @pt.id KIT-LIMIT-007-config-fallback-yaml
# @pt.item routing.quota_fallback max_switches and keep_sessions in orchestrator.yaml
# @pt.condition yaml sets both new keys
# @pt.expected they are parsed
# @pt.target bridge.orchestrator.parse
# @pt.method unit
# @pt.legacy none
def test_config_fallback_yaml():
    text = BASE + """
accounts:
  cx: {provider: openai}
routing:
  quota_fallback: {enabled: true, max_switches: 5, keep_sessions: true}
"""
    fallback = parse(text).routing.quota_fallback
    assert fallback.max_switches == 5 and fallback.keep_sessions is True


# @pt.id KIT-LIMIT-010-period-bounds
# @pt.item period_bounds UTC calendar periods
# @pt.condition day, week (Monday start, including a Monday and a Sunday), month including December to January
# @pt.expected start and next start of the containing period; unknown period raises ValueError
# @pt.target bridge.quota.period_bounds
# @pt.method unit
# @pt.legacy none
def test_period_bounds():
    assert period_bounds("day", NOW) == (ts(2026, 11, 5), ts(2026, 11, 6))
    assert period_bounds("day", ts(2026, 11, 5)) == (ts(2026, 11, 5), ts(2026, 11, 6))
    assert period_bounds("week", NOW) == (ts(2026, 11, 2), ts(2026, 11, 9))
    assert period_bounds("week", ts(2026, 11, 2)) == (ts(2026, 11, 2), ts(2026, 11, 9))
    assert period_bounds("week", ts(2026, 11, 8, 23, 59)) == (ts(2026, 11, 2), ts(2026, 11, 9))
    assert period_bounds("week", ts(2026, 12, 31, 8)) == (ts(2026, 12, 28), ts(2027, 1, 4))
    assert period_bounds("month", NOW) == (ts(2026, 11, 1), ts(2026, 12, 1))
    assert period_bounds("month", ts(2026, 12, 31, 23, 59)) == (ts(2026, 12, 1), ts(2027, 1, 1))
    assert period_bounds("day", ts(2026, 12, 31, 23, 59)) == (ts(2026, 12, 31), ts(2027, 1, 1))
    with pytest.raises(ValueError):
        period_bounds("year", NOW)


# @pt.id KIT-LIMIT-011-estimate-cost
# @pt.item estimate_cost from token usage and user pricing
# @pt.condition full usage, missing cached rate, non-numeric/bool fields, Codex cached_input_tokens, no pricing, no token fields
# @pt.expected formula per the contract; None when pricing or token fields are missing
# @pt.target bridge.quota.estimate_cost
# @pt.method unit
# @pt.legacy none
def test_estimate_cost():
    pricing = TokenPricing(input_per_mtok=2, output_per_mtok=10, cached_input_per_mtok=0.5)
    usage = {"input_tokens": 1_000_000, "cache_creation_input_tokens": 500_000,
             "output_tokens": 200_000, "cache_read_input_tokens": 2_000_000}
    assert estimate_cost(usage, pricing) == pytest.approx(3.0 + 2.0 + 1.0)
    no_cached = TokenPricing(input_per_mtok=2, output_per_mtok=10)
    assert estimate_cost(usage, no_cached) == pytest.approx(3.0 + 2.0 + 4.0)
    odd = {"input_tokens": "lots", "output_tokens": True, "cache_read_input_tokens": None,
           "cache_creation_input_tokens": 1_000_000}
    assert estimate_cost(odd, pricing) == pytest.approx(2.0)
    codex = {"input_tokens": 1_000_000, "cached_input_tokens": 800_000, "output_tokens": 0}
    assert estimate_cost(codex, pricing) == pytest.approx(2.0)
    assert estimate_cost(usage, None) is None
    assert estimate_cost(None, pricing) is None
    assert estimate_cost({}, pricing) is None
    assert estimate_cost({"cached_input_tokens": 5, "total": 9}, pricing) is None


# @pt.id KIT-LIMIT-012-run-cost
# @pt.item run_cost source precedence
# @pt.condition provider cost, zero cost, negative/bool/str cost with and without usage+pricing
# @pt.expected provider cost is actual; otherwise estimated from pricing; otherwise unavailable
# @pt.target bridge.quota.run_cost
# @pt.method unit
# @pt.legacy none
def test_run_cost():
    pricing = TokenPricing(input_per_mtok=1, output_per_mtok=1)
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert run_cost(0.42, usage, pricing) == (0.42, "actual")
    assert run_cost(0, None, None) == (0.0, "actual")
    assert run_cost(3, None, None) == (3.0, "actual")
    assert run_cost(-1.0, usage, pricing) == (pytest.approx(2.0), "estimated")
    assert run_cost(True, usage, pricing) == (pytest.approx(2.0), "estimated")
    assert run_cost(None, usage, pricing) == (pytest.approx(2.0), "estimated")
    assert run_cost("0.5", usage, pricing) == (pytest.approx(2.0), "estimated")
    assert run_cost(-1.0, usage, None) == (None, "unavailable")
    assert run_cost(False, None, pricing) == (None, "unavailable")


# @pt.id KIT-LIMIT-013-window-usage
# @pt.item window_usage normalisation
# @pt.condition a live window, a passed window, a window without numeric used_percent, None input
# @pt.expected live window kept; passed window counts 0 % with resets_at None; invalid windows skipped
# @pt.target bridge.quota.window_usage
# @pt.method unit
# @pt.legacy none
def test_window_usage():
    data = windows(primary=40, secondary=90, s_reset=NOW - 1)
    assert window_usage(data, NOW) == [
        {"slot": "primary", "window_minutes": 300, "used_pct": 40.0, "resets_at": LATER},
        {"slot": "secondary", "window_minutes": 10080, "used_pct": 0.0, "resets_at": None},
    ]
    data["primary"]["used_percent"] = True
    assert [w["slot"] for w in window_usage(data, NOW)] == ["secondary"]
    assert window_usage(None, NOW) == []
    assert window_usage({"primary": "x"}, NOW) == []


# @pt.id KIT-LIMIT-020-eval-none
# @pt.item evaluate_limits without configured limits
# @pt.condition limits None
# @pt.expected status none, no reason, no checks
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_evaluate_none():
    assert evaluate_limits(None, windows(99, 99), {"day": {"spent": 5}}, NOW) == {
        "status": "none", "reason": None, "checks": []}


# @pt.id KIT-LIMIT-021-five-hour-boundary
# @pt.item five_hour_pct threshold boundaries
# @pt.condition five_hour_pct 85 with the 300-minute window at 84.9, 85 and 85.1 %
# @pt.expected 84.9 ok; 85 and 85.1 limited with a reason naming the reset time
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("used, status", [(84.9, "ok"), (85, "limited"), (85.1, "limited")])
def test_five_hour_boundary(used, status):
    limits = AccountLimits(five_hour_pct=85)
    result = evaluate_limits(limits, windows(primary=used), None, NOW)
    assert result["status"] == status
    c = check(result, "five_hour_pct")
    assert c["unit"] == "pct" and c["source"] == "actual" and c["threshold"] == 85
    assert c["used"] == pytest.approx(used) and c["resets_at"] == LATER
    assert c["remaining"] == pytest.approx(max(0, 85 - used))
    assert c["breached"] is (status == "limited")
    if status == "ok":
        assert result["reason"] is None
    else:
        assert result["reason"] == (f"five_hour_pct: {float(used):.1f}% used >= 85% "
                                    "(resets 2026-11-05T12:00Z)")


# @pt.id KIT-LIMIT-022-window-classification
# @pt.item Windows are classified by window_minutes, not by slot
# @pt.condition a 300-minute window at 95 % and a 10080-minute window at 10 %, also with slots swapped
# @pt.expected five_hour_pct sees only the 300-minute window, weekly_pct only the 10080-minute one
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_window_classification():
    weekly_only = AccountLimits(weekly_pct=80)
    five_only = AccountLimits(five_hour_pct=80)
    data = windows(primary=95, secondary=10)
    assert evaluate_limits(weekly_only, data, None, NOW)["status"] == "ok"
    assert check(evaluate_limits(weekly_only, data, None, NOW), "weekly_pct")["used"] == 10
    assert evaluate_limits(five_only, data, None, NOW)["status"] == "limited"
    swapped = windows(primary=10, secondary=95, p_minutes=10080, s_minutes=300,
                      p_reset=NEXT_WEEK, s_reset=LATER)
    assert evaluate_limits(weekly_only, swapped, None, NOW)["status"] == "ok"
    result = evaluate_limits(five_only, swapped, None, NOW)
    assert result["status"] == "limited" and check(result, "five_hour_pct")["resets_at"] == LATER
    other = windows(primary=99, p_minutes=60)
    assert check(evaluate_limits(five_only, other, None, NOW), "five_hour_pct")["source"] == \
        "unavailable"


# @pt.id KIT-LIMIT-023-any-window
# @pt.item any_window_pct uses every reported window
# @pt.condition windows at 30 % and 70 % (including one with an unusual length), any_window_pct 60
# @pt.expected used is the max (70) with that window's reset; status limited
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_any_window_max():
    limits = AccountLimits(any_window_pct=60)
    result = evaluate_limits(limits, windows(primary=30, secondary=70, s_minutes=1440), None, NOW)
    c = check(result, "any_window_pct")
    assert c["used"] == 70 and c["resets_at"] == NEXT_WEEK and c["breached"]
    assert result["status"] == "limited"
    assert result["reason"].startswith("any_window_pct: 70.0% used >= 60%")
    assert evaluate_limits(AccountLimits(any_window_pct=75),
                           windows(primary=30, secondary=70), None, NOW)["status"] == "ok"


# @pt.id KIT-LIMIT-024-window-reset-recovery
# @pt.item A window whose reset time has passed counts as 0 % used
# @pt.condition five-hour window at 99 % whose resets_at is before now
# @pt.expected used 0, resets_at None, status ok (recovery without stored state)
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_window_reset_recovery():
    limits = AccountLimits(five_hour_pct=85, any_window_pct=90)
    data = windows(primary=99, p_reset=NOW - 60)
    assert evaluate_limits(limits, data, None, NOW - 120)["status"] == "limited"
    result = evaluate_limits(limits, data, None, NOW)
    assert result["status"] == "ok"
    c = check(result, "five_hour_pct")
    assert c["used"] == 0.0 and c["resets_at"] is None and c["remaining"] == 85


# @pt.id KIT-LIMIT-025-window-unavailable
# @pt.item Percentage limits without provider data
# @pt.condition no rate_limits at all, and an empty dict
# @pt.expected every percentage check has source unavailable, used/remaining/resets_at None, no breach; status ok
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("rate_limits", [None, {}])
def test_window_unavailable(rate_limits):
    limits = AccountLimits(five_hour_pct=50, weekly_pct=50, any_window_pct=50)
    result = evaluate_limits(limits, rate_limits, None, NOW)
    assert result["status"] == "ok" and result["reason"] is None
    assert [c["limit"] for c in result["checks"]] == ["five_hour_pct", "weekly_pct",
                                                      "any_window_pct"]
    for c in result["checks"]:
        assert c["source"] == "unavailable" and c["breached"] is False
        assert c["used"] is None and c["remaining"] is None and c["resets_at"] is None


# @pt.id KIT-LIMIT-030-budget-thresholds
# @pt.item USD budget against spend
# @pt.condition day budget 20 with spend under it and exactly at it
# @pt.expected under is ok with the remaining amount; exactly at the threshold is limited
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_budget_thresholds():
    limits = AccountLimits(budget_usd={"day": 20})
    result = evaluate_limits(limits, None, {"day": {"spent": 12.0, "reserved": 3.0}}, NOW)
    assert result["status"] == "ok"
    c = check(result, "budget_usd.day")
    assert c == {"limit": "budget_usd.day", "unit": "usd", "threshold": 20, "used": 15.0,
                 "remaining": 5.0, "resets_at": ts(2026, 11, 6), "source": "actual",
                 "breached": False}
    result = evaluate_limits(limits, None, {"day": {"spent": 20.0}}, NOW)
    assert result["status"] == "limited"
    assert check(result, "budget_usd.day")["remaining"] == 0
    assert result["reason"] == ("budget_usd.day: $20.00 spent+reserved >= $20.00 "
                                "(resets 2026-11-06T00:00Z)")
    assert evaluate_limits(limits, None, None, NOW)["status"] == "ok"


# @pt.id KIT-LIMIT-031-budget-reserve
# @pt.item Reservation must fit inside the budget
# @pt.condition day budget 20, 19 spent; reserve 1 (fits exactly) and reserve 1.5 (does not fit)
# @pt.expected exact fit allowed; overflow limited with a reserve reason
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_budget_reserve():
    limits = AccountLimits(budget_usd={"day": 20}, reserve_usd=1)
    spend = {"day": {"spent": 18.5, "reserved": 0.5}}
    assert evaluate_limits(limits, None, spend, NOW, reserve=1.0)["status"] == "ok"
    result = evaluate_limits(limits, None, spend, NOW, reserve=1.5)
    assert result["status"] == "limited"
    assert result["reason"] == ("budget_usd.day: $19.00 spent+reserved, $1.50 reserve > "
                                "$20.00 (resets 2026-11-06T00:00Z)")


# @pt.id KIT-LIMIT-032-budget-unknown
# @pt.item Unknown run cost inside a budget period fails closed
# @pt.condition week budget with zero spent but two runs of unknown cost
# @pt.expected source unavailable, breached, reason counts the runs and names the period end
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_budget_unknown():
    limits = AccountLimits(budget_usd={"week": 100})
    result = evaluate_limits(limits, None, {"week": {"unknown": 2}}, NOW)
    c = check(result, "budget_usd.week")
    assert c["source"] == "unavailable" and c["breached"] is True
    assert c["used"] == 0.0 and c["resets_at"] == NEXT_WEEK
    assert result["status"] == "limited"
    assert result["reason"] == ("budget_usd.week: 2 run(s) with unknown cost; configure "
                                "pricing or wait (resets 2026-11-09T00:00Z)")


# @pt.id KIT-LIMIT-033-budget-estimated
# @pt.item Estimated spend is reported as such
# @pt.condition month budget with estimated spend
# @pt.expected source estimated; status ok while under the budget
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_budget_estimated():
    limits = AccountLimits(budget_usd={"month": 50})
    result = evaluate_limits(limits, None, {"month": {"spent": 10.0, "estimated": True}}, NOW)
    c = check(result, "budget_usd.month")
    assert c["source"] == "estimated" and c["resets_at"] == ts(2026, 12, 1)
    assert result["status"] == "ok"


# @pt.id KIT-LIMIT-034-check-order
# @pt.item Check order and first-breach reason
# @pt.condition every limit configured; weekly and month budget breached
# @pt.expected checks in contract order; reason describes the first breached check (weekly_pct)
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_check_order():
    limits = AccountLimits(five_hour_pct=90, weekly_pct=50, any_window_pct=95,
                           budget_usd={"day": 10, "week": 40, "month": 100})
    spend = {"month": {"spent": 100.0}}
    result = evaluate_limits(limits, windows(primary=20, secondary=60), spend, NOW)
    assert [c["limit"] for c in result["checks"]] == [
        "five_hour_pct", "weekly_pct", "any_window_pct",
        "budget_usd.day", "budget_usd.week", "budget_usd.month"]
    assert [c["breached"] for c in result["checks"]] == [False, True, False, False, False,
                                                         True]
    assert result["status"] == "limited"
    assert result["reason"] == "weekly_pct: 60.0% used >= 50% (resets 2026-11-09T00:00Z)"


# @pt.id KIT-LIMIT-035-disabled
# @pt.item Disabled limits are reported but never block
# @pt.condition enabled False with every check over its threshold
# @pt.expected status disabled, reason None, checks computed with breached False
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_disabled():
    limits = AccountLimits(enabled=False, five_hour_pct=50, budget_usd={"day": 5})
    result = evaluate_limits(limits, windows(primary=99), {"day": {"unknown": 1}}, NOW,
                             reserve=10)
    assert result["status"] == "disabled" and result["reason"] is None
    assert len(result["checks"]) == 2
    assert all(c["breached"] is False for c in result["checks"])
    assert check(result, "five_hour_pct")["used"] == 99


# @pt.id KIT-LIMIT-036-no-reset-time
# @pt.item Reason without a readable reset time
# @pt.condition a breached five-hour window that reports no resets_at
# @pt.expected the reason omits the reset parenthesis
# @pt.target bridge.quota.evaluate_limits
# @pt.method unit
# @pt.legacy none
def test_reason_without_reset():
    result = evaluate_limits(AccountLimits(five_hour_pct=80), windows(primary=81, p_reset=None),
                             None, NOW)
    assert result["reason"] == "five_hour_pct: 81.0% used >= 80%"


# @pt.id KIT-LIMIT-040-no-hardcoded-prices
# @pt.item quota module carries no provider prices or quota sizes
# @pt.condition module constants inspected
# @pt.expected only the window-length identifiers exist; estimate_cost needs user pricing
# @pt.target bridge.quota
# @pt.method unit
# @pt.legacy none
def test_no_hardcoded_prices():
    assert quota.FIVE_HOUR_MINUTES == 300 and quota.WEEKLY_MINUTES == 10080
    assert estimate_cost({"input_tokens": 10**9, "output_tokens": 10**9}, None) is None
    constants = [n for n in vars(quota) if n.isupper()]
    assert not any("PRICE" in n or "PRICING" in n or "COST" in n for n in constants)
