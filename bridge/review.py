"""Evidence for review: what Codex changed (hash snapshots, not commits), whether it stayed in
scope, and the bridge's own run of the verification commands."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import BridgeConfig
from .security import clean_env, matches, redact


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False,
                         env={**os.environ, **env} if env else None)
    return out.stdout.decode(errors="replace") if out.returncode == 0 else ""


def _worktree_tree(root: Path) -> str | None:
    """Tree object of the whole working tree (tracked + untracked, .gitignore respected),
    built in a throwaway index so the real index and history are untouched."""
    real = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "index"
        if real.is_file():
            shutil.copyfile(real, tmp)  # reuse stat cache: only touched files get rehashed
        env = {"GIT_INDEX_FILE": str(tmp)}
        _git(root, "add", "-A", env=env)
        return _git(root, "write-tree", env=env).strip() or None


def _hash(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dirty(root: Path) -> set[str]:
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths, parts = set(), out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            paths.add(entry[3:])
            if entry[0] in "RC":  # rename/copy: next field is the source path
                i += 1
                if i < len(parts) and parts[i]:
                    paths.add(parts[i])
        i += 1
    return paths


def snapshot(root: Path, protected: list[str]) -> dict[str, Any]:
    """Hashes of every dirty/untracked file plus top-level protected files (often gitignored).

    ponytail: nested gitignored secrets (e.g. pkg/.env) are not hashed; walking ignored trees
    (.venv, node_modules) on every run is too slow. The sandbox and prompt rules cover them.
    """
    files = _dirty(root)
    for pat in protected:
        if "**" in pat:
            continue
        files.update(str(p.relative_to(root)) for p in root.glob(pat) if p.is_file())
    return {
        "head": _git(root, "rev-parse", "HEAD").strip() or None,
        "tree": _worktree_tree(root),
        "files": {f: _hash(root / f) for f in files},
    }


def diff(root: Path, before: dict[str, Any], protected: list[str]) -> dict[str, Any]:
    after = snapshot(root, protected)
    b, a = before["files"], after["files"]
    # Clean before and dirty now => changed. Dirty before => compare with the current content.
    changed = sorted(f for f in set(b) | set(a)
                     if f not in b or b[f] != (a[f] if f in a else _hash(root / f)))
    trees = (before.get("tree"), after["tree"])
    stat = patch = ""
    if all(trees) and trees[0] != trees[1]:
        stat = _git(root, "diff", "--stat", *trees)  # type: ignore[arg-type]
        patch = _git(root, "diff", "--no-color", *trees)  # type: ignore[arg-type]
    return {
        "files": changed,
        "head_before": before["head"],
        "head_after": after["head"],
        "head_changed": before["head"] != after["head"],
        "stat": stat.strip()[:4000],
        "patch": redact(patch, PATCH_LIMIT),
    }


PATCH_LIMIT = 30000


def scope_violations(changed: list[str], spec: dict[str, Any], cfg: BridgeConfig,
                     head_changed: bool) -> list[str]:
    v = []
    for f in changed:
        if matches(f, cfg.protected_paths):
            v.append(f"protected file changed: {f}")
        elif matches(f, spec.get("excluded_paths", [])):
            v.append(f"excluded path changed: {f}")
        elif not matches(f, spec["allowed_paths"]):
            v.append(f"outside allowed paths: {f}")
    if head_changed:
        v.append("git HEAD moved: the executor must not commit")
    return v


def touches_sensitive(changed: list[str], cfg: BridgeConfig) -> bool:
    return any(matches(f, cfg.sensitive_paths) for f in changed)


def parse_command(cmd: str, cfg: BridgeConfig) -> list[str]:
    """argv for a verification command; no shell, executable must be allowlisted."""
    argv = shlex.split(cmd)
    if not argv:
        raise ValueError("empty verification command")
    exe = os.path.basename(argv[0])
    if exe not in cfg.verify_allowlist:
        raise ValueError(f"verification executable not allowlisted: {exe!r}")
    return argv


async def run_verification(cmds: list[str], root: Path, cfg: BridgeConfig) -> list[dict[str, Any]]:
    results = []
    # No .pyc litter from the bridge's own checks in the next attempt's baseline.
    env = {**clean_env(os.environ, cfg.env_deny, cfg.env_allow), "PYTHONDONTWRITEBYTECODE": "1"}
    for cmd in cmds:
        r: dict[str, Any] = {"command": cmd, "passed": False}
        try:
            argv = parse_command(cmd, cfg)
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=root, env=env, start_new_session=True, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), cfg.verify_timeout_s)
                r["exit_code"] = proc.returncode
                r["passed"] = proc.returncode == 0
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, 9)
                await proc.wait()
                out, r["error"] = b"", f"timed out after {cfg.verify_timeout_s}s"
            r["output_tail"] = redact(out.decode(errors="replace"), 3000)
        except (ValueError, OSError) as e:
            r["error"] = str(e)
        results.append(r)
    return results
