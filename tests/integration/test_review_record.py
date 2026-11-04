"""Review persistence and atomic audit regressions."""

import json
from pathlib import Path

import pytest

from bridge.config import BridgeConfig
from bridge.models import Actor, State, TransitionError
from bridge.service import Bridge
from bridge.state import Store
from tests.conftest import run, spec


def ready(repo, fake_codex, monkeypatch, **options):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    bridge = Bridge(repo, BridgeConfig(retry_backoff_s=0,
                                      lock_dir=str(fake_codex / "locks")))
    tid = run(bridge.create_task(spec(**options)))["task_id"]
    run(bridge.execute_task(tid, wait=True))
    assert bridge.store.get(tid)["state"] == State.REVIEW
    return bridge, tid


def events(bridge, tid, kind):
    return [e for e in bridge.store.events(tid, 500) if e["kind"] == kind]


def fail_gate(bridge, tid):
    last = bridge.store.attempts(tid)[-1]
    bridge.store.finish_attempt(tid, last["n"], verification={"passed": False})


# @pt.id T0053-01
# @pt.item test_approve_with_secret_shaped_notes_reaches_done
# @pt.condition secret-shaped notes
# @pt.expected DONE with redacted dictionary detail
# @pt.target Bridge review lifecycle
# @pt.method integration
# @pt.legacy REL-03, REL-06
def test_approve_with_secret_shaped_notes_reaches_done(repo, fake_codex, monkeypatch):
    b, tid = ready(repo, fake_codex, monkeypatch)
    run(b.review_task(tid, "approve", "ok, verified. db password=hunter2"))
    assert b.store.get(tid)["state"] == State.DONE
    detail = events(b, tid, "review")[-1]["detail"]
    assert isinstance(detail, dict)
    assert "hunter2" not in detail["notes"]


# @pt.id T0053-02
# @pt.item test_approve_with_max_length_notes_reaches_done
# @pt.condition maximum-length notes
# @pt.expected DONE without JSON corruption
# @pt.target Bridge review lifecycle
# @pt.method integration
# @pt.legacy REL-03, REL-06
def test_approve_with_max_length_notes_reaches_done(repo, fake_codex, monkeypatch):
    b, tid = ready(repo, fake_codex, monkeypatch)
    run(b.review_task(tid, "approve", "x" * 7990))
    assert b.store.get(tid)["state"] == State.DONE


# @pt.id T0053-03
# @pt.item test_human_approve_after_long_notes
# @pt.condition high risk and long notes
# @pt.expected human approval reaches DONE with one audit event
# @pt.target Bridge review lifecycle
# @pt.method integration
# @pt.legacy REL-03, REL-06
def test_human_approve_after_long_notes(repo, fake_codex, monkeypatch):
    b, tid = ready(repo, fake_codex, monkeypatch, risk_level="high")
    run(b.review_task(tid, "approve", "x" * 7990))
    assert b.store.get(tid)["state"] == State.APPROVED
    b.human_approve(tid)
    assert b.store.get(tid)["state"] == State.DONE
    assert len(events(b, tid, "human_approval")) == 1


# @pt.id T0053-04
# @pt.item test_rejected_approve_leaves_no_event
# @pt.condition failed verification on review
# @pt.expected REVIEW retained with no review record or event
# @pt.target Bridge review lifecycle
# @pt.method integration
# @pt.legacy REL-03, REL-06
def test_rejected_approve_leaves_no_event(repo, fake_codex, monkeypatch):
    b, tid = ready(repo, fake_codex, monkeypatch)
    fail_gate(b, tid)
    with pytest.raises(TransitionError):
        run(b.review_task(tid, "approve", "ok"))
    assert b.store.get(tid)["state"] == State.REVIEW
    assert not events(b, tid, "review")
    assert b.store.attempts(tid)[-1]["review"] is None


# @pt.id T0053-05
# @pt.item test_rejected_human_approve_leaves_no_event
# @pt.condition failed verification on human approval
# @pt.expected APPROVED retained with no human audit event
# @pt.target Bridge review lifecycle
# @pt.method integration
# @pt.legacy REL-03, REL-06
def test_rejected_human_approve_leaves_no_event(repo, fake_codex, monkeypatch):
    b, tid = ready(repo, fake_codex, monkeypatch, risk_level="high")
    run(b.review_task(tid, "approve", "ok"))
    fail_gate(b, tid)
    with pytest.raises(TransitionError):
        b.human_approve(tid)
    assert b.store.get(tid)["state"] == State.APPROVED
    assert not events(b, tid, "human_approval")


# @pt.id T0053-06
# @pt.item test_resume_uses_stored_rework_notes
# @pt.condition rework with 8000-character notes
# @pt.expected resume prompt uses stored attempt notes
# @pt.target Bridge review lifecycle
# @pt.method integration
# @pt.legacy REL-03, REL-06
def test_resume_uses_stored_rework_notes(repo, fake_codex, monkeypatch):
    b, tid = ready(repo, fake_codex, monkeypatch)
    notes = "x" * 8000
    run(b.review_task(tid, "rework", notes))
    assert b.store.attempts(tid)[-1]["review"]["notes"] == notes
    # The attempt record remains authoritative even when event history disagrees.
    b.store.log(tid, Actor.CLIENT, "review", {"notes": "outdated"})
    b.store.log(tid, Actor.CLIENT, "review", "legacy malformed detail")
    captured = []

    async def start(task_id, kind, prompt, session, expect, wait, actor):
        captured.append(prompt)
        return {}

    monkeypatch.setattr(b, "_start", start)
    run(b.resume_task(tid))
    assert notes in captured[0]


# @pt.id T0053-07
# @pt.item test_event_detail_always_valid_json
# @pt.condition nested secret and oversized string leaves
# @pt.expected valid JSON with redacted capped leaves
# @pt.target Store._event
# @pt.method unit
# @pt.legacy REL-03, REL-06
def test_event_detail_always_valid_json():
    store = Store(Path(":memory:"))
    detail = {1: ["password=hunter2", {"nested": ("token=hidden", "x" * 20000)}],
              "scalar": [1, 1.5, True, None], "object": Path("example")}
    store.log(None, Actor.CLIENT, "test", detail)
    raw = store.db.execute("SELECT detail FROM events").fetchone()["detail"]
    decoded = json.loads(raw)
    assert "hunter2" not in raw and "hidden" not in raw
    assert len(decoded["1"][1]["nested"][1]) <= 8001
    assert decoded["scalar"] == [1, 1.5, True, None]
    assert decoded["object"] == "example"


# @pt.id T0053-08
# @pt.item test_legacy_review_events_fallback
# @pt.condition legacy null review and mixed event details
# @pt.expected only current dictionary approvals satisfy gates
# @pt.target Bridge review lifecycle
# @pt.method integration
# @pt.legacy REL-03, REL-06
def test_legacy_review_events_fallback(repo, fake_codex, monkeypatch):
    b, tid = ready(repo, fake_codex, monkeypatch)
    assert b.store.attempts(tid)[-1]["review"] is None
    b.store.log(tid, Actor.CLIENT, "review", {"decision": "approve"})
    with b.store.tx() as c:
        c.execute("UPDATE events SET ts=0 WHERE task_id=? AND kind='review'", (tid,))
    b.store.log(tid, Actor.CLIENT, "review", "malformed legacy detail")
    with pytest.raises(TransitionError):
        b._assert_gates(tid)
    b.store.log(tid, Actor.CLIENT, "review", {"decision": "approve"})
    b._assert_gates(tid)
    with b.store.tx() as c:
        c.execute("UPDATE attempts SET review=? WHERE task_id=?",
                  (json.dumps({"decision": "rework"}), tid))
    with pytest.raises(TransitionError):
        b._assert_gates(tid)
