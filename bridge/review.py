"""Evidence for review: what Codex changed (hash snapshots, not commits), whether it stayed in
scope, and the bridge's own run of the verification commands."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from . import osutil, sandbox
from .config import DEFAULT_ENV_DENY, BridgeConfig
from .executor import AGENT_ENV
from .models import PROTECTED_VIOLATION
from .security import clean_env, literal_prefix, matches, redact


class EvidenceError(RuntimeError):
    """Git could not report the worktree, so a run cannot be judged: never treat that as clean."""


def _git(root: Path, *args: str, env: dict[str, str] | None = None,
         required: bool = True) -> str:
    """Run git in `root` with the repository's code-running config switched off (core.fsmonitor
    and core.hooksPath, passed through GIT_CONFIG_COUNT/KEY/VALUE so the argv stays
    `git -C root <subcommand>`; they take -c precedence, git >= 2.31) and the credential-shaped
    environment variables dropped. Defence in depth: tampering is also caught by hashing the git
    internals before git runs."""
    if args and args[0] == "diff":
        args = (args[0], "--no-ext-diff", "--no-textconv", *args[1:])
    clean = clean_env(os.environ, DEFAULT_ENV_DENY, [])
    overrides = {"GIT_CONFIG_COUNT": "2",
                 "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": "false",
                 "GIT_CONFIG_KEY_1": "core.hooksPath", "GIT_CONFIG_VALUE_1": "/dev/null"}
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, check=False,
                             env={**clean, **overrides, **(env or {})})
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


def untracked(root: Path) -> set[str]:
    """Non-ignored untracked files in the worktree."""
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    paths = set()
    parts = iter(out.split("\0"))
    for entry in parts:
        if entry.startswith("?? "):
            paths.add(entry[3:])
        elif entry and entry[0] in "RC":
            next(parts, None)
    return paths


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
        "files": {**{f: _hash(root / f) for f in files}, **_git_internals(root)},
        "ignored": {f: s for f, s in _ignored(root).items() if f not in files},
    }


def _git_dirs(root: Path) -> tuple[Path, Path] | None:
    """(git dir, common git dir) found on disk without running git; None when the layout is
    not recognised. Main checkout: `.git/` is a directory. Linked worktree: `.git` is a file
    `gitdir: <path>` whose directory has a `commondir` file; config and hooks live in the common
    directory."""
    dot = root / ".git"
    if dot.is_dir():
        return dot, dot
    if not dot.is_file():
        return None
    try:
        line = dot.read_text(errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line[len("gitdir:"):].strip())
    gitdir = (gitdir if gitdir.is_absolute() else root / gitdir).resolve()
    if not gitdir.is_dir():
        return None
    common = gitdir
    marker = gitdir / "commondir"
    if marker.is_file():
        try:
            rel = Path(marker.read_text().strip())
        except OSError:
            return None
        common = (rel if rel.is_absolute() else gitdir / rel).resolve()
        if not common.is_dir():
            return None
    return gitdir, common


def git_dirs(root: Path) -> tuple[Path, Path]:
    """_git_dirs, asking git only when the layout is unrecognisable."""
    found = _git_dirs(root)
    if found:
        return found

    def ask(flag: str) -> Path:
        return Path(_git(root, "rev-parse", "--path-format=absolute", flag).strip())
    return ask("--git-dir"), ask("--git-common-dir")


def git_internal_path(root: Path, key: str) -> Path:
    """Where a `.git/...` key lives on disk (per-worktree config.worktree is in the git dir)."""
    gitdir, common = git_dirs(root)
    name = key[len(".git/"):]
    return (gitdir if name == "config.worktree" else common) / name


def git_write_denies(root: Path) -> list[str]:
    """Paths a verification command must not write: the repository's config, hooks and info.
    Not the whole git dir: the index must stay writable."""
    gitdir, common = git_dirs(root)
    return [str(common / "config"), str(gitdir / "config.worktree"), str(common / "hooks"),
            str(common / "info")]


def verification_read_denies(root: Path, homes: list[str] | list[Path]) -> list[str]:
    """Credential paths a verification command must not read (Seatbelt): every account home, the
    registry's homes directory, the global Codex/Claude logins and the project's private bridge config files."""
    from .registry import registry_dir
    home = Path.home()
    paths = [*(str(Path(h).expanduser()) for h in homes), str(registry_dir() / "homes"),
             str(home / ".codex" / "auth.json"), str(home / ".claude" / ".credentials.json"),
             str(Path(root) / ".ai" / "orchestrator.yaml"), str(Path(root) / ".ai" / "accounts.yaml")]
    return list(dict.fromkeys(str(Path(p).absolute()) for p in paths))


def _git_internals(root: Path) -> dict[str, str | None]:
    """Hooks, config and attributes of the repository, keyed as .git/... (a protected path).
    Anything that can write the worktree can write them in a main checkout, and git runs hooks
    (and config commands such as core.fsmonitor) outside any sandbox. Read from disk, no git."""
    gitdir, common = git_dirs(root)
    found = {".git/config": _hash(common / "config")}
    if (gitdir / "config.worktree").is_file():
        found[".git/config.worktree"] = _hash(gitdir / "config.worktree")
    if (common / "info" / "attributes").is_file():
        found[".git/info/attributes"] = _hash(common / "info" / "attributes")
    hooks = common / "hooks"
    if hooks.is_dir():
        found.update({f".git/hooks/{p.name}": _hash(p) for p in hooks.iterdir() if p.is_file()})
    return found


def tampered_internals(root: Path, before: dict[str, Any]) -> list[str]:
    """`.git/...` keys whose content differs from the baseline, found without running git."""
    b = {k: v for k, v in before["files"].items() if k.startswith(".git/")}
    if not b:
        return []  # a baseline stored before git internals were recorded
    now = _git_internals(root)
    return sorted(k for k in set(b) | set(now) if b.get(k) != now.get(k))


def _ignored(root: Path) -> dict[str, tuple[int, int] | None]:
    """(size, mtime_ns) of every git-ignored file. No hashing: 100 000 files take ~0.2 s."""
    out = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    stats: dict[str, tuple[int, int] | None] = {}
    for name in filter(None, out.split("\0")):
        try:
            st = os.lstat(root / name)
            stats[name] = (st.st_size, st.st_mtime_ns)
        except OSError:
            stats[name] = None
    return stats


class Changes(TypedDict):
    """What an attempt changed, as stored on the attempt and read by every reviewer and view.
    Attempts stored before patch_truncated / patch_omitted_files existed lack those keys."""
    files: list[str]
    head_before: str | None
    head_after: str | None
    head_changed: bool
    stat: str
    patch: str
    patch_truncated: bool
    patch_omitted_files: list[str]
    ignored_files: list[str]  # git-ignored files added, removed or modified (size or mtime)


_FILE_DIFF = re.compile(r"(?m)^(?=diff --git )")
_DIFF_NAME = re.compile(r"^diff --git a/(.*?) b/(.*)$", re.M)
_MIN_SHARE = 400  # less than this shows no useful part of a file: name it as omitted instead


def bounded_patch(patch: str, limit: int) -> tuple[str, bool, list[str]]:
    """The redacted patch within `limit` characters, each file's diff kept from its start.
    Files share the limit (small ones whole, the rest split evenly); a cut file says how much is
    missing, and files left with too little room are omitted and named instead."""
    chunks = [c for c in _FILE_DIFF.split(patch) if c]
    texts = [redact(c, len(c) + 1) for c in chunks]
    if sum(map(len, texts)) <= limit:
        return "".join(texts), False, []
    room, share = limit, {}
    for i in sorted(range(len(texts)), key=lambda i: len(texts[i])):
        share[i] = min(len(texts[i]), room // (len(texts) - len(share)))
        room -= share[i]
    out, omitted = [], []
    for i, text in enumerate(texts):
        if share[i] >= len(text):
            out.append(text)
        elif share[i] >= _MIN_SHARE:
            out.append(text[:share[i]] + f"\n… [{len(text) - share[i]} more characters of this "
                       "file's diff not shown]\n")
        else:
            m = _DIFF_NAME.match(text)
            omitted.append(m.group(2) if m else text.split("\n", 1)[0])
    return "".join(out), True, omitted


def diff(root: Path, before: dict[str, Any], protected: list[str]) -> Changes:
    if tampered := tampered_internals(root, before):
        # Never run git against a repository whose config or hooks changed: report only that.
        return {"files": tampered, "head_before": before["head"], "head_after": before["head"],
                "head_changed": False, "stat": "", "patch": "", "patch_truncated": False,
                "patch_omitted_files": [], "ignored_files": []}
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
    shown, truncated, omitted = bounded_patch(patch, PATCH_LIMIT)
    ig_b, ig_a = before.get("ignored"), after["ignored"]
    # A stored baseline has no ignored-file listing: compare ignored files only when it does.
    ignored = ([] if ig_b is None else
               sorted(f for f in set(ig_b) | set(ig_a) if ig_b.get(f) != ig_a.get(f)))
    return {
        "files": changed,
        "head_before": before["head"],
        "head_after": after["head"],
        "head_changed": before["head"] != after["head"],
        "stat": stat.strip()[:4000],
        "patch": shown,
        "patch_truncated": truncated,
        "patch_omitted_files": omitted,
        "ignored_files": ignored,
    }


PATCH_LIMIT = 30000


# Files a test run leaves behind; created by verification alone they are not scope violations.
VERIFICATION_NOISE = ["__pycache__/**", "**/__pycache__/**", "*.pyc", "**/*.pyc",
                      ".pytest_cache/**", "**/.pytest_cache/**"]


# Git-ignored files tools rewrite all the time; changes to them are never violations.
IGNORED_NOISE = [*VERIFICATION_NOISE, ".mypy_cache/**", "**/.mypy_cache/**", ".ruff_cache/**",
                 "**/.ruff_cache/**", "node_modules/.cache/**", ".coverage", "**/.coverage"]


def escaping_symlinks(root: Path, patterns: list[str]) -> list[str]:
    """Links covered by patterns that escape root; ignored links need explicit coverage."""
    tracked = _git(root, "ls-files", "-s", "-z")
    links = {entry.split("\t", 1)[1] for entry in tracked.split("\0")
             if entry.startswith("120000 ")}
    links.update(path for path in _git(root, "ls-files", "-o", "--exclude-standard", "-z")
                 .split("\0") if path and (root / path).is_symlink())
    ignored_links = {path for path in
                     _git(root, "ls-files", "-o", "-i", "--exclude-standard", "-z").split("\0")
                     if path and (root / path).is_symlink()}
    prefixes = [(pattern, literal_prefix(pattern)) for pattern in patterns]
    resolved_root = root.resolve()
    return sorted(link for link in links | ignored_links
                  if any(matches(link, [pattern])
                         or (prefix and (link == prefix or link.startswith(prefix + "/")))
                         or (not prefix and (link in links or (root / link).resolve().is_dir()))
                         for pattern, prefix in prefixes)
                  and (root / link).is_symlink()
                  and not (root / link).resolve().is_relative_to(resolved_root))


def scope_violations(changed: list[str], spec: dict[str, Any], cfg: BridgeConfig,
                     head_changed: bool, ignored: list[str] | None = None, *,
                     enforce_ignored: bool = False, root: Path | None = None,
                     verification_ignored: list[str] | None = None) -> list[str]:
    """Agent ignored changes gate scope; verification-only ignored changes gate when enforced.
    Protected paths and escaping symlinks always gate, regardless of their source."""
    v = []
    for f in changed:
        if (root is not None and (root / f).is_symlink()
                and not (root / f).resolve().is_relative_to(root.resolve())):
            v.append(f"symlink escapes the project: {f}")
        if matches(f, cfg.protected_paths):
            v.append(f"{PROTECTED_VIOLATION}{f}")
        elif matches(f, spec.get("excluded_paths", [])):
            v.append(f"excluded path changed: {f}")
        elif not matches(f, spec["allowed_paths"]):
            v.append(f"outside allowed paths: {f}")
    for f in ignored or []:
        if (root is not None and (root / f).is_symlink()
                and not (root / f).resolve().is_relative_to(root.resolve())):
            v.append(f"symlink escapes the project: {f}")
        if matches(f, cfg.protected_paths):
            v.append(f"{PROTECTED_VIOLATION}{f}")
        elif ((enforce_ignored or f not in (verification_ignored or []))
              and not matches(f, IGNORED_NOISE)
              and not matches(f, spec["allowed_paths"])):
            v.append(f"ignored file changed outside allowed paths: {f}")
    if head_changed:
        v.append("git HEAD moved: the executor must not commit")
    return v


def content_hashes(root: Path, files: list[str]) -> dict[str, str | None]:
    """sha256 of each file as it is now (None for a missing file): what approval binds to."""
    return {f: _hash(root / f) for f in files}


def protected_after(root: Path, violations: list[str]) -> dict[str, dict[str, str | None]]:
    """What a stopped run left in each protected file it changed. `health` keeps reporting a file
    while it still has this content; a restore or a human edit changes the hash and clears it."""
    paths = [v[len(PROTECTED_VIOLATION):] for v in violations if v.startswith(PROTECTED_VIOLATION)]
    return {"protected_after": protected_hashes(root, paths)} if paths else {}


def protected_hashes(root: Path, paths: list[str]) -> dict[str, str | None]:
    """Like content_hashes, but `.git/...` keys (from _git_internals) resolve through the git
    directory (no git run), since `.git` is a file in a linked worktree."""
    def where(f: str) -> Path:
        return git_internal_path(root, f) if f.startswith(".git/") else root / f
    return {f: _hash(where(f)) for f in paths}


def protected_git_left(root: Path, left: list[dict[str, str | None]]) -> list[str]:
    """`.git/...` paths a stopped run changed that still hold what it left (checked on disk)."""
    return sorted({f for entry in left for f, h in entry.items()
                   if f.startswith(".git/") and protected_hashes(root, [f])[f] == h})


def current_head(root: Path) -> str | None:
    return _git(root, "rev-parse", "--verify", "-q", "HEAD", required=False).strip() or None


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
    if "/" in argv[0] or "\\" in argv[0]:
        raise ValueError(f"verification commands name an allowlisted program, not a path: "
                         f"{argv[0]!r} (e.g. 'npx jest' instead of './node_modules/.bin/jest')")
    exe = argv[0]
    if exe not in cfg.verify_allowlist:
        raise ValueError(f"verification executable not allowlisted: {exe!r}")
    return argv


def recheck(root: Path, before: dict[str, Any], changes: dict[str, Any],
            cfg: BridgeConfig) -> dict[str, Any]:
    """The worktree again after verification, against the same snapshot. Whatever the checks
    changed or left behind is judged like the agent's own changes, except test-cache files."""
    final = diff(root, before, cfg.protected_paths)
    later = [f for f in final["files"] if f not in changes["files"]]
    later_ignored = [f for f in final["ignored_files"] if f not in changes["ignored_files"]]
    return {"files": sorted({*changes["files"],
                             *(f for f in later if not matches(f, VERIFICATION_NOISE))}),
            "ignored_files": sorted({*changes["ignored_files"], *(
                f for f in later_ignored if not matches(f, IGNORED_NOISE))}),
            "changed_by_verification": later + later_ignored,
            "head_changed": changes["head_changed"] or final["head_changed"]}


async def run_verification(cmds: list[str], root: Path, cfg: BridgeConfig,
                           should_cancel: Callable[[], bool] | None = None,
                           on_start: Callable[[int | None], None] | None = None,
                           confine: sandbox.Mode = "none", writable: list[str] | None = None,
                           read_deny: list[str] | None = None) -> list[dict[str, Any]]:
    """Run each command in its own process group. A cancel (polled every second) kills the
    running command and skips the rest. `on_start` gets each command's pid when it starts and
    None once its group has ended, so a dead runner's check can be found and killed.

    `confine` is the workflow's verify_sandbox: under Landlock a check may write only to the
    worktree, its own temporary directory and `writable`."""
    results: list[dict[str, Any]] = []
    tmp = tempfile.mkdtemp(prefix="agent-bridge-check-")
    try:
        await _verify(cmds, root, cfg, should_cancel, on_start, confine,
                      [str(root), tmp, *(str(Path(p).expanduser()) for p in writable or [])],
                      tmp, results, git_write_denies(root) if confine != "none" else [],
                      read_deny if confine != "none" else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


async def _verify(cmds: list[str], root: Path, cfg: BridgeConfig,
                  should_cancel: Callable[[], bool] | None,
                  on_start: Callable[[int | None], None] | None, confine: sandbox.Mode,
                  writable: list[str], tmp: str, results: list[dict[str, Any]],
                  deny_write: list[str] | None = None,
                  deny_read: list[str] | None = None) -> None:
    # No .pyc litter from the bridge's own checks in the next attempt's baseline; temp files go
    # to a private directory removed afterwards (the only temp location a confined check has).
    env = {**clean_env(os.environ, cfg.env_deny, []), "PYTHONDONTWRITEBYTECODE": "1",
           AGENT_ENV: "1", "TMPDIR": tmp, "TMP": tmp, "TEMP": tmp}
    loop = asyncio.get_running_loop()
    for cmd in cmds:
        if should_cancel and should_cancel():
            break
        r: dict[str, Any] = {"command": cmd, "passed": False}
        try:
            argv = sandbox.wrap(parse_command(cmd, cfg), writable, confine, deny_write, deny_read)
            if argv is None:
                raise ValueError("verify_sandbox is strict but no verification sandbox is available here: "
                                 "the check was not run")
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=root, env=env, **osutil.GROUP_KWARGS, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            if on_start:
                on_start(proc.pid)
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
            if on_start:
                on_start(None)
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
