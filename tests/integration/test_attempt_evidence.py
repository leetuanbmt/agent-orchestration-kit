"""Approval evidence covers every attempt of a task, not only the last one (audit F-02)."""

from __future__ import annotations

import pytest

from bridge.models import State, TransitionError
from tests.conftest import run, spec


def _two_attempts(b, repo, monkeypatch, tmp_path) -> str:
    """Attempt 1 writes migrations/001.sql then crashes; attempt 2 writes only src/app.py."""
    plan = tmp_path / "write_plan.txt"
    plan.write_text("migrations/001.sql=select 1\n")
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"@{plan}")
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "writecrash,ok")
    t = run(b.create_task(spec(allowed_paths=["src/**", "migrations/**"])))["task_id"]
    first = run(b.execute_task(t, wait=True))
    assert first["state"] in (State.FAILED, State.PAUSED), first
    assert (repo / "migrations" / "001.sql").exists()
    plan.write_text("src/app.py=x = 2\n")
    return t


# @pt.id KIT-SEC-ATTEMPT-001-earlier-sensitive
# @pt.item A sensitive file written by an earlier, early-ending attempt still needs a human
# @pt.condition attempt 1 writes migrations/001.sql and crashes; attempt 2 (resume) writes only src/app.py
# @pt.expected the task reaches REVIEW with needs_human_approval true; the last attempt's after_hashes holds migrations/001.sql
# @pt.target bridge.runner.Runner._run_inner, _all_touched
# @pt.method integration
# @pt.legacy audit F-02: only the last attempt's files were judged
def test_earlier_attempt_sensitive_file_forces_human(make_bridge, repo, monkeypatch, tmp_path):
    b = make_bridge()
    t = _two_attempts(b, repo, monkeypatch, tmp_path)
    assert b.store.get(t)["needs_human"] == 1  # latched when the attempt ended early
    out = run(b.resume_task(t, wait=True))
    assert out["state"] == State.REVIEW
    assert out["needs_human_approval"] is True
    last = b.store.attempts(t)[-1]
    assert "migrations/001.sql" in last["changes"]["after_hashes"]
    assert "src/app.py" in last["changes"]["after_hashes"]
    assert "migrations/001.sql" not in last["changes"]["files"]


# @pt.id KIT-SEC-ATTEMPT-002-bound-approval
# @pt.item Approval stays bound to a file an earlier attempt wrote
# @pt.condition the task of KIT-SEC-ATTEMPT-001 is approved by the client, then migrations/001.sql is edited
# @pt.expected the client approval stops at APPROVED; human_approve is refused with the content-binding error
# @pt.target bridge.service.Bridge._assert_verified_content via human_approve
# @pt.method integration
# @pt.legacy audit F-02: the earlier attempt's file was not bound
def test_earlier_attempt_file_is_bound(make_bridge, repo, monkeypatch, tmp_path):
    b = make_bridge()
    t = _two_attempts(b, repo, monkeypatch, tmp_path)
    assert run(b.resume_task(t, wait=True))["state"] == State.REVIEW
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.APPROVED
    (repo / "migrations" / "001.sql").write_text("drop table users")
    with pytest.raises(TransitionError, match="restore the verified content, or cancel"):
        b.human_approve(t)
    assert b.store.get(t)["state"] == State.APPROVED


# @pt.id KIT-SEC-ATTEMPT-003-single-attempt
# @pt.item A single-attempt task touching nothing sensitive still auto-closes
# @pt.condition one attempt writes only src/app.py and passes
# @pt.expected needs_human_approval is false and the client approval closes the task as DONE
# @pt.target bridge.runner.Runner._run_inner
# @pt.method integration
# @pt.legacy none (regression)
def test_single_attempt_still_auto_closes(make_bridge, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.REVIEW
    assert out["needs_human_approval"] is False
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE
