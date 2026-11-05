"""Approval and closing apply only to the content that was verified (audit SEC-05)."""

from __future__ import annotations

import json

import pytest

from bridge.models import State, TransitionError
from tests.conftest import run, spec


def verified_task(b, monkeypatch, **extra) -> str:
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    t = run(b.create_task(spec(**extra)))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REVIEW
    return t


# @pt.id KIT-SEC-BIND-001-client-approve
# @pt.item The client cannot approve content nobody verified
# @pt.condition a task in REVIEW; its verified src/app.py is edited, then deleted, before the client approves
# @pt.expected approve is refused naming the file and how to re-verify; the task stays in REVIEW; restoring the verified content lets the approval through
# @pt.target Bridge._assert_verified_content via review_task
# @pt.method integration
# @pt.legacy audit SEC-05: the edited file was approved and closed
def test_client_approve_bound_to_content(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    app = repo / "src" / "app.py"
    for tamper in (lambda: app.write_text("import os; os.system('id')\n"), app.unlink):
        tamper()
        with pytest.raises(TransitionError, match=r"worktree changed since verification: "
                                                  r"src/app\.py; send it back"):
            run(b.review_task(t, "approve", "looks fine"))
        assert b.store.get(t)["state"] == State.REVIEW
    app.write_text("x = 2\n")
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE


# @pt.id KIT-SEC-BIND-002-human-approve
# @pt.item A human approval cannot close content changed after verification
# @pt.condition a high-risk task waiting at APPROVED; src/app.py is edited before human_approve
# @pt.expected human_approve is refused (restore or cancel), the task stays APPROVED
# @pt.target Bridge._assert_verified_content via human_approve
# @pt.method integration
# @pt.legacy audit SEC-05: the human closed the tampered file as DONE
def test_human_approve_bound_to_content(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch, risk_level="high")
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.APPROVED
    (repo / "src" / "app.py").write_text("import os; os.system('id')\n")
    with pytest.raises(TransitionError, match="restore the verified content, or cancel"):
        b.human_approve(t)
    assert b.store.get(t)["state"] == State.APPROVED


# @pt.id KIT-SEC-BIND-003-unrelated-edit
# @pt.item Edits to other files do not block an approval
# @pt.condition a task in REVIEW; an unrelated file (README.md) changes before approval
# @pt.expected the approval goes through to DONE
# @pt.target Bridge._assert_verified_content
# @pt.method integration
# @pt.legacy none
def test_unrelated_edit_allowed(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    (repo / "README.md").write_text("other work\n")
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE


# @pt.id KIT-SEC-BIND-004-old-attempt
# @pt.item Attempts verified before content binding keep working
# @pt.condition the latest attempt has no after_hashes (verified by an older bridge)
# @pt.expected the approval goes through and an approval_unbound event records that it was not bound
# @pt.target Bridge._assert_verified_content
# @pt.method integration
# @pt.legacy none
def test_old_attempt_without_hashes(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    row = b.store.db.execute("SELECT changes FROM attempts WHERE task_id=?", (t,)).fetchone()
    old = {k: v for k, v in json.loads(row["changes"]).items() if k != "after_hashes"}
    with b.store.tx() as c:
        c.execute("UPDATE attempts SET changes=? WHERE task_id=?", (json.dumps(old), t))
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE
    assert b.store.count_events(t, "approval_unbound") >= 1
