"""Opt-in failover off accounts over their configured limits, the loop guard and the cooldown."""

import json
import time

import pytest

from bridge.models import Actor
from bridge.service import Bridge
from tests.conftest import run, spec
from tests.integration.test_orchestration import events, log

WRITE = "src/app.py=x = 2\n"
FIVE_HOUR = "{five_hour_pct: 80}"
LIMIT_TEXT = "five_hour_pct: 90.0% used >= 80%"


def config(cl1: str = "", cl2: str = "", cl3: str = "",
           fallback: str = "{enabled: true}") -> str:
    def limits(text: str) -> str:
        return f", limits: {text}" if text else ""
    return f"""
version: 1
accounts:
  cl1: {{provider: anthropic, home: {{home}}/cl1{limits(cl1)}}}
  cl2: {{provider: anthropic, home: {{home}}/cl2{limits(cl2)}}}
  cl3: {{provider: anthropic, home: {{home}}/cl3{limits(cl3)}}}
agents:
  claude1: {{adapter: claude-cli, account: cl1, allow_write: true}}
  claude2: {{adapter: claude-cli, account: cl2, allow_write: true}}
  claude3: {{adapter: claude-cli, account: cl3, allow_write: true}}
roles: {{executor: {{agents: [claude1, claude2, claude3]}}}}
workflow: {{stages: [execute, test]}}
routing: {{quota_fallback: {fallback}}}
"""


def seed(b: Bridge, name: str, used: float = 90, resets_at: float | None = None) -> None:
    """Provider usage as the CLI last reported it: a five-hour window `used` percent full."""
    reset = time.time() + 3600 if resets_at is None else resets_at
    b.store.save_quota(name, {"primary": {"used_percent": used, "window_minutes": 300,
                                          "resets_at": reset}}, key=b._key(name))


def task(b: Bridge, objective: str = "Set x to 2", **over: object) -> str:
    return run(b.create_task(spec(objective=objective, **over)))["task_id"]


def mark_quota(b: Bridge, name: str, age_s: float = 0) -> None:
    """Mark `name` QUOTA as the bridge does, then date the mark `age_s` seconds back."""
    key = b._key(name)
    b.store.set_account_state(name, "quota", "limit hit", actor=Actor.BRIDGE, key=key)
    age(b, name, age_s)


def age(b: Bridge, name: str, age_s: float) -> None:
    when = time.time() - age_s
    b.store.db.execute("UPDATE accounts SET updated_at=? WHERE name=?", (when, name))
    key = b._key(name)
    if b.store.shared is not None and key is not None:
        b.store.shared.execute("UPDATE accounts SET updated_at=? WHERE key=?", (when, key))


def state(b: Bridge, name: str) -> str:
    return b._account_state(name, key=b._key(name))["state"]


def cooldown_events(b: Bridge) -> list[dict]:
    rows = b.store.db.execute(
        "SELECT detail FROM events WHERE kind='account_cooldown_elapsed' ORDER BY id").fetchall()
    return [json.loads(row[0]) for row in rows]


def homes(fake_claude) -> list[str]:
    return [call["config_dir"].rsplit("/", 1)[1] for call in log(fake_claude)]


# @pt.id KIT-FAILOVER-001-execute
# @pt.item A bound task moves off an account over its limit at execute
# @pt.condition Fallback enabled; the task names claude1; cl1 has five_hour_pct 80 and reports 90 % used
# @pt.expected account_auto_switched to claude2 with reason limit and the limit text; the task finishes DONE on cl2
# @pt.target Bridge._quota_fallback, Bridge.execute_task
# @pt.method integration
# @pt.legacy none
def test_execute_moves_off_limited_account(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    b = make_orch(config(cl1=FIVE_HOUR))
    seed(b, "cl1")
    t = task(b, agent="claude1")
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == "DONE" and out["agent"] == "claude2" and out["account"] == "cl2"
    moved, = events(b, t, "account_auto_switched")
    assert moved["actor"] == Actor.BRIDGE
    detail = moved["detail"]
    assert detail["from"] == "claude1" and detail["to"] == "claude2"
    assert detail["from_account"] == "cl1" and detail["to_account"] == "cl2"
    assert detail["reason"] == "limit" and detail["limit"].startswith(LIMIT_TEXT)
    assert detail["remaining_pct"] == pytest.approx(10.0) and detail["to_remaining_pct"] is None
    assert homes(fake_claude) == ["cl2"]
    assert events(b, t, "limit_blocked") == []


# @pt.id KIT-FAILOVER-002-resume
# @pt.item A bound task moves off an account over its limit at resume
# @pt.condition Fallback enabled; the first run on claude1 fails its checks (REWORK); cl1 then reports 90 % of a five_hour_pct 80 limit
# @pt.expected The resume moves the task to claude2 with reason limit and it finishes DONE on cl2
# @pt.target Bridge._quota_fallback, Bridge.resume_task
# @pt.method integration
# @pt.legacy none
def test_resume_moves_off_limited_account(make_orch, fake_claude, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE_WRITE", raising=False)
    b = make_orch(config(cl1=FIVE_HOUR))
    t = task(b, agent="claude1")
    assert run(b.execute_task(t, wait=True))["state"] == "REWORK"
    assert events(b, t, "account_auto_switched") == []
    seed(b, "cl1")
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    out = run(b.resume_task(t, wait=True))
    assert out["state"] == "DONE" and out["agent"] == "claude2"
    moved, = events(b, t, "account_auto_switched")
    assert moved["detail"]["reason"] == "limit"
    assert moved["detail"]["limit"].startswith(LIMIT_TEXT)
    assert homes(fake_claude) == ["cl1", "cl2"]


# @pt.id KIT-FAILOVER-003-chain-direct
# @pt.item Failover skips every candidate over its own limit
# @pt.condition Fallback enabled; cl1 and cl2 both have five_hour_pct 80 and report 90 %; cl3 has no limit
# @pt.expected The task bound to claude1 moves straight to claude3 in one switch and finishes there
# @pt.target Bridge._quota_fallback, Bridge._unavailable
# @pt.method integration
# @pt.legacy none
def test_chain_direct(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    b = make_orch(config(cl1=FIVE_HOUR, cl2=FIVE_HOUR))
    seed(b, "cl1")
    seed(b, "cl2")
    t = task(b, agent="claude1")
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == "DONE" and out["agent"] == "claude3"
    moved, = events(b, t, "account_auto_switched")
    assert moved["detail"]["to"] == "claude3" and moved["detail"]["reason"] == "limit"
    assert homes(fake_claude) == ["cl3"]


# @pt.id KIT-FAILOVER-004-chain-sequential
# @pt.item Failover again once the fallback account goes over its own limit
# @pt.condition Fallback enabled; the task moves from claude1 (cl1 over limit) to claude2 and fails its checks there; cl2 then reports 90 % of its five_hour_pct 80
# @pt.expected The resume moves it again to claude3; two account_auto_switched events with reason limit; DONE on cl3
# @pt.target Bridge._quota_fallback, Bridge.resume_task
# @pt.method integration
# @pt.legacy none
def test_chain_sequential(make_orch, fake_claude, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE_WRITE", raising=False)
    b = make_orch(config(cl1=FIVE_HOUR, cl2=FIVE_HOUR))
    seed(b, "cl1")
    t = task(b, agent="claude1")
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == "REWORK" and out["agent"] == "claude2"
    seed(b, "cl2")
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    out = run(b.resume_task(t, wait=True))
    assert out["state"] == "DONE" and out["agent"] == "claude3"
    first, second = events(b, t, "account_auto_switched")
    assert (first["detail"]["from"], first["detail"]["to"]) == ("claude1", "claude2")
    assert (second["detail"]["from"], second["detail"]["to"]) == ("claude2", "claude3")
    assert first["detail"]["reason"] == second["detail"]["reason"] == "limit"
    assert homes(fake_claude) == ["cl2", "cl3"]


# @pt.id KIT-FAILOVER-005-loop-guard
# @pt.item A per-task cap on automatic moves
# @pt.condition Fallback enabled with max_switches 1; the task already moved once (claude1 -> claude2) and cl2 then goes over its limit
# @pt.expected auto_switch_skipped 'switch limit reached (1/1)'; the resume is refused with the limit ValueError; the task stays on claude2 and nothing else ran
# @pt.target Bridge._quota_fallback, Bridge._start
# @pt.method integration
# @pt.legacy none
def test_loop_guard(make_orch, fake_claude, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE_WRITE", raising=False)
    b = make_orch(config(cl1=FIVE_HOUR, cl2=FIVE_HOUR,
                         fallback="{enabled: true, max_switches: 1}"))
    seed(b, "cl1")
    t = task(b, agent="claude1")
    assert run(b.execute_task(t, wait=True))["agent"] == "claude2"
    seed(b, "cl2")
    with pytest.raises(ValueError, match="account cl2 over its configured limit: five_hour_pct"):
        run(b.resume_task(t, wait=True))
    skipped, = events(b, t, "auto_switch_skipped")
    assert skipped["detail"] == {"account": "cl2", "reason": "switch limit reached (1/1)"}
    assert len(events(b, t, "account_auto_switched")) == 1
    blocked, = events(b, t, "limit_blocked")
    assert blocked["detail"]["account"] == "cl2"
    row = b.store.get(t)
    assert row["agent"] == "claude2" and row["state"] == "REWORK" and row["attempts"] == 1
    assert homes(fake_claude) == ["cl2"]


# @pt.id KIT-FAILOVER-006-keep-sessions
# @pt.item Session affinity keeps a task with an agent session on its account
# @pt.condition Fallback enabled with keep_sessions true; the task names claude1 and has session s1; cl1 is over its five_hour_pct limit
# @pt.expected auto_switch_skipped with the keep_sessions reason, no move, and the start is refused with the limit ValueError
# @pt.target Bridge._quota_fallback, Bridge._start
# @pt.method integration
# @pt.legacy none
def test_keep_sessions(make_orch, fake_claude):
    b = make_orch(config(cl1=FIVE_HOUR, fallback="{enabled: true, keep_sessions: true}"))
    seed(b, "cl1")
    t = task(b, agent="claude1")
    b.store.update(t, session_id="s1")
    with pytest.raises(ValueError, match="account cl1 over its configured limit"):
        run(b.execute_task(t, wait=True))
    skipped, = events(b, t, "auto_switch_skipped")
    assert skipped["detail"] == {"account": "cl1",
                                 "reason": "keep_sessions: the task has an agent session"}
    assert events(b, t, "account_auto_switched") == []
    row = b.store.get(t)
    assert row["agent"] == "claude1" and row["session_id"] == "s1" and row["attempts"] == 0
    assert log(fake_claude) == []


# @pt.id KIT-FAILOVER-007-disabled
# @pt.item Without the opt-in a limit breach never moves a task
# @pt.condition Fallback disabled; the task names claude1; cl1 is over its five_hour_pct limit; claude2 is free
# @pt.expected The start is refused with the limit ValueError; no switch or skip events; the task stays on claude1
# @pt.target Bridge._quota_fallback, Bridge._start
# @pt.method integration
# @pt.legacy none
def test_disabled(make_orch, fake_claude):
    b = make_orch(config(cl1=FIVE_HOUR, fallback="{enabled: false}"))
    seed(b, "cl1")
    t = task(b, agent="claude1")
    with pytest.raises(ValueError, match="account cl1 over its configured limit"):
        run(b.execute_task(t, wait=True))
    assert events(b, t, "account_auto_switched") == []
    assert events(b, t, "auto_switch_skipped") == []
    assert b.store.get(t)["agent"] == "claude1"
    assert log(fake_claude) == []


# @pt.id KIT-FAILOVER-008-exhausted
# @pt.item Every fallback candidate over its limit
# @pt.condition Fallback enabled; cl1, cl2 and cl3 all have five_hour_pct 80 and report 90 %
# @pt.expected auto_switch_skipped 'no eligible fallback' with the limit reason; the start is refused; nothing ran
# @pt.target Bridge._quota_fallback, Bridge._start
# @pt.method integration
# @pt.legacy none
def test_all_candidates_exhausted(make_orch, fake_claude):
    b = make_orch(config(cl1=FIVE_HOUR, cl2=FIVE_HOUR, cl3=FIVE_HOUR))
    for name in ("cl1", "cl2", "cl3"):
        seed(b, name)
    t = task(b, agent="claude1")
    with pytest.raises(ValueError, match="account cl1 over its configured limit"):
        run(b.execute_task(t, wait=True))
    skipped, = events(b, t, "auto_switch_skipped")
    detail = skipped["detail"]
    assert detail["account"] == "cl1" and detail["reason"] == "no eligible fallback"
    assert detail["limit"].startswith(LIMIT_TEXT)
    assert detail["remaining_pct"] == pytest.approx(10.0)
    assert events(b, t, "account_auto_switched") == []
    row = b.store.get(t)
    assert row["agent"] == "claude1" and row["state"] == "READY" and row["attempts"] == 0
    assert log(fake_claude) == []


# @pt.id KIT-FAILOVER-009-cooldown
# @pt.item A QUOTA mark with no readable reset lifts after cooldown_s
# @pt.condition cl1 has cooldown_s 600 and no quota snapshot; it is marked QUOTA 590 s ago, then 610 s ago
# @pt.expected It stays QUOTA before the cooldown; afterwards it becomes available with one account_cooldown_elapsed event {name, cooldown_s}
# @pt.target Bridge._account_state
# @pt.method integration
# @pt.legacy none
def test_cooldown_lifts_quota_mark(make_orch, fake_claude):
    b = make_orch(config(cl1="{cooldown_s: 600}"))
    assert b._account_quota("cl1") == "UNKNOWN"
    mark_quota(b, "cl1", age_s=590)
    assert state(b, "cl1") == "quota"
    assert cooldown_events(b) == []
    age(b, "cl1", 610)
    assert state(b, "cl1") == "available"
    assert cooldown_events(b) == [{"name": "cl1", "cooldown_s": 600}]
    assert b._unavailable("claude1", "write") is None


# @pt.id KIT-FAILOVER-010-cooldown-future-reset
# @pt.item A known future reset overrides the cooldown
# @pt.condition cl1 has cooldown_s 600; its snapshot shows a five-hour window resetting in an hour; it was marked QUOTA 610 s ago
# @pt.expected The account stays QUOTA and no account_cooldown_elapsed event is logged
# @pt.target Bridge._account_state
# @pt.method integration
# @pt.legacy none
def test_cooldown_waits_for_known_reset(make_orch, fake_claude):
    b = make_orch(config(cl1="{cooldown_s: 600}"))
    seed(b, "cl1", used=100)
    mark_quota(b, "cl1", age_s=610)
    assert state(b, "cl1") == "quota"
    assert cooldown_events(b) == []


# @pt.id KIT-FAILOVER-011-no-cooldown
# @pt.item Accounts without cooldown_s keep the QUOTA mark
# @pt.condition cl2 has no limits and cl3 has limits without cooldown_s; both are marked QUOTA a week ago with no snapshot
# @pt.expected Both stay QUOTA and no account_cooldown_elapsed event is logged
# @pt.target Bridge._account_state
# @pt.method integration
# @pt.legacy none
def test_no_cooldown_keeps_mark(make_orch, fake_claude):
    b = make_orch(config(cl3=FIVE_HOUR))
    for name in ("cl2", "cl3"):
        mark_quota(b, name, age_s=7 * 86400)
        assert state(b, name) == "quota"
    assert cooldown_events(b) == []


# @pt.id KIT-FAILOVER-012-workflow
# @pt.item The workflow driver fails over a limit-blocked task
# @pt.condition Fallback enabled; a workflow task names claude1; cl1 is over its five_hour_pct limit
# @pt.expected run_workflow moves the task to claude2 and the workflow ends DONE
# @pt.target Bridge._drive, Bridge._quota_fallback
# @pt.method integration
# @pt.legacy none
def test_workflow_fails_over(make_orch, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", WRITE)
    b = make_orch(config(cl1=FIVE_HOUR))
    seed(b, "cl1")
    wid = b.store.create_workflow("Set x to 2", None, "READY")["id"]
    t = b._insert(*b._prepare(spec(agent="claude1")), wid)[0]["id"]
    wf = run(b.run_workflow(wid, wait=True))
    assert wf["state"] == "DONE"
    summary, = wf["tasks"]
    assert summary["task_id"] == t and summary["agent"] == "claude2"
    assert summary["state"] == "DONE"
    assert len(events(b, t, "account_auto_switched")) == 1
    assert homes(fake_claude) == ["cl2"]


# @pt.id KIT-FAILOVER-013-workflow-exhausted
# @pt.item The workflow driver does not loop when every account is over its limit
# @pt.condition Fallback enabled; a workflow task names claude1; cl1, cl2 and cl3 are all over their five_hour_pct limits
# @pt.expected The workflow ends WAITING with the limit reason for the task; one skip event, no switch, nothing ran
# @pt.target Bridge._drive, Bridge._quota_fallback
# @pt.method integration
# @pt.legacy none
def test_workflow_all_limited_waits(make_orch, fake_claude):
    b = make_orch(config(cl1=FIVE_HOUR, cl2=FIVE_HOUR, cl3=FIVE_HOUR))
    for name in ("cl1", "cl2", "cl3"):
        seed(b, name)
    wid = b.store.create_workflow("Set x to 2", None, "READY")["id"]
    t = b._insert(*b._prepare(spec(agent="claude1")), wid)[0]["id"]
    wf = run(b.run_workflow(wid, wait=True))
    assert wf["state"] == "WAITING"
    assert "over its configured limit" in wf["detail"]["waiting"][t]
    assert len(events(b, t, "auto_switch_skipped")) == 1
    assert events(b, t, "account_auto_switched") == []
    assert b.store.get(t)["state"] == "READY"
    assert log(fake_claude) == []
