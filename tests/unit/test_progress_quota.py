import json
import os
from types import SimpleNamespace

import pytest

from bridge import progress
from bridge.state import Store


@pytest.fixture
def dashboard(repo, tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    orch = progress.orchestrator.parse(
        f"accounts:\n  work: {{provider: openai, home: {home}}}\n"
        "agents:\n  coder: {adapter: codex-cli, account: work}\n"
        "roles:\n  executor: coder\n"
        "workflow:\n  stages: [execute, test]\n", "test.yaml")
    monkeypatch.setattr(progress.config, "load_config", lambda root: SimpleNamespace())
    monkeypatch.setattr(progress.orchestrator, "load", lambda root, cfg: orch)
    monkeypatch.setattr(progress, "_QUOTA_CACHE", {})
    with_store = Store(repo / ".git/agent-bridge/bridge.db")
    yield home, orch, with_store, lambda: progress.snapshot([str(repo)], now=100)["accounts"][0]
    with_store.db.close()


def rollout(home, used=25, observed=90):
    limits = {"primary": {"used_percent": used, "window_minutes": 300,
                          "resets_at": 4000000000}}
    path = home / "sessions/2026/10/19/rollout-test.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "rate_limits": limits}}) + "\n")
    os.utime(path, (observed, observed))
    return limits


# @pt.id T0116-rollout
# @pt.item Dashboard Codex quota fallback
# @pt.condition Rollout quota with absent, older or newer DB quota
# @pt.expected Newest observation supplies remaining percentage and windows
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("db_ts", [None, 80, 95])
def test_snapshot_rollout_quota(dashboard, db_ts):
    home, _, store, snapshot = dashboard
    rollout(home)
    if db_ts is not None:
        store.save_quota("work", {"primary": {"used_percent": 60, "window_minutes": 300,
                                               "resets_at": 4000000000}})
        store.db.execute("UPDATE account_quota SET observed_at=?", (db_ts,))
    entry = snapshot()
    used = 60 if db_ts == 95 else 25
    assert entry["remaining_pct"] == 100 - used
    assert entry["observed_at"] == (95 if db_ts == 95 else 90)
    assert entry["windows"] == [{"slot": "primary", "window_minutes": 300,
                                 "used_pct": used, "resets_at": 4000000000}]


# @pt.id T0116-failure
# @pt.item Quota resolution and read failures
# @pt.condition Account home cannot resolve or rollout reader raises
# @pt.expected Snapshot preserves empty quota without raising
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("failure", ["resolve", "read"])
def test_snapshot_quota_failure(dashboard, monkeypatch, failure):
    _, _, _, snapshot = dashboard
    def fail(*args, **kwargs):
        raise ValueError("unavailable home")
    if failure == "resolve":
        monkeypatch.setattr(progress.accounts, "resolve_profile", fail)
    else:
        monkeypatch.setattr(progress.quota, "read_quota", fail)
    entry = snapshot()
    assert entry["remaining_pct"] is None
    assert entry["windows"] == []
    assert entry["observed_at"] is None


# @pt.id T0116-cache
# @pt.item Rollout cache ceiling
# @pt.condition Same home polled before and at 30 seconds and another home polled
# @pt.expected Each home is read once until its 30 second cache expires
# @pt.target bridge.progress._rollout_quota
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("initial", [None, {"observed_at": 90}])
def test_rollout_cache(tmp_path, monkeypatch, initial):
    monkeypatch.setattr(progress, "_QUOTA_CACHE", {})
    ticks = [0]
    calls = []
    monkeypatch.setattr(progress.time, "monotonic", lambda: ticks[0])
    def read(home):
        calls.append(home)
        return initial
    monkeypatch.setattr(progress.quota, "read_quota", read)
    home = tmp_path / "one"
    assert progress._rollout_quota(home) == initial
    ticks[0] = 29
    assert progress._rollout_quota(home) == initial
    progress._rollout_quota(tmp_path / "two")
    assert calls == [home, tmp_path / "two"]
    ticks[0] = 30
    progress._rollout_quota(home)
    assert calls == [home, tmp_path / "two", home]
