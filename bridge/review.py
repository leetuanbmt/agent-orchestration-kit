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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import osutil
from .config import BridgeConfig
from .models import PROTECTED_VIOLATION
from .security import clean_env, matches, redact


class EvidenceError(RuntimeError):
    """Git could not report the worktree, so a run cannot be judged: never treat that as clean."""


def _git(root: Path, *args: str, env: dict[str, str] | None = None,
         required: bool = True) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False,
                             env={**os.environ, **env} if env else None)
    except OSError as e:
        raise EvidenceError(f"git {args[0]}: {e}") from e
    if out.returncode != 0:
        if not required:
            return ""
        detail = redact(out.stderr.decode(errors="replace").strip(), 300)
        raise EvidenceError(f"git {args[0]} failed with exit {out.returncode}: {detail}")
    return out.stdout.decode(errors="replace")


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
    # The bridge's own config; orchestrator.yaml is git-ignored, so _dirty misses it.
    files.update(f for f in (".ai/config.json", ".ai/orchestrator.yaml") if (root / f).is_file())
    return {
        # A repository without commits has no HEAD; that is not an evidence failure.
        "head": _git(root, "rev-parse", "--verify", "-q", "HEAD", required=False).strip() or None,
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
            v.append(f"{PROTECTED_VIOLATION}{f}")
        elif matches(f, spec.get("excluded_paths", [])):
            v.append(f"excluded path changed: {f}")
        elif not matches(f, spec["allowed_paths"]):
            v.append(f"outside allowed paths: {f}")
    if head_changed:
        v.append("git HEAD moved: the executor must not commit")
    return v


def protected_dirty(root: Path, cfg: BridgeConfig) -> list[str]:
    """Tracked protected files whose content differs from HEAD (e.g. a tampered .ai/config.json
    or .mcp.json): they would be loaded by the next bridge start."""
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    paths = [entry[3:] for entry in out.split("\0") if len(entry) > 3]
    return sorted(p for p in paths if matches(p, cfg.protected_paths))


# Instructions and settings that agents, shells and editors load from the project. A change to
# one steers every later run, so it always needs a human, whatever sensitive_paths says.
AGENT_INSTRUCTIONS = ["CLAUDE.md", "**/CLAUDE.md", "AGENTS.md", "**/AGENTS.md", ".claude/**",
                      ".codex/**", ".envrc", "**/.envrc", ".vscode/**"]


def touches_sensitive(changed: list[str], cfg: BridgeConfig) -> bool:
    return any(matches(f, cfg.sensitive_paths) or matches(f, AGENT_INSTRUCTIONS) for f in changed)


def _shell_operators(cmd: str) -> list[str]:
    """Unquoted shell operators (&&, |, ;, >, 2>&1, ...), written with or without spaces."""
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars="&|;<>")
    lexer.whitespace_split = True
    return [token for token in lexer if set(token) <= set("&|;<>")]


def parse_command(cmd: str, cfg: BridgeConfig) -> list[str]:
    """argv for a verification command; no shell, executable must be allowlisted."""
    argv = shlex.split(cmd)
    if not argv:
        raise ValueError("empty verification command")
    if ops := _shell_operators(cmd):
        raise ValueError(f"verification commands run without a shell, so {ops[0]!r} would be "
                         "passed as an argument; give each command separately")
    exe = os.path.basename(argv[0])
    if exe not in cfg.verify_allowlist:
        raise ValueError(f"verification executable not allowlisted: {exe!r}")
    return argv


# Files a test run leaves behind; created by verification alone they are not scope violations.
VERIFICATION_NOISE = ["__pycache__/**", "**/__pycache__/**", "*.pyc", "**/*.pyc",
                      ".pytest_cache/**", "**/.pytest_cache/**"]


def recheck(root: Path, before: dict[str, Any], changes: dict[str, Any],
            cfg: BridgeConfig) -> dict[str, Any]:
    """The worktree again after verification, against the same snapshot. Whatever the checks
    changed or left behind is judged like the agent's own changes, except test-cache files."""
    final = diff(root, before, cfg.protected_paths)
    later = [f for f in final["files"] if f not in changes["files"]]
    return {"files": sorted({*changes["files"],
                             *(f for f in later if not matches(f, VERIFICATION_NOISE))}),
            "changed_by_verification": later,
            "head_changed": changes["head_changed"] or final["head_changed"]}


async def run_verification(cmds: list[str], root: Path, cfg: BridgeConfig,
                           should_cancel: Callable[[], bool] | None = None
                           ) -> list[dict[str, Any]]:
    """Run each command in its own process group. A cancel (polled every second) kills the
    running command and skips the rest."""
    results = []
    # No .pyc litter from the bridge's own checks in the next attempt's baseline.
    env = {**clean_env(os.environ, cfg.env_deny, cfg.env_allow), "PYTHONDONTWRITEBYTECODE": "1"}
    loop = asyncio.get_running_loop()
    for cmd in cmds:
        if should_cancel and should_cancel():
            break
        r: dict[str, Any] = {"command": cmd, "passed": False}
        try:
            argv = parse_command(cmd, cfg)
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=root, env=env, **osutil.GROUP_KWARGS, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            io = asyncio.ensure_future(proc.communicate())
            deadline = loop.time() + cfg.verify_timeout_s
            stop = ""
            # returncode is set when the command exits; proc.wait() would also wait for every
            # pipe to close, which a child left behind can delay for as long as it likes.
            while proc.returncode is None:
                if should_cancel and should_cancel():
                    stop = "cancelled"
                elif loop.time() >= deadline:
                    stop = f"timed out after {cfg.verify_timeout_s}s"
                if stop:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        osutil.kill_group(proc.pid, force=True)
                    break
                await asyncio.wait({io}, timeout=min(0.1, max(deadline - loop.time(), 0)))
            await osutil.end_group(proc.pid)  # nothing the check started outlives it
            await asyncio.wait({io}, timeout=5)  # a process that left the group may hold it
            if io.done():
                out, _ = io.result()
            else:
                io.cancel()
                out = b""
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 5)
            if stop:
                r["error"] = stop
            else:
                r["exit_code"] = proc.returncode
                r["passed"] = proc.returncode == 0
            r["output_tail"] = redact((out or b"").decode(errors="replace"), 3000)
        except (ValueError, OSError) as e:
            r["error"] = str(e)
        results.append(r)
    return results
