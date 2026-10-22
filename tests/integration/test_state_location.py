"""State isolation and protected project configuration regressions."""

import subprocess

import pytest

from bridge.accounts import Account, AccountLock
from bridge.config import ensure_ai_dir
from bridge.models import State
from bridge.state import ConflictError
from tests.conftest import run, spec


# @pt.id KIT-STATE-001-location
# @pt.item Mutable state is outside the workspace
# @pt.condition A bridge executes a task in a git repository
# @pt.expected Database, schema and health paths use the private git state directory
# @pt.target Bridge.__init__, Bridge.health, CodexAdapter.run
# @pt.method integration
# @pt.legacy none (new kit)
def test_state_location(make_bridge, repo, monkeypatch, fake_codex):
    b = make_bridge()
    git_dir = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"], text=True).strip()
    assert str(b.store.path) == git_dir + "/agent-bridge/bridge.db"
    assert b.state_dir.stat().st_mode & 0o777 == 0o700
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    tid = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(tid, wait=True))
    from tests.integration.test_accounts_routing import calls

    argv = calls(fake_codex)[-1]["argv"]
    schema = argv[argv.index("--output-schema") + 1]
    assert schema.startswith(str(b.state_dir) + "/")
    assert not (repo / ".ai" / "bridge.db").exists()
    assert not (repo / ".ai" / "output_schema.json").exists()
    health = run(b.health())
    assert health["database"] == str(b.store.path)
    assert health["state_dir"] == str(b.state_dir)


# @pt.id KIT-STATE-002-migration
# @pt.item Existing tasks survive migration
# @pt.condition A legacy database exists before the first bridge startup
# @pt.expected Task remains readable and migration is audited only once
# @pt.target Bridge.__init__
# @pt.method integration
# @pt.legacy none (new kit)
def test_legacy_migration(make_bridge, repo):
    b = make_bridge()
    tid = run(b.create_task(spec()))["task_id"]
    b.store.db.close()
    legacy = repo / ".ai" / "bridge.db"
    b.store.path.replace(legacy)
    fresh = make_bridge()
    assert fresh.store.get(tid)["spec"]["objective"] == spec()["objective"]
    assert not legacy.exists()
    assert fresh.store.db.execute(
        "SELECT count(*) FROM events WHERE kind='state_migrated'").fetchone()[0] == 1
    again = make_bridge()
    assert again.store.db.execute(
        "SELECT count(*) FROM events WHERE kind='state_migrated'").fetchone()[0] == 1


# @pt.id KIT-STATE-003-ignore
# @pt.item Config remains visible to git
# @pt.condition Ignore file is missing or already customized
# @pt.expected Exact default content is created and existing content is preserved
# @pt.target ensure_ai_dir
# @pt.method integration
# @pt.legacy none (new kit)
def test_ai_ignore(repo):
    ensure_ai_dir(repo)
    path = repo / ".ai" / ".gitignore"
    assert path.read_text() == "*\n!.gitignore\n!config.json\n"
    path.write_text("custom\n")
    ensure_ai_dir(repo)
    assert path.read_text() == "custom\n"


# @pt.id KIT-STATE-004-config-tampering
# @pt.item Config modifications trigger the protected path gate
# @pt.condition Fake codex modifies project configuration during execution
# @pt.expected Task enters REWORK with a protected config violation
# @pt.target Bridge._run_inner
# @pt.method integration
# @pt.legacy none (new kit)
def test_config_tampering(make_bridge, repo, monkeypatch):
    b = make_bridge()
    (repo / ".ai" / "config.json").write_text("{}\n")
    monkeypatch.setenv("FAKE_CODEX_WRITE", '.ai/config.json={"retry_backoff_s": 0}\n')
    tid = run(b.create_task(spec(verification_commands=[])))["task_id"]
    run(b.execute_task(tid, wait=True))
    row = b.store.get(tid)
    assert row["state"] == State.REWORK
    assert "protected file changed: .ai/config.json" in row["failure"]


# @pt.id KIT-STATE-005-child-lock-isolation
# @pt.item Child bridges run while the default account is locked by verification
# @pt.condition Parent holds the system default lock during CLI recovery and MCP lifecycle
# @pt.expected Both child bridge workflows start codex and complete their assertions
# @pt.target AccountLock, test_recovery_after_runner_death, _server
# @pt.method integration
# @pt.legacy none (new kit)
@pytest.mark.parametrize("workflow", ["recovery", "mcp"])
def test_child_lock_isolation(workflow, make_bridge, fake_codex, repo, monkeypatch):
    from tests.e2e.test_lifecycle import test_full_lifecycle_over_mcp
    from tests.integration.test_bridge import test_recovery_after_runner_death

    lock = AccountLock(Account("default", None), None)
    try:
        lock.acquire()
    except ConflictError:
        # Verification may already run under an outer bridge holding this same lock.
        pass
    try:
        if workflow == "recovery":
            test_recovery_after_runner_death(make_bridge, fake_codex, repo, monkeypatch)
        else:
            test_full_lifecycle_over_mcp(repo, fake_codex, monkeypatch)
    finally:
        lock.release()
