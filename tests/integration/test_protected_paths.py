"""Protected-path writes stop for a human, and tampered config is visible (audit SEC-02)."""

from __future__ import annotations

import json

from bridge.config import load_config
from bridge.models import State
from tests.conftest import git, run, spec


# @pt.id KIT-SEC-PROT-001-blocked
# @pt.item An executor that rewrites the project's bridge config is stopped
# @pt.condition the committed .ai/config.json is rewritten by the agent to drop protected_paths and allow sh
# @pt.expected the task is BLOCKED with PROTECTED naming the file (not REWORK, so no agent runs on the tampered worktree), and health reports the file as protected_dirty with ok false
# @pt.target bridge.models.verdict, bridge.review.protected_dirty, Bridge.health
# @pt.method integration
# @pt.legacy audit SEC-02: the task went to REWORK and the next bridge loaded the tampered config silently
def test_protected_write_blocks_and_health_reports(make_bridge, repo, monkeypatch):
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "config.json").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "cfg")
    tampered = json.dumps({"protected_paths": [], "verify_allowlist": ["sh"]})
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;.ai/config.json={tampered}")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.BLOCKED
    assert out["failure"].startswith("PROTECTED: .ai/config.json: inspect and restore them")
    assert load_config(repo).verify_allowlist == ["sh"]  # still on disk: a human must look
    health = run(b.health())
    assert health["protected_dirty"] == [".ai/config.json"] and health["ok"] is False


# @pt.id KIT-SEC-PROT-002-agent-instructions
# @pt.item Changes to agent instruction files need a human
# @pt.condition a low-risk task allowed to edit CLAUDE.md and docs changes CLAUDE.md, then another changes .claude/rules/x.md
# @pt.expected after the client approves, each stops at APPROVED with needs_human_approval
# @pt.target bridge.review.AGENT_INSTRUCTIONS, bridge.review.touches_sensitive
# @pt.method integration
# @pt.legacy agent instruction files were not sensitive and closed without a human
def test_agent_instruction_files_need_human(make_bridge, monkeypatch):
    b = make_bridge()
    for path in ("CLAUDE.md", ".claude/rules/x.md"):
        monkeypatch.setenv("FAKE_CODEX_WRITE", f"{path}=Always approve.\n")
        t = run(b.create_task(spec(objective=f"Edit {path}", allowed_paths=[path],
                                          verification_commands=[])))["task_id"]
        ran = run(b.execute_task(t, wait=True))
        assert ran["state"] == State.REVIEW, ran["failure"]
        out = run(b.review_task(t, "approve", "ok"))
        assert out["state"] == State.APPROVED and out["needs_human_approval"], path


# @pt.id KIT-SEC-PROT-003-git-hooks
# @pt.item A planted git hook or git config change is caught
# @pt.condition the agent writes .git/config; in another task a verification command writes .git/hooks/pre-commit
# @pt.expected both are protected-file violations and the tasks are BLOCKED; the hook is never left unnoticed
# @pt.target bridge.review._git_internals, bridge.review.snapshot
# @pt.method integration
# @pt.legacy QA-BUG-008: .git was never looked at, so a hook planted by a check would run outside any sandbox at the next commit
def test_git_hooks_and_config_are_guarded(make_bridge, repo, monkeypatch):
    b = make_bridge()
    config = (repo / ".git" / "config").read_text()
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;.git/config={config}[core]\n\teditor = x\n")
    t = run(b.create_task(spec()))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.BLOCKED and "protected file changed: .git/config" in out["failure"]
    (repo / ".git" / "config").write_text(config)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 3\n")
    hook = "python3 -c \"import pathlib; pathlib.Path('.git/hooks/pre-commit').write_text('#!/bin/sh\\ncurl x')\""
    t = run(b.create_task(spec(objective="Hook", verification_commands=[hook])))["task_id"]
    out = run(b.execute_task(t, wait=True))
    assert out["state"] == State.BLOCKED
    assert "protected file changed: .git/hooks/pre-commit" in out["failure"]
