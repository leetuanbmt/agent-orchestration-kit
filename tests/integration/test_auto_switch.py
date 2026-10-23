"""Opt-in quota switching with fake Codex and isolated account homes."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bridge.config import BridgeConfig
from bridge.models import Actor, State
from tests.conftest import run, spec
from tests.integration.test_accounts_routing import calls


@pytest.fixture
def accounts(tmp_path):
    result = {}
    for name in ("a", "b", "low", "quota", "logged-out", "busy", "invalid"):
        home = tmp_path / name
        home.mkdir()
        if name != "logged-out":
            (home / "auth.json").touch()
        result[name] = {"codex_home": str(home)}
    return result


def seed(accounts, name, remaining):
    path = Path(accounts[name]["codex_home"]) / "sessions/2026/10/19/rollout-x.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {"primary": {
            "used_percent": 100 - remaining, "resets_at": 9999999999,
        }},
    }}) + "\n")


def create(bridge):
    return run(bridge.create_task(spec(account="a", verification_commands=[])))["task_id"]


def events(bridge, task, kind):
    return [event for event in bridge.store.events(task, 200) if event["kind"] == kind]


# @pt.id KIT-SWITCH-001-config
# @pt.item Auto-switch configuration validation
# @pt.condition Defaults, valid fallbacks, invalid names, duplicates, bounds and unknown keys
# @pt.expected Defaults are opt-in and invalid configurations are rejected
# @pt.target AutoSwitchConfig, BridgeConfig.validate_accounts
# @pt.method integration
# @pt.legacy none
def test_config():
    assert BridgeConfig().auto_switch.model_dump() == {
        "enabled": False, "min_remaining_pct": 10.0, "fallback_accounts": [],
    }
    assert BridgeConfig(auto_switch={"fallback_accounts": ["default"]})
    for policy in ({"extra": True}, {"fallback_accounts": ["missing"]},
                   {"fallback_accounts": ["default", "default"]},
                   {"min_remaining_pct": -1}, {"min_remaining_pct": 101}):
        with pytest.raises(ValidationError):
            BridgeConfig(auto_switch=policy)


# @pt.id KIT-SWITCH-002-execute
# @pt.item Execution selects the first eligible fallback
# @pt.condition Current quota below threshold with two eligible fallbacks
# @pt.expected Codex uses the first fallback and logs bridge from/to/reason
# @pt.target Bridge.execute_task, Bridge._rebind_account
# @pt.method integration
# @pt.legacy none
def test_execute(make_bridge, accounts, fake_codex):
    seed(accounts, "a", 5)
    seed(accounts, "b", 10)
    bridge = make_bridge(accounts=accounts, auto_switch={
        "enabled": True, "fallback_accounts": ["a", "b", "default"],
    })
    task = create(bridge)
    assert run(bridge.execute_task(task, wait=True))["state"] == "REVIEW"
    assert calls(fake_codex)[-1]["codex_home"] == accounts["b"]["codex_home"]
    event, = events(bridge, task, "account_auto_switched")
    assert event["actor"] == Actor.BRIDGE
    assert event["detail"] == {"from": "a", "to": "b", "reason": "below_threshold",
                               "remaining_pct": 5, "to_remaining_pct": 10}
    row = bridge.store.get(task)
    assert row["account"] == row["spec"]["account"] == "b"


# @pt.id KIT-SWITCH-003-eligibility
# @pt.item Ineligible fallbacks are skipped in order
# @pt.condition Low quota, quota state, logged-out, busy and invalid homes precede unknown quota
# @pt.expected The unknown-quota account is selected
# @pt.target Bridge._auto_switch
# @pt.method integration
# @pt.legacy none
def test_eligibility(make_bridge, accounts, fake_codex):
    seed(accounts, "a", 2)
    seed(accounts, "low", 9)
    accounts["invalid"] = {"codex_home": "relative-home"}
    bridge = make_bridge(accounts=accounts, auto_switch={"enabled": True,
        "fallback_accounts": ["low", "quota", "logged-out", "busy", "invalid", "b"]})
    bridge.store.set_account_state("quota", "quota", actor=Actor.BRIDGE)
    busy = run(bridge.create_task(spec(account="busy", objective="Busy task",
                                       sandbox="read-only")))["task_id"]
    bridge.store.transition(busy, State.RUNNING, Actor.CLIENT, expect={State.READY})
    task = create(bridge)
    run(bridge.execute_task(task, wait=True))
    assert calls(fake_codex)[-1]["codex_home"] == accounts["b"]["codex_home"]
    assert events(bridge, task, "account_auto_switched")[0]["detail"]["to_remaining_pct"] is None


# @pt.id KIT-SWITCH-004-no-trigger
# @pt.item Unknown or sufficient quota and disabled mode retain the account
# @pt.condition Unknown quota, quota at threshold, or disabled mode with low quota
# @pt.expected Codex uses the current account without switch events
# @pt.target Bridge._auto_switch
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("enabled,remaining", [(True, None), (True, 10), (False, 1)])
def test_no_trigger(make_bridge, accounts, fake_codex, enabled, remaining):
    if remaining is not None:
        seed(accounts, "a", remaining)
    bridge = make_bridge(accounts=accounts, auto_switch={
        "enabled": enabled, "fallback_accounts": ["b"],
    })
    task = create(bridge)
    run(bridge.execute_task(task, wait=True))
    assert calls(fake_codex)[-1]["codex_home"] == accounts["a"]["codex_home"]
    assert not events(bridge, task, "account_auto_switched")
    assert not events(bridge, task, "auto_switch_skipped")


# @pt.id KIT-SWITCH-005-no-fallback
# @pt.item No eligible fallback preserves the current binding
# @pt.condition Low quota with no eligible fallback and available or quota state
# @pt.expected Available account runs; quota account raises; both log a skipped switch
# @pt.target Bridge._auto_switch, Bridge._start
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("state", ["available", "quota"])
def test_no_fallback(make_bridge, accounts, fake_codex, state):
    seed(accounts, "a", 1)
    bridge = make_bridge(accounts=accounts, auto_switch={
        "enabled": True, "fallback_accounts": ["logged-out"],
    })
    bridge.store.set_account_state("a", state, actor=Actor.BRIDGE)
    task = create(bridge)
    if state == "quota":
        with pytest.raises(ValueError, match="account a unavailable"):
            run(bridge.execute_task(task, wait=True))
        assert not calls(fake_codex)
    else:
        run(bridge.execute_task(task, wait=True))
        assert calls(fake_codex)[-1]["codex_home"] == accounts["a"]["codex_home"]
    event, = events(bridge, task, "auto_switch_skipped")
    assert event["actor"] == Actor.BRIDGE
    assert event["detail"] == {"account": "a", "remaining_pct": 0 if state == "quota" else 1,
                               "reason": "no eligible fallback"}


# @pt.id KIT-SWITCH-006-resume
# @pt.item Quota-paused tasks restart with the full prompt on the fallback
# @pt.condition An existing session fails for quota and is resumed
# @pt.expected Old session is cleared before start and objective and feedback are sent
# @pt.target Bridge.resume_task, Bridge._rebind_account
# @pt.method integration
# @pt.legacy none
def test_resume(make_bridge, accounts, fake_codex, monkeypatch):
    bridge = make_bridge(accounts=accounts, auto_switch={
        "enabled": True, "fallback_accounts": ["b"],
    })
    task = create(bridge)
    first = run(bridge.execute_task(task, wait=True))
    assert first["session_id"]
    run(bridge.review_task(task, "rework", "Check again"))
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok,quota,ok")
    assert run(bridge.resume_task(task, wait=True))["state"] == "PAUSED"
    assert bridge.store.get(task)["session_id"]
    original_start = bridge._start

    async def check_start(task_id, kind, prompt, session, expect, wait, actor):
        assert session is None
        assert bridge.store.get(task_id)["session_id"] is None
        return await original_start(task_id, kind, prompt, session, expect, wait, actor)

    monkeypatch.setattr(bridge, "_start", check_start)
    assert run(bridge.resume_task(task, "Keep the objective", wait=True))["state"] == "REVIEW"
    call = calls(fake_codex)[-1]
    assert call["codex_home"] == accounts["b"]["codex_home"]
    assert call["argv"][:2] != ["exec", "resume"]
    assert spec()["objective"] in call["prompt"]
    assert "Keep the objective" in call["prompt"]
    assert events(bridge, task, "account_auto_switched")[0]["detail"]["reason"] == "quota"


# @pt.id KIT-SWITCH-007-auth
# @pt.item AUTH state never triggers a switch
# @pt.condition Auth-unavailable account with low quota and an eligible fallback
# @pt.expected Start refuses the current account without switching or logging a skip
# @pt.target Bridge._auto_switch, Bridge._start
# @pt.method integration
# @pt.legacy none
def test_auth(make_bridge, accounts, fake_codex):
    seed(accounts, "a", 1)
    bridge = make_bridge(accounts=accounts, auto_switch={
        "enabled": True, "fallback_accounts": ["b"],
    })
    bridge.store.set_account_state("a", "auth", actor=Actor.BRIDGE)
    task = create(bridge)
    with pytest.raises(ValueError, match="account a unavailable"):
        run(bridge.execute_task(task, wait=True))
    assert bridge.store.get(task)["account"] == "a"
    assert not calls(fake_codex)
    assert not events(bridge, task, "account_auto_switched")
    assert not events(bridge, task, "auto_switch_skipped")
