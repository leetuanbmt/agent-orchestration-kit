"""Approval and closing apply only to the content that was verified (audit SEC-05)."""

from __future__ import annotations

import json

import pytest

from bridge.models import State, TransitionError
from bridge.review import EvidenceError, current_head
from tests.conftest import git, run, spec


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
# @pt.condition a task in REVIEW; an unrelated tracked file (README.md) changes before approval
# @pt.expected the approval goes through to DONE
# @pt.target Bridge._assert_verified_content
# @pt.method integration
# @pt.legacy none
def test_unrelated_edit_allowed(make_bridge, repo, monkeypatch):
    (repo / "README.md").write_text("initial documentation\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "add unrelated tracked file")
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


# @pt.id KIT-SEC-BIND-005-human-head
# @pt.item Human approval binds to the verified HEAD
# @pt.condition a high-risk task is APPROVED, then an empty commit moves HEAD
# @pt.expected approval names both SHAs and remediation; the task stays APPROVED
# @pt.target Bridge._assert_verified_content via human_approve
# @pt.method integration
# @pt.legacy audit SEC-07: a new commit could be approved without verification
def test_human_approve_bound_to_head(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch, risk_level="high")
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.APPROVED
    verified_head = b.store.attempts(t)[-1]["changes"]["verified_head"]
    git(repo, "commit", "--allow-empty", "-qm", "move HEAD")
    head = current_head(repo)
    assert head is not None
    with pytest.raises(TransitionError, match="HEAD changed since verification") as exc:
        b.human_approve(t)
    assert verified_head[:8] in str(exc.value)
    assert head[:8] in str(exc.value)
    assert "restore the verified HEAD, or cancel" in str(exc.value)
    assert b.store.get(t)["state"] == State.APPROVED


# @pt.id KIT-SEC-BIND-006-client-head
# @pt.item Client approval binds to the verified HEAD
# @pt.condition a task is in REVIEW, then an empty commit moves HEAD
# @pt.expected approval is refused with rework guidance; the task stays REVIEW
# @pt.target Bridge._assert_verified_content via review_task
# @pt.method integration
# @pt.legacy audit SEC-07: a new commit could be approved without verification
def test_client_approve_bound_to_head(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    git(repo, "commit", "--allow-empty", "-qm", "move HEAD")
    with pytest.raises(TransitionError, match="HEAD changed since verification.*send it back"):
        run(b.review_task(t, "approve", "ok"))
    assert b.store.get(t)["state"] == State.REVIEW


# @pt.id KIT-SEC-BIND-007-old-head
# @pt.item Attempts verified before HEAD binding keep working
# @pt.condition the attempt lacks verified_head and an empty commit moves HEAD
# @pt.expected approval goes through to DONE
# @pt.target Bridge._assert_verified_content
# @pt.method integration
# @pt.legacy none
def test_old_attempt_without_verified_head(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    changes = b.store.attempts(t)[-1]["changes"]
    del changes["verified_head"]
    with b.store.tx() as c:
        c.execute("UPDATE attempts SET changes=? WHERE task_id=?", (json.dumps(changes), t))
    git(repo, "commit", "--allow-empty", "-qm", "move HEAD")
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE


# @pt.id KIT-SEC-BIND-008-head-failure
# @pt.item HEAD lookup failures refuse approval
# @pt.condition a verified task is in REVIEW and git cannot return HEAD
# @pt.expected approval raises TransitionError and the task stays REVIEW
# @pt.target Bridge._assert_verified_content
# @pt.method integration
# @pt.legacy none
@pytest.mark.parametrize("raises", [False, True])
def test_head_lookup_failure(make_bridge, monkeypatch, raises):
    b = make_bridge()
    t = verified_task(b, monkeypatch)

    def failed_head(root):
        if raises:
            raise EvidenceError("git unavailable")
        return None

    monkeypatch.setattr("bridge.service.current_head", failed_head)
    with pytest.raises(TransitionError, match="cannot read HEAD since verification"):
        run(b.review_task(t, "approve", "ok"))
    assert b.store.get(t)["state"] == State.REVIEW


# @pt.id KIT-SEC-BIND-009-client-untracked
# @pt.item Client approval refuses new untracked files after verification
# @pt.condition src/conftest.py appears after the task reaches REVIEW
# @pt.expected approval names the file and rework guidance; the task stays REVIEW
# @pt.target Bridge._assert_verified_content via review_task
# @pt.method integration
# @pt.legacy audit SEC-07: unverified untracked files could be approved
def test_client_approve_refuses_new_untracked(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    (repo / "src" / "conftest.py").write_text("x = 3\n")
    with pytest.raises(TransitionError, match=r"new untracked files since verification: "
                                              r"src/conftest\.py; send it back"):
        run(b.review_task(t, "approve", "ok"))
    assert b.store.get(t)["state"] == State.REVIEW


# @pt.id KIT-SEC-BIND-010-human-untracked
# @pt.item Human approval refuses new untracked files after verification
# @pt.condition a high-risk task is APPROVED, then src/conftest.py appears
# @pt.expected approval names the file and restore-or-cancel guidance; the task stays APPROVED
# @pt.target Bridge._assert_verified_content via human_approve
# @pt.method integration
# @pt.legacy audit SEC-07: unverified untracked files could be approved
def test_human_approve_refuses_new_untracked(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch, risk_level="high")
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.APPROVED
    (repo / "src" / "conftest.py").write_text("x = 3\n")
    with pytest.raises(TransitionError, match=r"new untracked files since verification: "
                                              r"src/conftest\.py; restore the verified content, "
                                              r"or cancel"):
        b.human_approve(t)
    assert b.store.get(t)["state"] == State.APPROVED


# @pt.id KIT-SEC-BIND-011-existing-untracked
# @pt.item Pre-existing untracked files do not block approval
# @pt.condition src/conftest.py exists before the verified attempt starts
# @pt.expected approval goes through to DONE
# @pt.target Bridge._assert_verified_content
# @pt.method integration
# @pt.legacy none
def test_preexisting_untracked_allowed(make_bridge, repo, monkeypatch):
    b = make_bridge()
    (repo / "src" / "conftest.py").write_text("x = 3\n")
    t = verified_task(b, monkeypatch)
    assert "src/conftest.py" in b.store.attempts(t)[-1]["baseline"]["files"]
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE


# @pt.id KIT-SEC-BIND-012-other-attempt-untracked
# @pt.item Files recorded by later attempts of other tasks do not block approval
# @pt.condition another task's attempt starts after verification and records src/conftest.py
# @pt.expected approval of the original task goes through to DONE
# @pt.target Bridge._assert_verified_content and Store.other_attempt_files_since
# @pt.method integration
# @pt.legacy none
def test_later_other_attempt_untracked_allowed(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    other = run(b.create_task(spec(objective="Add src/conftest.py")))["task_id"]
    b.store.start_attempt(other, 1, "execute")
    assert b.store.attempts(other)[-1]["started_at"] > b.store.attempts(t)[-1]["ended_at"]
    (repo / "src" / "conftest.py").write_text("x = 3\n")
    b.store.finish_attempt(other, 1, changes={"files": ["src/conftest.py"]})
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE


# @pt.id KIT-SEC-BIND-013-protected-untracked
# @pt.item New human-owned protected files do not block client approval
# @pt.condition .ai/config.json is created after the verified attempt reaches REVIEW
# @pt.expected client approval goes through to DONE
# @pt.target Bridge._assert_verified_content via review_task
# @pt.method integration
# @pt.legacy none
def test_new_protected_untracked_allowed(make_bridge, repo, monkeypatch):
    b = make_bridge()
    t = verified_task(b, monkeypatch)
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "config.json").write_text("{}\n")
    assert run(b.review_task(t, "approve", "ok"))["state"] == State.DONE
