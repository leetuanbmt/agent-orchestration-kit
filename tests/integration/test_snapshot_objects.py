"""The review snapshot must not hash sandbox caches or huge untracked files into the object store."""

from __future__ import annotations

import os
import subprocess

from bridge import review
from bridge.models import State
from bridge.review import diff, snapshot
from tests.conftest import git, run, spec


def _blob(repo, rel: str) -> str:
    """The blob id `rel` would get, computed without writing it."""
    return subprocess.run(["git", "-C", str(repo), "hash-object", rel], check=True,
                          capture_output=True, text=True).stdout.strip()


def _stored(repo, sha: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", sha],
                          capture_output=True).returncode == 0


# @pt.id KIT-REV-SNAP-001
# @pt.item The review snapshot skips cache directories and untracked files over the size cap
# @pt.condition after the baseline, untracked .codex-tmp/gocache/ab/x-d, untracked big.bin over the cap and untracked src/new.py appear, and tracked .cache/keep.txt changes
# @pt.expected no blob of the cache file or big.bin exists in the object store; both are in changes.files and patch_omitted_files with patch_truncated set; src/new.py and .cache/keep.txt are in the patch
# @pt.target bridge.review._worktree_tree, bridge.review.diff
# @pt.method integration
# @pt.legacy 2026-11-17: harness-vibecode/.git held ~17 GB of unreachable Codex GOCACHE blobs
def test_snapshot_skips_caches_and_big_untracked(repo, monkeypatch):
    monkeypatch.setattr(review, "UNTRACKED_TREE_CAP", 1024, raising=False)
    (repo / ".cache").mkdir()
    (repo / ".cache" / "keep.txt").write_text("old\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "tracked file in a cache dir")
    before = snapshot(repo, [])
    cache = repo / ".codex-tmp" / "gocache" / "ab"
    cache.mkdir(parents=True)
    (cache / "x-d").write_bytes(os.urandom(64))
    (repo / "big.bin").write_bytes(os.urandom(4096))
    (repo / "src" / "new.py").write_text("y = 1\n")
    (repo / ".cache" / "keep.txt").write_text("new\n")
    skipped = [_blob(repo, ".codex-tmp/gocache/ab/x-d"), _blob(repo, "big.bin")]

    changes = diff(repo, before, [])

    assert [s for s in skipped if _stored(repo, s)] == []
    assert "src/new.py" in changes["patch"] and ".cache/keep.txt" in changes["patch"]
    assert {".codex-tmp/gocache/ab/x-d", "big.bin", "src/new.py"} <= set(changes["files"])
    assert changes["patch_omitted_files"] == [".codex-tmp/gocache/ab/x-d", "big.bin"]
    assert changes["patch_truncated"]


# @pt.id KIT-REV-SNAP-002
# @pt.item A write into a skipped cache directory is still a scope violation
# @pt.condition the agent changes src/app.py and writes .codex-tmp/gocache/ab/x-d (outside allowed_paths)
# @pt.expected the cache file is listed in changes.files and as a scope violation; the task goes to REWORK
# @pt.target bridge.review.diff, bridge.review.scope_violations
# @pt.method integration
# @pt.legacy none
def test_cache_write_still_flagged(make_bridge, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;.codex-tmp/gocache/ab/x-d=blob")
    b = make_bridge()
    t = run(b.create_task(spec()))["task_id"]
    assert run(b.execute_task(t, wait=True))["state"] == State.REWORK
    a = b.store.attempts(t)[-1]
    assert ".codex-tmp/gocache/ab/x-d" in a["changes"]["files"]
    assert any(".codex-tmp/gocache/ab/x-d" in v for v in a["verification"]["scope_violations"])
