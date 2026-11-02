"""Resolve per-worktree bridge state without moving existing databases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import fcntl
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing, contextmanager
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


def _paths(root: Path) -> tuple[Path, Path, Path]:
    gdir = install.git_dir(root)
    commondir = gdir / "commondir"
    common = (gdir / commondir.read_text().strip()).resolve() if commondir.exists() else gdir
    base = home(root)
    project = base / "projects" / project_key(common)
    central = project / "worktrees" / worktree_key(root)
    return gdir, common, central


def locate(root: Path, create: bool = False) -> Location:
    gdir, common, central = _paths(root)
    base = home(root)
    project = central.parent.parent
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


def central_stores() -> dict[str, Path]:
    stores = {}
    for directory in sorted((home() / "projects").glob("*/worktrees/*")):
        if ".partial-" in directory.name or not directory.is_dir():
            continue
        if not (directory / "bridge.db").is_file():
            continue
        try:
            metadata = json.loads((directory / "worktree.json").read_text())
            root = metadata.get("root") if isinstance(metadata, dict) else None
            if isinstance(root, str):
                stores[str(Path(root).resolve())] = directory
        except (OSError, ValueError):
            continue
    return stores


def find_store(root: str | Path) -> Path:
    path = Path(root)
    if path.is_dir():
        return store_dir(path, create=False) / "bridge.db"
    directory = central_stores().get(str(path.resolve()))
    if directory is None:
        raise FileNotFoundError(root)
    return directory / "bridge.db"


TABLES = ("tasks", "attempts", "events", "workflows", "accounts", "account_quota")


def fingerprint(conn) -> dict:
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = {}
    for name in TABLES:
        if name in existing:
            digest = hashlib.sha256()
            count = 0
            for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid"):
                digest.update(repr(tuple(row)).encode())
                count += 1
            tables[name] = {"count": count, "sha256": digest.hexdigest()}
    return {"integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "tables": tables, "max_event_id":
            (conn.execute("SELECT max(id) FROM events").fetchone()[0] or 0)
            if "events" in existing else 0}


def running_processes() -> list[dict]:
    try:
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                                capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    processes = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            try:
                processes[int(parts[0])] = (int(parts[1]), parts[2])
            except ValueError:
                continue
    excluded = set()
    pid = os.getpid()
    while pid and pid not in excluded:
        excluded.add(pid)
        pid = processes.get(pid, (0, ""))[0]
    return [{"pid": pid, "command": cmd} for pid, (_, cmd) in processes.items()
            if pid not in excluded and ("agent-bridge" in cmd or "-m bridge" in cmd)]


def _readonly(path: Path):
    # SQLite may create an empty WAL and create or update SHM even with mode=ro.
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def plan(here: Path | None = None) -> list[dict]:
    from . import accounts, config, progress
    from .models import ACTIVE

    roots = dict.fromkeys(str(Path(r).resolve()) for r in
                          [*accounts.known_projects(), *progress.discover_roots(here)])
    entries = []
    for value in roots:
        root = Path(value)
        entry = dict(root=value, status="missing", source=None, target=None,
                     bytes=0, active=0, error=None)
        entries.append(entry)
        if not root.is_dir():
            continue
        try:
            top_level = config.find_root(root) == root.resolve()
        except RuntimeError:
            top_level = False
        if not top_level:
            entry.update(status="not_git", error="not a worktree top level")
            continue
        try:
            gdir, _, target = _paths(root)
        except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
            entry.update(status="not_git", error=str(exc))
            continue
        entry["target"] = str(target)
        source = next((p for p in (gdir / "agent-bridge/bridge.db",
                                   gdir / "codex-bridge/bridge.db", root / ".ai/bridge.db")
                       if p.exists()), None)
        entry["source"] = str(source) if source else None
        if (target / "bridge.db").exists():
            entry["status"] = "migrated"
            continue
        if source is None:
            entry["status"] = "no_state"
            continue
        try:
            files = [source, source.with_name(source.name + "-wal"),
                     *source.parent.joinpath("logs").glob("*.jsonl")]
            entry["bytes"] = sum(p.stat().st_size for p in files if p.is_file())
            with closing(_readonly(source)) as conn:
                fp = fingerprint(conn)
                if fp["integrity"] != "ok":
                    raise RuntimeError("integrity check failed")
                if "tasks" in fp["tables"]:
                    states = tuple(ACTIVE)
                    marks = ",".join("?" for _ in states)
                    entry["active"] = conn.execute(
                        f"SELECT count(*) FROM tasks WHERE state IN ({marks})", states).fetchone()[0]
            entry["status"] = "ready"
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            entry.update(status="corrupt", error=str(exc))
    return entries


def _copy_logs(source: Path, partial: Path) -> dict:
    logs = {}
    for path in sorted((source.parent / "logs").glob("*.jsonl")):
        directory = partial / "logs"
        directory.mkdir(mode=0o700, exist_ok=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        dest = directory / path.name
        shutil.copyfile(path, dest)
        dest.chmod(0o600)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            raise RuntimeError("copy differs")
        logs[path.name] = digest
    return logs


def _migrate_entry(entry: dict) -> None:
    source, target = Path(entry["source"]), Path(entry["target"])
    try:
        for leftover in target.parent.glob(target.name + ".partial-*"):
            shutil.rmtree(leftover)
        if target.exists():
            raise RuntimeError("target already exists")
        partial = target.with_name(target.name + f".partial-{os.getpid()}")
        for directory in reversed([partial, *partial.parents]):
            if not directory.exists():
                directory.mkdir(mode=0o700)
            if directory.is_relative_to(home()):
                directory.chmod(0o700)
        gdir, common, _ = _paths(Path(entry["root"]))
        created = datetime.now(timezone.utc).isoformat()
        _metadata(target.parent.parent / "project.json", {
            "name": _project_name(common.resolve()), "common_git_dir": str(common.resolve()),
            "created_at": created})
        with closing(_readonly(source)) as conn:
            before = fingerprint(conn)
            with closing(sqlite3.connect(partial / "bridge.db")) as dest:
                conn.backup(dest)
                logs = _copy_logs(source, partial)
                copied = fingerprint(dest)
            after = fingerprint(conn)
        if any(fp["integrity"] != "ok" for fp in (before, after, copied)):
            raise RuntimeError("integrity check failed")
        if before != after:
            raise RuntimeError("source changed during copy")
        if before != copied:
            raise RuntimeError("copy differs")
        _metadata(partial / "worktree.json", {"root": str(Path(entry["root"]).resolve()),
                  "git_dir": str(gdir.resolve()), "created_at": created})
        _metadata(partial / "migration.json", {"source": str(source), "migrated_at": created,
                  "fingerprint": before, "logs": logs, "source_max_event_id": before["max_event_id"]})
        os.replace(partial, target)
        entry["status"] = "migrated_now"
    except Exception as exc:
        entry.update(status="failed", error=str(exc))


def migrate(here: Path | None = None, apply: bool = False) -> dict:
    base = home()
    if not apply:
        return {"applied": False, "home": str(base), "running": running_processes(),
                "sources": plan(here)}
    with _storage_guard() as running:
        entries = plan(here)
        for entry in entries:
            if entry["status"] == "ready":
                _migrate_entry(entry)
        return {"applied": True, "home": str(base), "running": running, "sources": entries}


@contextmanager
def _storage_guard():
    base = home()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    locks = base / "locks"
    locks.mkdir(mode=0o700, exist_ok=True)
    locks.chmod(0o700)
    with (locks / "migrate.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another storage migration is running") from exc
        running = running_processes()
        if running:
            raise RuntimeError("bridge processes are running: " + ", ".join(str(p["pid"]) for p in running))
        yield running


def prune(apply: bool = False) -> dict:
    if apply:
        with _storage_guard():
            return _prune(True)
    return _prune(False)


def _prune(apply: bool) -> dict:
    from . import accounts

    roots = accounts.known_projects()
    removed = [root for root in roots if not Path(root).is_dir()]
    kept = [root for root in roots if root not in removed]
    if apply:
        base = accounts.registry_dir()
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = base / f"projects.json.{os.getpid()}.tmp"
        try:
            temporary.write_text(json.dumps(sorted(kept), indent=2) + "\n")
            os.replace(temporary, base / "projects.json")
        finally:
            temporary.unlink(missing_ok=True)
    return {"applied": apply, "removed": removed, "kept": len(kept)}


def gc(days: float, apply: bool = False) -> dict:
    if not days >= 1:
        raise ValueError("days must be >= 1")
    if apply:
        with _storage_guard():
            return _gc(days, True)
    return _gc(days, False)


def _gc(days: float, apply: bool) -> dict:
    from .models import TERMINAL

    cutoff = time.time() - days * 86400
    files = []
    total = 0
    for central in central_stores().values():
        with closing(_readonly(central / "bridge.db")) as conn:
            terminal = {row[0] for row in conn.execute(
                "SELECT id FROM tasks WHERE state IN (?, ?, ?)", tuple(TERMINAL))}
        for path in sorted((central / "logs").glob("*.jsonl")):
            task, separator, attempt = path.stem.rpartition("-")
            if not separator or not attempt.isdecimal() or task not in terminal:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if stat.st_mtime < cutoff:
                files.append(str(path))
                total += stat.st_size
    if apply:
        for path in files:
            Path(path).unlink()
    return {"applied": apply, "files": files, "bytes": total}


def _archive_dir(kind: str, central: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = home() / kind / stamp / central.parent.parent.name / central.name
    for directory in reversed([destination, *destination.parents]):
        if directory.is_relative_to(home()):
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
    return destination


def rollback(root: Path, yes: bool = False) -> dict:
    root = root.resolve()
    with _storage_guard():
        central = central_stores().get(str(root))
        if central is None and root.exists():
            central = _paths(root)[2]
        if central is None or not (central / "migration.json").is_file():
            raise RuntimeError("only migrated stores can be rolled back")
        metadata = json.loads((central / "migration.json").read_text())
        if not Path(metadata["source"]).is_file():
            raise RuntimeError("legacy source no longer exists")
        with closing(_readonly(central / "bridge.db")) as conn:
            has_events = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
            hidden = conn.execute("SELECT count(*) FROM events WHERE id > ?",
                                  (metadata["source_max_event_id"],)).fetchone()[0] if has_events else 0
        if hidden and not yes:
            raise RuntimeError(f"{hidden} events would be hidden; pass --yes to roll back")
        destination = _archive_dir("rollback", central)
        os.replace(central, destination)
        return {"root": str(root), "moved_to": str(destination), "hidden_events": hidden}


def cleanup_legacy(apply: bool = False) -> dict:
    if not apply:
        return _cleanup_legacy(False)
    with _storage_guard():
        return _cleanup_legacy(True)


def _cleanup_legacy(apply: bool) -> dict:
    entries = []
    for root, central in central_stores().items():
        manifest = central / "migration.json"
        if not manifest.is_file():
            continue
        entry = {"root": root, "source": None, "status": "changed", "error": None}
        entries.append(entry)
        try:
            metadata = json.loads(manifest.read_text())
            source = Path(metadata["source"])
            entry["source"] = str(source)
            if not source.exists():
                entry["status"] = "gone"
                continue
            with closing(_readonly(source)) as conn:
                current = fingerprint(conn)
            recorded = metadata["fingerprint"]
            if (current["integrity"] != "ok" or current["tables"] != recorded["tables"]
                    or current["max_event_id"] != recorded["max_event_id"]):
                entry["error"] = "legacy database changed since migration"
                continue
            entry["status"] = "ready"
            if apply:
                destination = _archive_dir("backups", central)
                files = [source, Path(str(source) + "-wal"), Path(str(source) + "-shm")]
                git_dir = Path(json.loads((central / "worktree.json").read_text())["git_dir"])
                if source.parent in (git_dir / "agent-bridge", git_dir / "codex-bridge"):
                    files.append(source.parent / "logs")
                if any((destination / path.name).exists() for path in files):
                    raise RuntimeError("backup destination already exists")
                for path in files:
                    if path.exists():
                        shutil.move(str(path), str(destination / path.name))
                entry["status"] = "moved"
        except (OSError, ValueError, KeyError, sqlite3.Error, RuntimeError) as exc:
            entry.update(status="changed", error=str(exc))
    return {"applied": apply, "entries": entries}
