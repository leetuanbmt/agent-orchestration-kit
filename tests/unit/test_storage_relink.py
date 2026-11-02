"""Relink central history after moving a repository."""

import json
import os

import pytest

from bridge import cli, storage
from bridge.service import Bridge
from bridge.state import Store


@pytest.fixture
def moved(repo, monkeypatch):
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    source = storage.locate(repo, create=True).dir
    store = Store(source / "bridge.db")
    with store.tx() as conn:
        conn.execute("INSERT INTO tasks(id,spec,spec_hash,state,created_at,updated_at) "
                     "VALUES ('T0001','{}','x','DONE',1,1)")
    store.db.close()
    destination = repo.with_name("renamed")
    os.rename(repo, destination)
    return repo, destination, source


# @pt.id T0038-relink
# @pt.item Moved repository history
# @pt.condition Central store exists and repository is renamed
# @pt.expected Preview changes nothing and CLI apply preserves task and updates metadata
# @pt.target bridge.storage.relink bridge.cli.main
# @pt.method unit
# @pt.legacy None
def test_relink(moved, capsys):
    old, new, source = moved
    before = {p: p.read_bytes() for p in storage.home().rglob("*") if p.is_file()}
    created = json.loads((source / "worktree.json").read_text())["created_at"]
    target = storage._paths(new)[2]
    cli.main(["storage", "relink", str(old), str(new)])
    assert json.loads(capsys.readouterr().out) == {
        "applied": False, "from": str(source), "to": str(target)}
    assert before == {p: p.read_bytes() for p in storage.home().rglob("*") if p.is_file()}
    assert not target.exists()
    cli.main(["storage", "relink", str(old), str(new), "--apply"])
    assert json.loads(capsys.readouterr().out) == {
        "applied": True, "from": str(source), "to": str(target)}
    assert not source.parent.parent.exists()
    assert json.loads((target / "worktree.json").read_text()) == {
        "root": str(new), "git_dir": str(new / ".git"),
        "created_at": created, "relinked_from": str(old)}
    project = json.loads((target.parent.parent / "project.json").read_text())
    assert project["name"] == new.name
    assert project["common_git_dir"] == str(new / ".git")
    for directory in (target.parent.parent, target.parent, target):
        assert directory.stat().st_mode & 0o777 == 0o700
    bridge = Bridge(new)
    try:
        assert bridge.storage == "central"
        assert bridge.store.get("T0001")["id"] == "T0001"
    finally:
        bridge.store.db.close()


# @pt.id T0038-refusals
# @pt.item Relink validation
# @pt.condition Unknown source, nested destination, occupied target, live source or running process
# @pt.expected RuntimeError preserves source database
# @pt.target bridge.storage.relink
# @pt.method unit
# @pt.legacy None
@pytest.mark.parametrize("reason", ["unknown", "nested", "target", "live", "running"])
def test_refusals(moved, monkeypatch, reason):
    old, new, source = moved
    before = (source / "bridge.db").read_bytes()
    if reason == "unknown":
        old = old.with_name("unknown")
    elif reason == "nested":
        new = new / "nested"
        new.mkdir()
    elif reason == "target":
        target = storage.locate(new, create=True).dir
        (target / "bridge.db").write_bytes(b"existing")
    elif reason == "live":
        old.mkdir()
    else:
        monkeypatch.setattr(storage, "running_processes", lambda: [{"pid": 123}])
    with pytest.raises(RuntimeError):
        storage.relink(str(old), new, apply=True)
    assert (source / "bridge.db").read_bytes() == before


# @pt.id T0038-preserve-project
# @pt.item Old project cleanup
# @pt.condition Old project holds another worktree or an unrelated file
# @pt.expected Relink leaves old project and its remaining contents intact
# @pt.target bridge.storage.relink
# @pt.method unit
# @pt.legacy None
@pytest.mark.parametrize("extra", ["worktree", "file"])
def test_preserve_project(moved, extra):
    old, new, source = moved
    kept = source.parent / "other" if extra == "worktree" else source.parent.parent / "keep"
    if extra == "worktree":
        kept.mkdir()
    else:
        kept.write_text("keep")
    storage.relink(str(old), new, apply=True)
    assert kept.exists()
    assert (source.parent.parent / "project.json").exists()
