"""A protected file a stopped run changed stays in health until a human restores or edits it
(audit SEC-04), including files git cannot see such as .ai/orchestrator.yaml."""

from __future__ import annotations

import json

from bridge.models import PROTECTED_VIOLATION, State
from tests.conftest import git, run, spec

YAML = ".ai/orchestrator.yaml"


def tamper(make_bridge, repo, monkeypatch, content: str = "workflow: {stages: [execute, test]}"):
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".gitignore").write_text(".env\n.ai/orchestrator.yaml\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore orchestrator")
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;{YAML}={content}")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.BLOCKED
    return b, t


# @pt.id KIT-SEC-TAMPER-001-ignored-config
# @pt.item A git-ignored protected config a stopped run rewrote is reported by health
# @pt.condition the agent writes the git-ignored .ai/orchestrator.yaml; the task is BLOCKED
# @pt.expected health lists .ai/orchestrator.yaml in protected_dirty and ok is false; the attempt records protected_after
# @pt.target Bridge.health, review.protected_after, Store.protected_left
# @pt.method integration
# @pt.legacy audit SEC-04: protected_dirty stayed empty and the next bridge loaded the tampered stages
def test_ignored_config_tamper_reported(make_bridge, repo, monkeypatch):
    b, t = tamper(make_bridge, repo, monkeypatch)
    assert YAML in b.store.attempts(t)[-1]["verification"]["protected_after"]
    health = run(b.health())
    assert YAML in health["protected_dirty"] and health["ok"] is False
    assert any(p["code"] == "protected_dirty" for p in health["problems"])


# @pt.id KIT-SEC-TAMPER-002-cleared
# @pt.item Restoring or editing the file clears the report without a command
# @pt.condition after the tamper, the file is deleted (restored to absent), then rewritten by a human with other content
# @pt.expected neither state is reported
# @pt.target Bridge.health
# @pt.method integration
# @pt.legacy none
def test_restore_or_human_edit_clears(make_bridge, repo, monkeypatch):
    b, _ = tamper(make_bridge, repo, monkeypatch)
    (repo / YAML).unlink()
    assert YAML not in run(b.health())["protected_dirty"]
    (repo / YAML).write_text("workflow: {stages: [execute, test, review]}\n")
    assert YAML not in run(b.health())["protected_dirty"]


# @pt.id KIT-SEC-TAMPER-003-old-attempt
# @pt.item Attempts recorded before protected_after existed are ignored
# @pt.condition a stored attempt has a protected violation but no protected_after
# @pt.expected health does not report the path
# @pt.target Store.protected_left
# @pt.method integration
# @pt.legacy none
def test_attempt_without_hashes_ignored(make_bridge, repo):
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    b.store.start_attempt(t, 1, "execute")
    b.store.finish_attempt(t, 1, verification={"passed": False,
                                               "scope_violations": [f"{PROTECTED_VIOLATION}{YAML}"]})
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / YAML).write_text("anything\n")
    assert b.store.protected_left() == []
    assert YAML not in run(b.health())["protected_dirty"]


# @pt.id KIT-SEC-TAMPER-004-tracked-once
# @pt.item A tracked protected file reported by git and by the attempt appears once
# @pt.condition the agent rewrites the committed .ai/config.json; the task is BLOCKED
# @pt.expected protected_dirty is exactly [".ai/config.json"]
# @pt.target Bridge.health
# @pt.method integration
# @pt.legacy none
def test_tracked_tamper_listed_once(make_bridge, repo, monkeypatch):
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "config.json").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "cfg")
    monkeypatch.setenv("FAKE_CODEX_WRITE",
                       f"src/app.py=x = 2\n;.ai/config.json={json.dumps({'sandbox': 'read-only'})}")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.BLOCKED
    assert run(b.health())["protected_dirty"] == [".ai/config.json"]
