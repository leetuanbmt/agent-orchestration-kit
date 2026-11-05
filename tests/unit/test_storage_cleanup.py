"""Rollback and legacy retirement checks."""

import sqlite3
from pathlib import Path

import pytest

from bridge import cli, storage
from bridge.service import Bridge
from tests.unit.test_storage_migrate import assert_unchanged, hashes, seed


@pytest.fixture
def migrated(repo, monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    source = seed(repo)
    entry = {"root": str(repo), "source": str(source), "target": str(storage._paths(repo)[2])}
    storage._migrate_entry(entry)
    assert entry["status"] == "migrated_now"
    return source, Path(entry["target"])


# @pt.id T0035-rollback
# @pt.item Storage rollback
# @pt.condition Migrated store with optional newer events
# @pt.expected Central copy archived and legacy tasks readable with explicit loss consent
# @pt.target bridge.storage.rollback
# @pt.method unit
# @pt.legacy agent-bridge
@pytest.mark.parametrize("new_events", [False, True])
def test_rollback(repo, migrated, new_events):
    source, central = migrated
    if new_events:
        with sqlite3.connect(central / "bridge.db") as conn:
            conn.execute("INSERT INTO events(ts,actor,kind) VALUES (3,'system','new')")
        conn.close()
        with pytest.raises(RuntimeError, match="1 events.*--yes"):
            storage.rollback(repo)
        assert central.exists()
    result = storage.rollback(repo, yes=new_events)
    assert result["hidden_events"] == int(new_events)
    assert Path(result["moved_to"]).is_relative_to(storage.home() / "rollback")
    assert (Path(result["moved_to"]) / "bridge.db").exists()
    assert not central.exists()
    bridge = Bridge(repo)
    assert bridge.storage == "legacy"
    assert bridge.store.get("T0001")["id"] == "T0001"
    bridge.store.db.close()


# @pt.id T0035-cleanup
# @pt.item Legacy cleanup
# @pt.condition Verified migration with WAL SHM logs and installation files
# @pt.expected Preview preserves files and apply archives only state while central stays readable
# @pt.target bridge.storage.cleanup_legacy
# @pt.method unit
# @pt.legacy agent-bridge
def test_cleanup(repo, migrated):
    source, central = migrated
    for name in ("install-manifest.json", "output_schema.json", "other"):
        (source.parent / name).write_text("{}")
    conn = sqlite3.connect(source)
    conn.execute("SELECT * FROM events").fetchall()
    assert Path(str(source) + "-wal").exists()
    assert Path(str(source) + "-shm").exists()
    assert storage.cleanup_legacy()["entries"][0]["status"] == "ready"
    assert source.exists() and (source.parent / "logs").exists()
    assert not (storage.home() / "backups").exists()
    assert storage.cleanup_legacy(True)["entries"][0]["status"] == "moved"
    backup = next((storage.home() / "backups").glob("*/*/*"))
    for name in ("bridge.db", "bridge.db-wal", "bridge.db-shm", "logs"):
        assert (backup / name).exists()
        assert not (source.parent / name).exists()
    conn.close()
    assert backup.stat().st_mode & 0o777 == 0o700
    for name in ("install-manifest.json", "output_schema.json", "other"):
        assert (source.parent / name).read_text() == "{}"
    bridge = Bridge(repo)
    assert bridge.storage == "central"
    assert bridge.store.get("T0001")["id"] == "T0001"
    bridge.store.db.close()
    assert storage.cleanup_legacy()["entries"][0]["status"] == "gone"
    with pytest.raises(RuntimeError, match="legacy source"):
        storage.rollback(repo)


# @pt.id T0035-changed
# @pt.item Changed legacy source
# @pt.condition Legacy database written after migration
# @pt.expected No move and CLI exits one
# @pt.target bridge.storage.cleanup_legacy bridge.cli.main
# @pt.method unit
# @pt.legacy agent-bridge
def test_changed(repo, migrated, capsys):
    source, _ = migrated
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE tasks SET spec_hash='changed'")
    conn.close()
    assert storage.cleanup_legacy(True)["entries"][0]["status"] == "changed"
    assert source.exists()
    with pytest.raises(SystemExit) as exc:
        cli.main(["storage", "cleanup-legacy", "--apply"])
    assert exc.value.code == 1
    assert 'changed' in capsys.readouterr().out


# @pt.id T0035-guards
# @pt.item Retirement guards
# @pt.condition Unmigrated store or active bridge process
# @pt.expected Operations refuse
# @pt.target bridge.storage.rollback bridge.storage.cleanup_legacy
# @pt.method unit
# @pt.legacy None
def test_guards(repo, monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    storage.locate(repo, create=True)
    with pytest.raises(RuntimeError, match="only migrated stores"):
        storage.rollback(repo)
    monkeypatch.setattr(storage, "running_processes", lambda: [{"pid": 123}])
    for operation in (lambda: storage.rollback(repo),
                      lambda: storage.cleanup_legacy(True)):
        with pytest.raises(RuntimeError, match="123"):
            operation()


# @pt.id T0035-preview-running
# @pt.item Cleanup preview
# @pt.condition Migrated store and active bridge process
# @pt.expected Preview preserves state without a lock and apply refuses
# @pt.target bridge.storage.cleanup_legacy
# @pt.method unit
# @pt.legacy agent-bridge
def test_preview_while_running(repo, migrated, monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [{"pid": 123}])
    before = hashes(repo)
    central_before = hashes(storage.home())
    result = storage.cleanup_legacy(apply=False)
    assert result["applied"] is False
    assert result["entries"][0]["status"] == "ready"
    assert_unchanged(repo, before)
    assert_unchanged(storage.home(), central_before)
    assert not (storage.home() / "locks").exists()
    assert not (storage.home() / "backups").exists()
    with pytest.raises(RuntimeError, match="123"):
        storage.cleanup_legacy(apply=True)
    assert migrated[0].exists()
