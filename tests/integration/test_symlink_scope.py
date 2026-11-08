"""Symlinks cannot silently extend an executor's project scope (SEC-05)."""

import pytest

from bridge.models import State
from tests.conftest import git, run, spec


# @pt.id KIT-SEC-SYMLINK-001-creation
# @pt.item Allowed patterns cannot cover existing escaping symlinks
# @pt.condition src/** covers a tracked or non-ignored untracked src/out link outside the project
# @pt.expected Task creation fails with a creation error naming the symlink src/out
# @pt.target bridge.review.escaping_symlinks, Bridge._prepare
# @pt.method integration
@pytest.mark.parametrize("tracked", [True, False])
def test_existing_escape_rejects_creation(make_bridge, repo, tmp_path, tracked):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "src/out").symlink_to(outside, target_is_directory=True)
    if tracked:
        git(repo, "add", "src/out")
        git(repo, "commit", "-qm", "Add outside link")
    b = make_bridge()
    with pytest.raises(ValueError, match="symlink.*src/out"):
        run(b.create_task(spec(allowed_paths=["src/**"])))


# @pt.id KIT-SEC-SYMLINK-002-attempt
# @pt.item An attempt cannot introduce or retarget an escaping symlink unnoticed
# @pt.condition The fake executor creates src/link pointing outside the project
# @pt.expected The task reports symlink escapes the project: src/link and never reaches REVIEW or DONE
# @pt.target bridge.review.scope_violations, bridge.runner.TaskRunner
# @pt.method integration
@pytest.mark.parametrize("existing", [True, False])
def test_executor_escape_is_violation(make_bridge, repo, tmp_path, monkeypatch, existing):
    if existing:
        (repo / "src/link").symlink_to("app.py")
        git(repo, "add", "src/link")
        git(repo, "commit", "-qm", "Add internal link")
        monkeypatch.setenv("FAKE_CODEX_WRITE", "!src/link")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("FAKE_CODEX_SYMLINK", f"src/link={outside}")
    b = make_bridge(max_attempts=1)
    task = run(b.create_task(spec(verification_commands=[])))["task_id"]
    out = run(b.execute_task(task, wait=True))
    assert out["state"] not in (State.REVIEW, State.DONE)
    assert "symlink escapes the project: src/link" in out["failure"]


# @pt.id KIT-SEC-SYMLINK-003-internal
# @pt.item Symlinks that resolve inside the project remain permitted
# @pt.condition A committed src/inner points to app.py and the executor creates another internal link
# @pt.expected Task creation succeeds and execution reaches REVIEW without a scope violation
# @pt.target bridge.review.escaping_symlinks, bridge.review.scope_violations
# @pt.method integration
def test_internal_symlink_is_allowed(make_bridge, repo, monkeypatch):
    (repo / "src/inner").symlink_to("app.py")
    git(repo, "add", "src/inner")
    git(repo, "commit", "-qm", "Add internal link")
    monkeypatch.setenv("FAKE_CODEX_SYMLINK", "src/new-inner=app.py")
    b = make_bridge()
    task = run(b.create_task(spec(verification_commands=[])))["task_id"]
    out = run(b.execute_task(task, wait=True))
    assert out["state"] == State.REVIEW, out["failure"]


# @pt.id KIT-SEC-SYMLINK-004-deep-glob
# @pt.item Deeper glob patterns cover escaping links
# @pt.condition A committed src/out escapes root under a deeper or empty-prefix glob
# @pt.expected Creation refuses the task and names src/out
# @pt.target bridge.review.escaping_symlinks
# @pt.method integration
# @pt.legacy false
@pytest.mark.parametrize("pattern", ["src/*/x.py", "**/*.py"])
def test_deep_glob_escape_rejects_creation(make_bridge, repo, tmp_path, pattern):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "src/out").symlink_to(outside, target_is_directory=True)
    git(repo, "add", "src/out")
    git(repo, "commit", "-qm", "Add outside link")
    with pytest.raises(ValueError, match="symlink.*src/out"):
        run(make_bridge().create_task(spec(allowed_paths=[pattern])))


# @pt.id KIT-SEC-SYMLINK-005-ignored-prefix
# @pt.item Ignored escaping links under an allowed literal prefix block creation
# @pt.condition Ignored src/build/out escapes root and src/** is allowed
# @pt.expected Creation refuses the task and names src/build/out
# @pt.target bridge.review.escaping_symlinks
# @pt.method integration
# @pt.legacy false
def test_ignored_escape_under_prefix_rejects_creation(make_bridge, repo, tmp_path):
    (repo / ".gitignore").write_text("src/build/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "Ignore build directory")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "src/build").mkdir()
    (repo / "src/build/out").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink.*src/build/out"):
        run(make_bridge().create_task(spec(allowed_paths=["src/**"])))


# @pt.id KIT-SEC-SYMLINK-006-ignored-empty-prefix
# @pt.item Empty-prefix globs do not cover unrelated ignored escaping links
# @pt.condition Ignored .venv/py escapes root and only **/*.py is allowed
# @pt.expected Task creation succeeds
# @pt.target bridge.review.escaping_symlinks
# @pt.method integration
# @pt.legacy false
def test_ignored_escape_without_prefix_allows_creation(make_bridge, repo, tmp_path):
    (repo / ".gitignore").write_text(".venv/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "Ignore virtual environment")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".venv").mkdir()
    (repo / ".venv/py").symlink_to(outside, target_is_directory=True)
    assert run(make_bridge().create_task(spec(allowed_paths=["**/*.py"])))["task_id"]


# @pt.id KIT-SEC-SYMLINK-007-ignored-attempt
# @pt.item Review workflows reject escaping links created in ignored directories
# @pt.condition The executor creates ignored src/build/out pointing outside root
# @pt.expected A symlink escape violation prevents REVIEW and DONE
# @pt.target bridge.review.scope_violations, bridge.runner.TaskRunner
# @pt.method integration
# @pt.legacy false
def test_executor_ignored_escape_is_violation(make_bridge, repo, tmp_path, monkeypatch):
    (repo / ".gitignore").write_text("src/build/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "Ignore build directory")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("FAKE_CODEX_SYMLINK", f"src/build/out={outside}")
    b = make_bridge(max_attempts=1)
    task = run(b.create_task(spec(verification_commands=[])))["task_id"]
    out = run(b.execute_task(task, wait=True))
    assert out["state"] not in (State.REVIEW, State.DONE)
    assert "symlink escapes the project: src/build/out" in out["failure"]
