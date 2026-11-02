"""Central storage resolution and legacy compatibility."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from bridge import install, storage
from tests.conftest import git


# @pt.id T0029-central
# @pt.item New repository storage
# @pt.condition No existing bridge database
# @pt.expected Private central directories and stable metadata
# @pt.target bridge.storage.locate
# @pt.method unit
# @pt.legacy None
def test_new_repository(repo):
    location = storage.locate(repo, create=True)
    project = storage.home() / "projects" / storage.project_key(repo / ".git")
    assert location == storage.Location(project / "worktrees" / storage.worktree_key(repo), "central")
    for directory in (storage.home(), project.parent, project, location.dir.parent, location.dir):
        assert directory.stat().st_mode & 0o777 == 0o700
    metadata = json.loads((project / "project.json").read_text())
    assert metadata["name"] == repo.name
    assert metadata["common_git_dir"] == str(repo / ".git")
    assert metadata["created_at"]
    tree = json.loads((location.dir / "worktree.json").read_text())
    assert tree["root"] == str(repo)
    assert tree["git_dir"] == str(repo / ".git")
    assert tree["created_at"]
    before = (location.dir / "worktree.json").stat().st_mtime_ns
    storage.locate(repo, create=True)
    assert (location.dir / "worktree.json").stat().st_mtime_ns == before


# @pt.id T0029-legacy
# @pt.item Legacy discovery
# @pt.condition Existing current or old git-dir database
# @pt.expected Legacy selected without central creation or read-only rename
# @pt.target bridge.storage.locate
# @pt.method unit
# @pt.legacy agent-bridge, codex-bridge
@pytest.mark.parametrize("name", ["agent-bridge", "codex-bridge"])
def test_legacy(repo, name):
    directory = install.git_dir(repo) / name
    directory.mkdir()
    (directory / "bridge.db").touch()
    assert storage.locate(repo) == storage.Location(directory, "legacy")
    assert directory.exists()
    assert not storage.home().exists()
    created = storage.locate(repo, create=True)
    assert created == storage.Location(install.git_dir(repo) / "agent-bridge", "legacy")
    assert not storage.home().exists()


# @pt.id T0029-precedence
# @pt.item Central precedence
# @pt.condition Both central and legacy databases exist
# @pt.expected Central wins
# @pt.target bridge.storage.locate
# @pt.method unit
# @pt.legacy agent-bridge
def test_central_wins(repo):
    central = storage.store_dir(repo, create=True)
    (central / "bridge.db").touch()
    legacy = install.git_dir(repo) / "agent-bridge"
    legacy.mkdir()
    (legacy / "bridge.db").touch()
    assert storage.locate(repo) == storage.Location(central, "central")


# @pt.id T0029-home
# @pt.item Home override
# @pt.condition Absolute override or empty environment value
# @pt.expected Override honored and empty value uses default
# @pt.target bridge.storage.home
# @pt.method unit
# @pt.legacy None
def test_home_override(repo, tmp_path, monkeypatch):
    override = tmp_path / "state"
    monkeypatch.setenv(storage.HOME_ENV, str(override))
    assert storage.home(repo) == override
    assert storage.store_dir(repo, create=True).is_relative_to(override)
    monkeypatch.setenv(storage.HOME_ENV, "")
    assert storage.home() == Path.home() / ".agent-bridge"


# @pt.id T0029-invalid-home
# @pt.item Home validation
# @pt.condition Relative, symlink, file, foreign owner or workspace home
# @pt.expected ValueError without creating directories
# @pt.target bridge.storage.home
# @pt.method unit
# @pt.legacy None
@pytest.mark.parametrize("kind", ["relative", "symlink", "file", "inside", "owner"])
def test_invalid_home(repo, tmp_path, monkeypatch, kind):
    path = tmp_path / "state"
    if kind == "relative":
        path = Path("relative")
    elif kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif kind == "file":
        path.touch()
    elif kind == "inside":
        path = repo / "state"
    else:
        path.mkdir()
        monkeypatch.setattr(storage.os, "getuid", lambda: path.stat().st_uid + 1)
    monkeypatch.setenv(storage.HOME_ENV, str(path))
    with pytest.raises(ValueError):
        storage.home(repo)


# @pt.id T0029-readonly
# @pt.item Read-only new repository lookup
# @pt.condition No storage exists
# @pt.expected Returns central path without writing anything
# @pt.target bridge.storage.locate
# @pt.method unit
# @pt.legacy None
def test_readonly(repo):
    assert storage.locate(repo).kind == "central"
    assert not storage.home().exists()


# @pt.id T0029-worktrees
# @pt.item Linked worktree identity
# @pt.condition Main and linked worktrees share a common git directory
# @pt.expected Same project key and different worktree keys
# @pt.target bridge.storage.locate
# @pt.method unit
# @pt.legacy None
def test_linked_worktree(repo):
    tree = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", str(tree))
    main = storage.locate(repo).dir
    linked = storage.locate(tree).dir
    assert main.parent.parent == linked.parent.parent
    assert main.name != linked.name
    assert linked.name == storage.worktree_key(tree)


# @pt.id T0029-keys
# @pt.item Safe deterministic keys
# @pt.condition Names contain spaces, unicode, dots or long slugs
# @pt.expected Bounded safe slug and resolved path hash
# @pt.target bridge.storage.project_key, bridge.storage.worktree_key
# @pt.method unit
# @pt.legacy None
@pytest.mark.parametrize("name", ["My Repo", "日本語", "..", ".", "a" * 80, "Bare Repo.git"])
def test_keys(tmp_path, name):
    path = tmp_path / name
    for key, length in ((storage.project_key(path), 12), (storage.worktree_key(path), 8)):
        slug, digest = key.rsplit("-", 1)
        assert re.fullmatch(r"[a-z0-9._-]+", key)
        assert 1 <= len(slug) <= 40
        assert digest == hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:length]


# @pt.id T0032-discovery
# @pt.item Read-only central discovery
# @pt.condition Complete stores beside partial and missing metadata directories
# @pt.expected Only complete stores resolve and absent home stays absent
# @pt.target bridge.storage.central_stores, bridge.storage.find_store
# @pt.method unit
# @pt.legacy None
def test_central_discovery(repo, tmp_path):
    assert storage.central_stores() == {}
    assert not storage.home().exists()
    directory = storage.store_dir(repo, create=True)
    (directory / "bridge.db").touch()
    for name in ("other.partial-1", "no-metadata"):
        sibling = directory.parent / name
        sibling.mkdir()
        (sibling / "bridge.db").touch()
        if ".partial-" in name:
            (sibling / "worktree.json").write_text(json.dumps({"root": str(tmp_path / name)}))
    assert storage.central_stores() == {str(repo): directory}
    assert storage.find_store(repo) == directory / "bridge.db"
    missing = tmp_path / "deleted"
    (directory / "worktree.json").write_text(json.dumps({"root": str(missing)}))
    assert storage.find_store(missing) == directory / "bridge.db"
    with pytest.raises(FileNotFoundError):
        storage.find_store(tmp_path / "unknown")
