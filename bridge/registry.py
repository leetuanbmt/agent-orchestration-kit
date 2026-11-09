"""Machine-wide project registry: the project roots agent-bridge has run in, and their worktrees."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import osutil

REGISTRY_DIR = ".agent-bridge"
HOME_ENV = "AGENT_BRIDGE_HOME"
_held = threading.local()


def bridge_home() -> Path:
    """The validated bridge home shared by central storage and the machine registry."""
    path = Path(os.environ.get(HOME_ENV) or Path.home() / REGISTRY_DIR)
    if not path.is_absolute():
        raise ValueError(f"{HOME_ENV} must be absolute")
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("bridge home must be a directory, not a symlink")
    if path.exists() and hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise ValueError("bridge home must belong to the current user")
    return path


def registry_dir() -> Path:
    return bridge_home()


def adopt_legacy() -> None:
    """Copy accounts.json (and projects.json) from ~/.agent-bridge into a custom bridge home.

    Copy only, once, never overwriting; homes/ and everything else stay where they are."""
    if not os.environ.get(HOME_ENV):
        return
    target = bridge_home()
    legacy = Path.home() / REGISTRY_DIR
    if (target.resolve() == legacy.resolve() or not (legacy / "accounts.json").is_file()
            or (target / "accounts.json").exists()):
        return
    with contextlib.suppress(OSError), projects_lock():
        if (target / "accounts.json").exists():
            return
        for name in ("accounts.json", "projects.json"):
            source, dest = legacy / name, target / name
            if source.is_file() and not source.is_symlink() and not dest.exists():
                _copy_new(source, dest)


def _copy_new(source: Path, dest: Path) -> None:
    temporary = dest.with_name(f"{dest.name}.{os.getpid()}.adopt.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(source.read_bytes())
        os.link(temporary, dest)  # fails if dest appeared meanwhile: never overwrite
    except FileExistsError:
        pass
    finally:
        temporary.unlink(missing_ok=True)


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
        adopt_legacy()
        data = json.loads((registry_dir() / "projects.json").read_text())
        if isinstance(data, list):
            return [p for p in data if isinstance(p, str)]
    return []


@contextlib.contextmanager
def projects_lock() -> Iterator[None]:
    """Serialises every read-modify-write of the registry files on this machine.

    Re-entrant within a thread: a holder may call helpers that take it again."""
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    base = registry_dir()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (base / "projects.lock").open("a") as handle:
        osutil.lock(handle)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0


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
