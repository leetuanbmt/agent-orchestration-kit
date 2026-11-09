"""A run that rewrites .git/config (core.fsmonitor) must never make the bridge run git against it
(audit F-01): the tamper is found by hashing the git internals from disk, before any git call."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from bridge import review
from bridge.models import PROTECTED_VIOLATION, State
from tests.conftest import git, run, spec

FLOW = """
version: 1
accounts:
  default: {provider: openai}
agents:
  codex: {adapter: codex-cli, account: default}
roles:
  executor: codex
workflow:
  stages: [execute, test]
  verify_sandbox: none
"""


def payload(tmp_path: Path) -> tuple[Path, Path]:
    """A harmless fsmonitor: an executable that only creates a marker file."""
    marker = tmp_path / "fsmonitor-ran"
    script = tmp_path / "fsmonitor.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, marker


def tampered(repo: Path, script: Path) -> str:
    return (repo / ".git" / "config").read_text() + f"[core]\n\tfsmonitor = {script}\n"


# @pt.id KIT-SEC-GITCFG-001-agent-writes-config
# @pt.item An agent that adds core.fsmonitor to .git/config never gets the script run by the bridge
# @pt.condition the fake agent rewrites .git/config with an fsmonitor script that creates a marker file
# @pt.expected the task is BLOCKED with a protected violation for .git/config and the marker does not exist
# @pt.target review.diff, review.tampered_internals, review._git
# @pt.method integration
# @pt.legacy audit F-01: git status / git add -A ran the fsmonitor script before the tamper was detected
def test_agent_config_write_blocks_without_running_script(make_bridge, repo, tmp_path, monkeypatch):
    script, marker = payload(tmp_path)
    content = tampered(repo, script)
    assert ";" not in content
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;.git/config={content}")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.BLOCKED
    violations = b.store.attempts(t)[-1]["verification"]["scope_violations"]
    assert f"{PROTECTED_VIOLATION}.git/config" in violations
    assert not marker.exists()


# @pt.id KIT-SEC-GITCFG-002-verification-writes-config
# @pt.item A verification command that adds core.fsmonitor to .git/config is caught before git runs
# @pt.condition verify_sandbox none; a python3 check appends the fsmonitor setting to .git/config
# @pt.expected the task is BLOCKED with a protected violation for .git/config and the marker does not exist
# @pt.target review.recheck, review.diff
# @pt.method integration
# @pt.legacy audit F-01: recheck ran git status against the tampered config
def test_verification_config_write_blocks(make_orch, repo, tmp_path, monkeypatch):
    script, marker = payload(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    command = ("python3 -c \"import pathlib; p = pathlib.Path('.git/config'); "
               f"p.write_text(p.read_text() + '[core]\\n\\tfsmonitor = {script}\\n')\"")
    b = make_orch(FLOW)
    t = run(b.create_task(spec(verification_commands=[command])))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.BLOCKED
    violations = b.store.attempts(t)[-1]["verification"]["scope_violations"]
    assert f"{PROTECTED_VIOLATION}.git/config" in violations
    assert not marker.exists()


# @pt.id KIT-SEC-GITCFG-003-health-and-refusal
# @pt.item While the tampered .git/config is on disk health reports it and no task starts
# @pt.condition after a BLOCK by a .git/config rewrite: health, execute of a new task, restore of the config, execute again
# @pt.expected health lists .git/config with ok false; execute raises ValueError naming .git/config; after the restore execute runs; the marker never exists
# @pt.target Bridge.health, Bridge.execute_task, review.protected_git_left
# @pt.method integration
# @pt.legacy audit F-01: health ran git status against the tampered config
def test_health_and_execute_refuse_until_restored(make_bridge, repo, tmp_path, monkeypatch):
    script, marker = payload(tmp_path)
    original = (repo / ".git" / "config").read_text()
    monkeypatch.setenv("FAKE_CODEX_WRITE",
                       f"src/app.py=x = 2\n;.git/config={tampered(repo, script)}")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.BLOCKED
    health = run(b.health())
    assert ".git/config" in health["protected_dirty"] and health["ok"] is False
    assert not marker.exists()
    t2 = run(b.create_task(spec(objective="Set x to 2 again")))["task_id"]
    with pytest.raises(ValueError, match=r"\.git/config"):
        run(b.execute_task(t2, wait=True))
    assert not marker.exists()
    (repo / ".git" / "config").write_text(original)
    assert ".git/config" not in run(b.health())["protected_dirty"]
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    assert run(b.execute_task(t2, wait=True))["state"] != State.BLOCKED
    assert not marker.exists()


# @pt.id KIT-SEC-GITCFG-004-locator-main
# @pt.item The git-internals locator finds a main checkout's config and hooks without git
# @pt.condition a normal repository
# @pt.expected git dir and common dir are <root>/.git; the hashes cover config, hooks and info/attributes under .git/... keys
# @pt.target review._git_dirs, review._git_internals
# @pt.method unit
# @pt.legacy none
def test_locator_main_checkout(repo):
    assert review._git_dirs(repo) == (repo / ".git", repo / ".git")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* text=auto\n")
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n")
    found = review._git_internals(repo)
    assert {".git/config", ".git/info/attributes", ".git/hooks/pre-commit"} <= set(found)
    assert all(found[k] for k in (".git/config", ".git/info/attributes", ".git/hooks/pre-commit"))
    assert review.git_internal_path(repo, ".git/config") == repo / ".git" / "config"


# @pt.id KIT-SEC-GITCFG-005-locator-worktree
# @pt.item The locator follows a linked worktree to the shared config and hooks
# @pt.condition `git worktree add` creates a linked worktree; the shared config is then edited
# @pt.expected git dir is .git/worktrees/<name>, common dir is the main .git; the shared config change is reported by tampered_internals and diff makes no git call
# @pt.target review._git_dirs, review.tampered_internals, review.diff
# @pt.method unit
# @pt.legacy none
def test_locator_linked_worktree(repo, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    gitdir, common = review._git_dirs(wt)
    assert common == (repo / ".git").resolve()
    assert gitdir.parent == (repo / ".git" / "worktrees").resolve()
    assert review.git_internal_path(wt, ".git/config") == common / "config"
    assert review.protected_hashes(wt, [".git/config"])[".git/config"] is not None
    before = {"files": review._git_internals(wt), "head": None, "tree": None}
    assert ".git/config" in before["files"]
    assert review.tampered_internals(wt, before) == []
    with (repo / ".git" / "config").open("a") as f:
        f.write("[core]\n\tfsmonitor = false\n")
    assert review.tampered_internals(wt, before) == [".git/config"]

    def no_git(*args, **kwargs):
        raise AssertionError("git must not run after a tamper")

    monkeypatch.setattr(review, "_git", no_git)
    changes = review.diff(wt, before, [])
    assert changes["files"] == [".git/config"] and changes["patch"] == ""
    assert changes["ignored_files"] == [] and not changes["head_changed"]


# @pt.id KIT-SEC-GITCFG-006-git-flags
# @pt.item Every bridge git call switches off fsmonitor and hook paths and drops credential variables
# @pt.condition _git runs `git diff` with an API key in the environment
# @pt.expected argv stays `git -C root ...`, the environment carries core.fsmonitor=false and core.hooksPath=/dev/null via GIT_CONFIG_*, diff gets --no-ext-diff --no-textconv, the key is not passed
# @pt.target review._git
# @pt.method unit
# @pt.legacy none
def test_git_defence_in_depth(repo, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setenv("SOME_API_KEY", "abc")
    monkeypatch.setattr(review.subprocess, "run", fake_run)
    review._git(repo, "diff", "--stat", "a", "b", env={"GIT_INDEX_FILE": "/x"})
    argv = seen["argv"]
    assert argv == ["git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv",
                    "--stat", "a", "b"]
    env = seen["env"]
    assert (env["GIT_CONFIG_COUNT"], env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"],
            env["GIT_CONFIG_KEY_1"], env["GIT_CONFIG_VALUE_1"]) == (
        "2", "core.fsmonitor", "false", "core.hooksPath", "/dev/null")
    assert "SOME_API_KEY" not in env and env["GIT_INDEX_FILE"] == "/x"
    assert "PATH" in seen["env"]
