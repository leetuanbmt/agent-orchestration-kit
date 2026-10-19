"""Quota snapshots from isolated session rollouts."""

import json
import os

import pytest

from bridge.accounts import Account
from bridge.quota import codex_home_for, read_quota, remaining_pct


def rollout(home, name, limits, mtime):
    path = home / "sessions" / "2026" / "10" / "19" / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": limits}}) + "\n")
    os.utime(path, (mtime, mtime))
    return path


# @pt.id KIT-QUOTA-001-home
# @pt.item Account home resolution
# @pt.condition Named home and inherited environment are resolved
# @pt.expected Named home wins and empty environment falls back to HOME
# @pt.target bridge.quota.codex_home_for
# @pt.method unit
# @pt.legacy none
def test_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "inherited"))
    assert codex_home_for(Account("work", tmp_path / "work")) == tmp_path / "work"
    assert codex_home_for(Account("default", None)) == tmp_path / "inherited"
    monkeypatch.setenv("CODEX_HOME", "")
    assert codex_home_for(Account("default", None)) == tmp_path / ".codex"
    monkeypatch.delenv("CODEX_HOME")
    assert codex_home_for(Account("default", None)) == tmp_path / ".codex"


# @pt.id KIT-QUOTA-002-missing
# @pt.item Missing session data
# @pt.condition Sessions directory does not exist
# @pt.expected No snapshot is returned
# @pt.target bridge.quota.read_quota
# @pt.method unit
# @pt.legacy none
def test_missing(tmp_path):
    assert read_quota(tmp_path) is None


# @pt.id KIT-QUOTA-003-newest
# @pt.item Snapshot ordering and fallback
# @pt.condition Newest rollout has quota or contains no token event
# @pt.expected Newest valid snapshot wins with fallback to older file
# @pt.target bridge.quota.read_quota
# @pt.method unit
# @pt.legacy none
def test_newest(tmp_path):
    rollout(tmp_path, "old", {"primary": {"used_percent": 70}}, 10)
    newest = rollout(tmp_path, "new", {"primary": {"used_percent": 20}}, 20)
    assert read_quota(tmp_path, now=0)["remaining_pct"] == 80.0
    assert read_quota(tmp_path, now=0)["observed_at"] == 20
    newest.write_text('{"type": "event_msg", "payload": {"type": "other"}}\n')
    os.utime(newest, (20, 20))
    assert read_quota(tmp_path, now=0)["remaining_pct"] == 30.0


# @pt.id KIT-QUOTA-004-last-event
# @pt.item Event filtering
# @pt.condition Malformed and unrelated lines surround multiple token events
# @pt.expected Last valid token snapshot wins with missing fields represented as None
# @pt.target bridge.quota.read_quota
# @pt.method unit
# @pt.legacy none
def test_last_event(tmp_path):
    path = rollout(tmp_path, "events", {"primary": {"used_percent": 90}}, 10)
    with path.open("a") as stream:
        stream.write('broken\n[]\n{"type":"other"}\n')
        stream.write(json.dumps({"type": "event_msg", "payload": {
            "type": "token_count", "rate_limits": {"primary": {"used_percent": 12},
                                                   "plan_type": "plus"}}}) + "\n")
        stream.write('{"type":"event_msg","payload":{"type":"other","rate_limits":{}}}\n')
    result = read_quota(tmp_path, now=0)
    assert result["remaining_pct"] == 88.0
    assert result["primary"] == {"used_percent": 12, "window_minutes": None, "resets_at": None}
    assert result["secondary"] is None
    assert result["plan_type"] == "plus"


# @pt.id KIT-QUOTA-005-windows
# @pt.item Quota window calculation
# @pt.condition Windows have missing, invalid, expired, or differing usage
# @pt.expected Maximum active usage determines remaining quota and no valid usage returns None
# @pt.target bridge.quota.remaining_pct
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("limits,expected", [
    ({}, None),
    ({"primary": None, "secondary": {"used_percent": "20"}}, None),
    ({"primary": {"used_percent": True}}, None),
    ({"primary": {"used_percent": 80, "resets_at": 99}}, 100.0),
    ({"primary": {"used_percent": 80, "resets_at": 100}}, 100.0),
    ({"primary": {"used_percent": 80, "resets_at": 101},
      "secondary": {"used_percent": 30}}, 20.0),
    ({"primary": {"used_percent": 10}, "secondary": {"used_percent": 60}}, 40.0),
    ({"secondary": {"used_percent": 120}}, 0.0),
])
def test_windows(limits, expected):
    assert remaining_pct(limits, 100) == expected


# @pt.id KIT-QUOTA-006-symlinks
# @pt.item Rollout symlink exclusion
# @pt.condition File and directory symlinks point outside sessions
# @pt.expected Symlinked quota is ignored
# @pt.target bridge.quota.read_quota
# @pt.method unit
# @pt.legacy none
def test_symlinks(tmp_path):
    outside = rollout(tmp_path / "outside", "external", {}, 10)
    home = tmp_path / "home"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "rollout-link.jsonl").symlink_to(outside)
    (sessions / "linked").symlink_to(outside.parent, target_is_directory=True)
    assert read_quota(home) is None


# @pt.id KIT-QUOTA-007-file-limit
# @pt.item Rollout scan bound
# @pt.condition Only the twenty-first newest rollout has quota
# @pt.expected No snapshot is returned beyond the scan limit
# @pt.target bridge.quota.read_quota
# @pt.method unit
# @pt.legacy none
def test_file_limit(tmp_path):
    rollout(tmp_path, "old", {}, 1)
    for index in range(20):
        path = rollout(tmp_path, str(index), {}, index + 2)
        path.write_text("malformed\n")
        os.utime(path, (index + 2, index + 2))
    assert read_quota(tmp_path) is None
