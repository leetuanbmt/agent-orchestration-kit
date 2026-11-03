"""Account quota reporting through the bridge service."""

import json
import os
import time

import pytest

from bridge.config import BridgeConfig
from bridge.models import Actor
from bridge.service import Bridge
from tests.conftest import git, run, spec


# @pt.id KIT-QUOTA-008-reporting
# @pt.item Per-account quota reporting
# @pt.condition Named account has a rollout and other accounts have no data
# @pt.expected Listing returns the snapshot and health returns remaining quota or UNKNOWN
# @pt.target bridge.service.Bridge.list_accounts, bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none
def test_quota_reporting(tmp_path, monkeypatch, make_bridge):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "default"))
    home = tmp_path / "work"
    sessions = home / "sessions" / "2026" / "10" / "19"
    sessions.mkdir(parents=True)
    primary = {"used_percent": 25.0, "window_minutes": 300, "resets_at": 9999999999}
    secondary = {"used_percent": 40.0, "window_minutes": 10080, "resets_at": 9999999999}
    path = sessions / "rollout-test.jsonl"
    path.write_text(json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": {
            "primary": primary, "secondary": secondary, "plan_type": "plus"}}}) + "\n")
    os.utime(path, (100, 100))
    bridge = make_bridge(accounts={"work": {"codex_home": str(home)},
                                   "empty": {"codex_home": str(tmp_path / "empty")}})
    listing = {account["name"]: account["quota"]
               for account in bridge.list_accounts()["accounts"]}
    assert listing["work"] == {"remaining_pct": 60.0, "primary": primary,
                                "secondary": secondary, "plan_type": "plus",
                                "observed_at": 100.0}
    assert listing["default"] == listing["empty"] == "UNKNOWN"
    assert run(bridge.health())["quota"] == {
        "default": "UNKNOWN", "empty": "UNKNOWN", "work": 60.0}


# @pt.id KIT-QUOTA-009-misconfigured-account
# @pt.item Quota reporting tolerates a rejected account
# @pt.condition Discovery lists an account whose codex_home resolve rejects
# @pt.expected Listing and health return UNKNOWN for the account without raising
# @pt.target bridge.service.Bridge.list_accounts, bridge.service.Bridge.health
# @pt.method integration
# @pt.legacy none
def test_misconfigured_account_quota(monkeypatch, make_bridge):
    bridge = make_bridge(accounts={"broken": {"codex_home": "relative-home"}})
    monkeypatch.setattr("bridge.service.discover", lambda cfg, root: {
        "accounts": [{"name": "default"}, {"name": "broken"}]})
    listing = {account["name"]: account["quota"]
               for account in bridge.list_accounts()["accounts"]}
    assert listing == {"default": "UNKNOWN", "broken": "UNKNOWN"}
    assert run(bridge.health())["quota"] == listing


# @pt.id KIT-QUOTA-010-display-after-reset
# @pt.item The CLI and dashboard show quota the way routing computes it
# @pt.condition a CLI reported the 5h window 95% used and that window's reset time has passed; the week window is 30% used
# @pt.expected render shows 5h 100% left and 70% overall, equal to the listing's remaining_pct that routing uses
# @pt.target bridge.render.remaining_pct, bridge.render.quota_text, bridge.service.Bridge.list_accounts
# @pt.method integration
# @pt.legacy render ignored resets_at and showed 5% left
def test_quota_display_after_reset(make_bridge):
    from bridge.render import quota_text, remaining_pct
    bridge = make_bridge()
    bridge.store.save_quota("default", {
        "primary": {"used_percent": 95.0, "window_minutes": 300, "resets_at": 1},
        "secondary": {"used_percent": 30.0, "window_minutes": 10080, "resets_at": 9999999999}})
    quota, = (a["quota"] for a in bridge.list_accounts()["accounts"] if a["name"] == "default")
    assert remaining_pct(quota) == quota["remaining_pct"] == 70.0
    assert quota_text(quota) == "5h 100% left · week 70% left"
    assert quota_text({"primary": {"used_percent": "42"}}) == "UNKNOWN"  # bad data, no crash


def reset_snapshot(reset, weekly_used=62):
    return {"remaining_pct": 0, "primary": {
        "used_percent": 100, "window_minutes": 300, "resets_at": reset},
        "secondary": {"used_percent": weekly_used, "window_minutes": 10080,
                      "resets_at": 9999999999}}


# @pt.id KIT-QUOTA-011-lapse
# @pt.item Quota marks lapse after an open window resets
# @pt.condition A Codex 5h window resets after the mark while weekly usage is 62 percent
# @pt.expected Listing and routing allow the account, log a bridge event and share the lift
# @pt.target Bridge._account_state, Bridge.list_accounts, Bridge._unavailable
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("entry", ["listing", "routing"])
def test_quota_mark_lapses(tmp_path, make_bridge, monkeypatch, entry):
    home = tmp_path / "work"
    sessions = home / "sessions/2026/11/02"
    sessions.mkdir(parents=True)
    (home / "auth.json").touch()
    bridge = make_bridge(accounts={"work": {"codex_home": str(home)}})
    key = bridge._key("work")
    marked = time.time()
    bridge.store.set_account_state("work", "quota", "limit", actor=Actor.BRIDGE, key=key)
    reset = marked + 60
    (sessions / "rollout-reset.jsonl").write_text(json.dumps({
        "type": "event_msg", "payload": {"type": "token_count",
        "rate_limits": reset_snapshot(reset)}}) + "\n")
    monkeypatch.setattr("bridge.service.time.time", lambda: reset + 60)
    other_root = tmp_path / "other"
    other_root.mkdir()
    git(other_root, "init", "-q")
    other = Bridge(other_root, BridgeConfig(accounts={"work": {"codex_home": str(home)}}))
    assert other.store.account_state("work", key=key)["state"] == "quota"
    agent = bridge._executors_on("work")[0]
    if entry == "routing":
        assert bridge._unavailable(agent, "write") is None
    listing = {a["name"]: a for a in bridge.list_accounts()["accounts"]}
    assert listing["work"]["state"] == "available"
    assert listing["work"]["last_error"] is None
    assert bridge._unavailable(agent, "write") is None
    assert other.store.account_state("work", key=key)["state"] == "available"
    events = bridge.store.db.execute(
        "SELECT actor, to_state FROM events WHERE kind='account_state' ORDER BY id"
    ).fetchall()
    assert [(e["actor"], e["to_state"]) for e in events] == [
        (Actor.BRIDGE, "quota"), (Actor.BRIDGE, "available")]


# @pt.id KIT-QUOTA-012-keeps-marks
# @pt.item Only a proven reset with positive quota lifts QUOTA
# @pt.condition Weekly exhausted, reset before mark, unknown snapshot, nonnumeric reset, missing mark time, unknown remaining or AUTH
# @pt.expected The original state and error remain and no lift event is written
# @pt.target Bridge._account_state
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("case", ["weekly", "old_reset", "equal_reset", "future_reset",
                                 "unknown", "nonnumeric", "boolean", "missing_time",
                                 "no_remaining", "auth"])
def test_quota_mark_stays(make_bridge, monkeypatch, case):
    bridge = make_bridge()
    key = bridge._key("default")
    state = "auth" if case == "auth" else "quota"
    bridge.store.set_account_state("default", state, "keep", actor=Actor.BRIDGE, key=key)
    original = bridge.store.account_state("default", key=key)
    marked = original["updated_at"]
    snapshot = reset_snapshot(marked + 10, 100 if case == "weekly" else 62)
    if case in {"old_reset", "equal_reset", "future_reset", "nonnumeric", "boolean"}:
        snapshot["primary"]["resets_at"] = {
            "old_reset": marked - 1, "equal_reset": marked, "future_reset": marked + 100,
            "nonnumeric": "yesterday", "boolean": True}[case]
    if case == "unknown":
        snapshot = "UNKNOWN"
    if case == "no_remaining":
        snapshot["primary"].pop("used_percent")
        snapshot["secondary"] = None
    if case == "missing_time":
        original.pop("updated_at")
        monkeypatch.setattr(bridge.store, "account_state", lambda *a, **kw: original)
    monkeypatch.setattr(bridge, "_account_quota", lambda name: snapshot)
    monkeypatch.setattr("bridge.service.time.time", lambda: marked + 20)
    assert bridge._account_state("default", key=key) == original
    assert bridge.store.db.execute(
        "SELECT COUNT(*) FROM events WHERE kind='account_state'"
    ).fetchone()[0] == 1


# @pt.id KIT-QUOTA-013-resume-fallback
# @pt.item Resume and quota fallback use the lapsed state
# @pt.condition A paused task has a quota mark followed by a reset with positive quota
# @pt.expected Resume reaches REVIEW on its original account without a fallback event
# @pt.target Bridge.resume_task, Bridge._quota_fallback, Bridge._start
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("fallback", [False, True])
def test_resume_after_quota_reset(make_bridge, monkeypatch, fallback):
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "quota,ok")
    bridge = make_bridge(auto_switch={"enabled": fallback, "fallback_accounts": ["default"]})
    task = run(bridge.create_task(spec(verification_commands=[])))["task_id"]
    assert run(bridge.execute_task(task, wait=True))["state"] == "PAUSED"
    marked = bridge.store.account_state("default", key=bridge._key("default"))["updated_at"]
    snapshot = reset_snapshot(marked + 10)
    snapshot["remaining_pct"] = 38
    monkeypatch.setattr(bridge, "_account_quota", lambda name: snapshot)
    monkeypatch.setattr("bridge.service.time.time", lambda: marked + 20)
    assert run(bridge.resume_task(task, wait=True))["state"] == "REVIEW"
    assert not [e for e in bridge.store.events(task, 200)
                if e["kind"] in {"account_auto_switched", "auto_switch_skipped"}]
