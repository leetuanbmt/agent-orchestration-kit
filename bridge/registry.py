"""Machine-wide project registry: the project roots agent-bridge has run in, and their worktrees."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import osutil

REGISTRY_DIR = ".agent-bridge"


def registry_dir() -> Path:
    return Path.home() / REGISTRY_DIR


def find_root(start: str | os.PathLike[str] | None = None) -> Path:
    """Project root = git toplevel of BRIDGE_PROJECT_ROOT (or cwd). Refuses non-git dirs."""
    base = Path(start or os.environ.get("BRIDGE_PROJECT_ROOT") or os.getcwd()).resolve()
    out = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"not a git repository: {base}")
    return Path(out.stdout.strip()).resolve()


def known_projects() -> list[str]:
    """Project roots (worktrees included) where agent-bridge has run on this machine."""
    with contextlib.suppress(OSError, ValueError):
        data = json.loads((registry_dir() / "projects.json").read_text())
        if isinstance(data, list):
            return [p for p in data if isinstance(p, str)]
    return []


@contextlib.contextmanager
def projects_lock() -> Iterator[None]:
    """Serialises every read-modify-write of projects.json on this machine."""
    base = registry_dir()
    base.mkdir(mode=0o700, exist_ok=True)
    with (base / "projects.lock").open("a") as handle:
        osutil.lock(handle)
        yield


def save_projects(roots: Iterable[str]) -> None:
    """Replace projects.json atomically; the caller holds projects_lock."""
    base = registry_dir()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = base / f"projects.json.{os.getpid()}.tmp"
    try:
        temporary.write_text(json.dumps(sorted(roots), indent=2) + "\n")
        os.replace(temporary, base / "projects.json")
    finally:
        temporary.unlink(missing_ok=True)


def remember_project(root: Path) -> None:
    """Add a project to the machine-wide list `agent-bridge tasks` reads; best effort."""
    projects = known_projects()
    if str(root) in projects:
        return
    with contextlib.suppress(OSError), projects_lock():
        save_projects({*known_projects(), str(root)})


def with_worktrees(roots: Iterable[str]) -> list[str]:
    """Existing roots, each followed by its existing git worktrees, without duplicates."""
    found: dict[str, None] = {}
    for root in dict.fromkeys(roots):
        if not Path(root).is_dir():
            continue
        out = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, check=False)
        found |= dict.fromkeys([root, *(line.removeprefix("worktree ")
                                        for line in out.stdout.splitlines()
                                        if line.startswith("worktree ")
                                        and Path(line.removeprefix("worktree ")).is_dir())])
    return list(found)
