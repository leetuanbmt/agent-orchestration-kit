"""Project boundary checks, path patterns, secret redaction and environment scrubbing."""

from __future__ import annotations

import fnmatch
import functools
import re
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath

GLOB_CHARS = set("*?[")


class PathError(ValueError):
    pass


@functools.lru_cache(maxsize=512)
def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """`**` crosses directories, `*`/`?` do not. A glob-free pattern also covers its subtree."""
    if not GLOB_CHARS & set(pattern):
        return re.compile(re.escape(pattern.rstrip("/")) + r"(?:/.*)?")
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + r"(?:.*/)?", i + 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out, i = out + r"(?:/.*)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + r".*", i + 2
        elif pattern[i] == "*":
            out, i = out + r"[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + r"[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(_pattern_regex(p).fullmatch(path) for p in patterns)


def validate_pattern(pattern: str, root: Path, protected: Iterable[str]) -> str:
    """Accept a project-relative path/glob that stays inside root and is not protected."""
    p = pattern.strip()
    if not p or "\x00" in p or "\\" in p:
        raise PathError(f"invalid path: {pattern!r}")
    pure = PurePosixPath(p)
    if pure.is_absolute() or p.startswith("~"):
        raise PathError(f"path must be project-relative: {pattern!r}")
    if ".." in pure.parts:
        raise PathError(f"path traversal rejected: {pattern!r}")
    # Resolve the literal prefix so a symlinked directory cannot point outside the project.
    literal = []
    for part in pure.parts:
        if GLOB_CHARS & set(part):
            break
        literal.append(part)
    resolved = root.joinpath(*literal).resolve()
    if resolved != root and root not in resolved.parents:
        raise PathError(f"path escapes the project (symlink?): {pattern!r}")
    norm = str(pure)
    if norm in (".", "**", "*") or matches(norm, protected) or matches(norm + "/x", protected):
        raise PathError(f"protected or over-broad path: {pattern!r}")
    return norm


def inside_root(path: Path, root: Path) -> bool:
    r = path.resolve()
    return r == root or root in r.parents


# Order matters: the specific token shapes before the generic key=value rule.
_SECRET_RES = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|token|secret|password|passwd))\b(\s*[:=]\s*)"
        r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
]


def redact(text: str | None, limit: int = 4000) -> str:
    """Mask credential-shaped substrings and keep only the tail (most diagnostic part)."""
    if not text:
        return ""
    for rx in _SECRET_RES:
        if rx.groups >= 3:
            text = rx.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        elif rx.groups == 1 and rx.pattern.startswith("(?i)\\b(bearer)"):
            text = rx.sub(lambda m: f"{m.group(1)} [REDACTED]", text)
        else:
            text = rx.sub("[REDACTED]", text)
    return text if len(text) <= limit else "…" + text[-limit:]


def clean_env(env: Mapping[str, str], deny: Iterable[str], allow: Iterable[str]) -> dict[str, str]:
    deny, allow = list(deny), set(allow)
    return {
        k: v for k, v in env.items()
        if k in allow or not any(fnmatch.fnmatchcase(k.upper(), d.upper()) for d in deny)
    }
