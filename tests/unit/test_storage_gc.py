"""Registry pruning and terminal attempt log retention."""

import json
import os
import sqlite3
import time

import pytest

from bridge import accounts, cli, storage


# @pt.id T0036-prune
# @pt.item Registry pruning
# @pt.condition Existing directory, missing root and file registered
# @pt.expected Preview preserves registry and apply keeps only directories
# @pt.target bridge.storage.prune bridge.cli.main
# @pt.method unit
# @pt.legacy None
def test_prune(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    existing = tmp_path / "existing"
    existing.mkdir()
    missing = tmp_path / "missing"
    regular = tmp_path / "file"
    regular.write_text("keep")
    base = accounts.registry_dir()
    base.mkdir(parents=True)
    registry = base / "projects.json"
    registry.write_text(json.dumps([str(existing), str(missing), str(regular)]))
    before = registry.read_bytes()
    result = storage.prune()
    assert result == {"applied": False, "removed": [str(missing), str(regular)], "kept": 1}
    assert registry.read_bytes() == before
    assert not (storage.home() / "locks").exists()
    cli.main(["storage", "prune", "--apply"])
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert registry.read_text() == json.dumps([str(existing)], indent=2) + "\n"
    assert regular.read_text() == "keep"


# @pt.id T0036-gc
# @pt.item Terminal attempt log retention
# @pt.condition Old terminal, active, unknown and fresh terminal logs across central stores
# @pt.expected Only old terminal logs deleted and databases byte-identical
# @pt.target bridge.storage.gc bridge.cli.main
# @pt.method unit
# @pt.legacy None
def test_gc(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    selected = []
    preserved = []
    databases = {}
    for index in range(2):
        central = storage.home() / "projects" / str(index) / "worktrees" / "wt"
        logs = central / "logs"
        logs.mkdir(parents=True)
        (central / "worktree.json").write_text(json.dumps({"root": str(tmp_path / str(index))}))
        db = central / "bridge.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE tasks (id TEXT, state TEXT)")
            conn.executemany("INSERT INTO tasks VALUES (?, ?)", [
                ("task-done", "DONE"), ("cancelled", "CANCELLED"),
                ("blocked", "BLOCKED"), ("running", "RUNNING"), ("review", "REVIEW")])
        conn.close()
        databases[db] = db.read_bytes()
        for name in ("task-done-1", "cancelled-1", "blocked-1", "running-1", "review-1", "unknown-1", "task-done-2"):
            path = logs / (name + ".jsonl")
            path.write_text("{}\n")
            old = time.time() - 10 * 86400
            if name != "task-done-2":
                os.utime(path, (old, old))
            (selected if name in ("task-done-1", "cancelled-1", "blocked-1") else preserved).append(path)
    result = storage.gc(5)
    assert result["applied"] is False
    assert set(result["files"]) == {str(p) for p in selected}
    assert result["bytes"] == sum(p.stat().st_size for p in selected)
    assert all(p.exists() for p in selected + preserved)
    assert not (storage.home() / "locks").exists()
    cli.main(["storage", "gc", "--logs-older-than", "5", "--apply"])
    applied = json.loads(capsys.readouterr().out)
    assert applied == {**result, "applied": True}
    assert all(not p.exists() for p in selected)
    assert all(p.exists() for p in preserved)
    assert all(db.read_bytes() == before for db, before in databases.items())


# @pt.id T0036-days
# @pt.item Retention threshold validation
# @pt.condition Threshold below one day
# @pt.expected ValueError without cleanup
# @pt.target bridge.storage.gc
# @pt.method unit
# @pt.legacy None
@pytest.mark.parametrize("days", [-1, 0, 0.99, float("nan")])
def test_invalid_days(days):
    with pytest.raises(ValueError, match=">= 1"):
        storage.gc(days)


# @pt.id T0036-guards
# @pt.item Cleanup process guards
# @pt.condition Bridge process running
# @pt.expected Both apply operations refuse while previews remain available
# @pt.target bridge.storage.prune bridge.storage.gc
# @pt.method unit
# @pt.legacy None
def test_guards(monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [{"pid": 123}])
    assert storage.prune()["applied"] is False
    assert storage.gc(1)["applied"] is False
    for operation in (lambda: storage.prune(True), lambda: storage.gc(1, True)):
        with pytest.raises(RuntimeError, match="123"):
            operation()
