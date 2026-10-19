"""Account quota reporting through the bridge service."""

import json
import os

from tests.conftest import run


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
