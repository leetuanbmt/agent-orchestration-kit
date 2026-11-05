"""Legacy storage migration checks."""

import fcntl
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge import accounts, cli, install, progress, registry, storage
from bridge.service import Bridge
from bridge.state import Store, SCHEMA
from tests.conftest import git

PROCESS_SCAN = storage.running_processes


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [])


def seed(root, name="agent-bridge"):
    directory = root / ".ai" if name == ".ai" else install.git_dir(root) / name
    directory.mkdir()
    store = Store(directory / "bridge.db")
    with store.tx() as conn:
        conn.execute("INSERT INTO tasks(id,spec,spec_hash,state,created_at,updated_at) VALUES ('T0001','{}','x','RUNNING',1,1)")
    store.start_attempt("T0001", 1, "execute")
    store.log("T0001", "system", "seed")
    store.create_workflow("workflow", None, "RUNNING")
    store.db.close()
    logs = directory / "logs"
    logs.mkdir(mode=0o700)
    for n in (1, 2):
        p = logs / f"T0001-{n}.jsonl"
        p.write_text('{"message":"test"}\n')
        p.chmod(0o600)
    return directory / "bridge.db"


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file() and p.name != ".env"}


def assert_unchanged(directory, before):
    after = hashes(directory)
    for name, digest in before.items():
        if not name.endswith("-shm"):
            assert after.get(name) == digest, name
    for name in after.keys() - before.keys():
        if name.endswith("-shm"):
            continue
        assert name.endswith("-wal"), name
        assert (directory / name).stat().st_size == 0, name


# @pt.id T0031-copy
# @pt.item Migration
# @pt.condition Legacy state with tasks attempts events workflows and logs
# @pt.expected Dry run is read only and apply preserves state and permissions
# @pt.target bridge.storage.migrate
# @pt.method unit
# @pt.legacy agent-bridge codex-bridge .ai
@pytest.mark.parametrize("name", ["agent-bridge", "codex-bridge", ".ai"])
def test_copy(repo, monkeypatch, name):
    source = seed(repo, name)
    monkeypatch.setattr(accounts, "known_projects", lambda: [str(repo)])
    monkeypatch.setattr(progress, "discover_roots", lambda here=None: [])
    before = hashes(repo)
    result = storage.migrate(repo)
    assert result["sources"][0]["status"] == "ready"
    assert_unchanged(repo, before)
    assert not (storage.home() / "projects").exists()
    target = Path(result["sources"][0]["target"])
    leftover = target.with_name(target.name + ".partial-old")
    leftover.mkdir(parents=True)
    (leftover / "old").touch()
    result = storage.migrate(repo, apply=True)
    assert result["sources"][0]["status"] == "migrated_now"
    assert not leftover.exists()
    assert_unchanged(repo, before)
    with storage._readonly(source) as src, storage._readonly(target / "bridge.db") as dst:
        assert storage.fingerprint(src) == storage.fingerprint(dst)
    metadata = json.loads((target / "migration.json").read_text())
    assert len(metadata["logs"]) == 2
    assert json.loads((target / "worktree.json").read_text())["root"] == str(repo)
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "logs").stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (target / "logs").iterdir())
    central_before = hashes(storage.home())
    assert storage.migrate(repo, True)["sources"][0]["status"] == "migrated"
    assert_unchanged(storage.home(), central_before)
    bridge = Bridge(repo)
    assert bridge.storage == "central"
    assert bridge.store.get("T0001")["id"] == "T0001"
    assert bridge.store.attempts("T0001")[0]["n"] == 1


# @pt.id T0031-wal
# @pt.item WAL snapshot
# @pt.condition Committed rows remain in WAL and linked worktree has state
# @pt.expected Every row migrates to separate worktree directories
# @pt.target bridge.storage.migrate
# @pt.method unit
# @pt.legacy agent-bridge
def test_wal_worktree(repo, monkeypatch):
    tree = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", str(tree))
    source = seed(repo)
    seed(tree)
    monkeypatch.setattr(accounts, "known_projects", lambda: [str(repo)])
    monkeypatch.setattr(progress, "discover_roots", lambda here=None: [str(repo), str(tree)])
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("INSERT INTO events(ts,actor,kind) VALUES (2,'system','wal')")
        conn.commit()
        before = storage.fingerprint(conn)
        results = storage.migrate(repo, True)["sources"]
        assert len(results) == 2
        assert all(e["status"] == "migrated_now" for e in results)
        paths = [Path(e["target"]) for e in results]
        assert paths[0].parent == paths[1].parent
        with storage._readonly(paths[0] / "bridge.db") as copied:
            assert storage.fingerprint(copied) == before


# @pt.id T0031-statuses
# @pt.item Discovery and CLI
# @pt.condition Missing non git empty and corrupt roots
# @pt.expected Explicit statuses and corrupt CLI exits one after JSON
# @pt.target bridge.storage.plan bridge.cli.main
# @pt.method unit
# @pt.legacy agent-bridge
def test_statuses(repo, tmp_path, monkeypatch, capsys):
    missing, ordinary = tmp_path / "missing", tmp_path / "ordinary"
    ordinary.mkdir()
    monkeypatch.setattr(registry, "known_projects", lambda: [
        str(missing), str(ordinary), str(repo / "src"), str(repo)])
    monkeypatch.setattr(registry, "with_worktrees", lambda roots: [])
    entries = storage.plan()
    assert [e["status"] for e in entries] == ["missing", "not_git", "not_git", "no_state"]
    assert entries[2]["error"] == "not a worktree top level"
    directory = repo / ".git/agent-bridge"
    directory.mkdir()
    (directory / "bridge.db").write_bytes(b"garbage")
    monkeypatch.setattr(cli, "find_root", lambda: None)
    for args in (["storage", "status"], ["storage", "migrate", "--apply"]):
        with pytest.raises(SystemExit) as exc:
            cli.main(args)
        assert exc.value.code == 1
        assert json.loads(capsys.readouterr().out)["sources"][-1]["status"] == "corrupt"


# @pt.id T0031-guards
# @pt.item Migration guards
# @pt.condition Running process or held migration lock
# @pt.expected RuntimeError without projects directory
# @pt.target bridge.storage.migrate
# @pt.method unit
# @pt.legacy None
def test_guards(repo, monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [{"pid": 123, "command": "agent-bridge"}])
    with pytest.raises(RuntimeError, match="123"):
        storage.migrate(repo, True)
    assert not (storage.home() / "projects").exists()
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    with (storage.home() / "locks/migrate.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="another storage migration"):
            storage.migrate(repo, True)


# @pt.id T0031-changing
# @pt.item Concurrent source mutation
# @pt.condition Source event inserted while logs copy
# @pt.expected Failed entry and partial retained without final target
# @pt.target bridge.storage.migrate
# @pt.method unit
# @pt.legacy agent-bridge
def test_source_changed(repo, monkeypatch):
    source = seed(repo)
    original = storage._copy_logs
    snapshots = []
    def copy(src, partial):
        logs = original(src, partial)
        with sqlite3.connect(source) as conn:
            conn.execute("INSERT INTO events(ts,actor,kind) VALUES (3,'system','changed')")
        conn.close()
        snapshots.append(hashes(repo))
        return logs
    monkeypatch.setattr(storage, "_copy_logs", copy)
    result = storage.migrate(repo, True)["sources"][0]
    assert result["status"] == "failed"
    assert result["error"] == "source changed during copy"
    assert not Path(result["target"]).exists()
    assert_unchanged(repo, snapshots[0])


# @pt.id T0031-processes
# @pt.item Process ancestry
# @pt.condition ps contains self ancestors and other bridge processes
# @pt.expected Only unrelated bridge processes reported
# @pt.target bridge.storage.running_processes
# @pt.method unit
# @pt.legacy None
def test_processes(monkeypatch):
    monkeypatch.setattr(storage.os, "getpid", lambda: 30)
    monkeypatch.setattr(storage.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="30 20 agent-bridge\n20 10 python -m bridge\n10 1 agent-bridge\n40 1 agent-bridge web\n50 1 python -m bridge\n60 1 other\n"))
    assert [p["pid"] for p in PROCESS_SCAN()] == [40, 50]


# @pt.id T0031-old-schema
# @pt.item Old schema
# @pt.condition No new task columns or workflows table
# @pt.expected Copy preserves schema and Store subsequently upgrades it
# @pt.target bridge.storage.migrate
# @pt.method unit
# @pt.legacy agent-bridge
def test_old_schema(repo):
    directory = repo / ".git/agent-bridge"
    directory.mkdir()
    with sqlite3.connect(directory / "bridge.db") as conn:
        conn.executescript(SCHEMA)
        conn.execute("DROP TABLE workflows")
    entry = storage.migrate(repo, True)["sources"][0]
    assert entry["status"] == "migrated_now"
    store = Store(Path(entry["target"]) / "bridge.db")
    assert store.list() == []

