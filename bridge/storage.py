"""Resolve per-worktree bridge state without moving existing databases."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import install

HOME_ENV = "AGENT_BRIDGE_HOME"
Location = NamedTuple("Location", [("dir", Path), ("kind", str)])


def home(root: Path | None = None) -> Path:
    path = Path(os.environ.get(HOME_ENV) or Path.home() / ".agent-bridge")
    if not path.is_absolute():
        raise ValueError(f"{HOME_ENV} must be absolute")
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("bridge home must be a directory, not a symlink")
    if path.exists() and hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise ValueError("bridge home must belong to the current user")
    if root is not None and path.resolve().is_relative_to(root.resolve()):
        raise ValueError("bridge home must be outside the worktree")
    return path


def _key(path: Path, name: str, fallback: str, length: int) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-.")[:40] or fallback
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:length]
    return f"{slug}-{digest}"


def _project_name(path: Path) -> str:
    return path.parent.name if path.name == ".git" else path.name.removesuffix(".git")


def project_key(common_git_dir: Path) -> str:
    path = common_git_dir.resolve()
    return _key(path, _project_name(path), "project", 12)


def worktree_key(root: Path) -> str:
    path = root.resolve()
    return _key(path, path.name, "worktree", 8)


def _metadata(path: Path, data: dict) -> None:
    if path.exists():
        return
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(data) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def locate(root: Path, create: bool = False) -> Location:
    gdir = install.git_dir(root)
    commondir = gdir / "commondir"
    common = (gdir / commondir.read_text().strip()).resolve() if commondir.exists() else gdir
    base = home(root)
    project = base / "projects" / project_key(common)
    central = project / "worktrees" / worktree_key(root)
    if not (central / "bridge.db").exists():
        current, old = gdir / "agent-bridge", gdir / "codex-bridge"
        if any(path.exists() for path in (current / "bridge.db", old / "bridge.db",
                                         root / ".ai" / "bridge.db")):
            directory = install.bridge_dir(gdir) if create else current if current.exists() else old
            return Location(directory, "legacy")
    if create:
        for directory in (base, base / "projects", project, project / "worktrees", central):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        base.chmod(0o700)
        central.chmod(0o700)
        created = datetime.now(timezone.utc).isoformat()
        _metadata(project / "project.json", {"name": _project_name(common.resolve()),
                  "common_git_dir": str(common.resolve()), "created_at": created})
        _metadata(central / "worktree.json", {"root": str(root.resolve()),
                  "git_dir": str(gdir.resolve()), "created_at": created})
    return Location(central, "central")


def store_dir(root: Path, create: bool = False) -> Path:
    return locate(root, create=create).dir
