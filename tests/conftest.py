"""Shared fixtures: a throwaway git repo and the fake `codex` binary first on PATH."""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
from pathlib import Path

import pytest

from bridge.config import BridgeConfig
from bridge.service import Bridge

FAKES = Path(__file__).parent / "fakes"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\n")
    (root / ".gitignore").write_text(".env\n")
    (root / ".env").write_text("SECRET_TOKEN=abc\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root.resolve()


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    exe = FAKES / "codex"
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    state = tmp_path / "fake_state"
    monkeypatch.setenv("PATH", f"{FAKES}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_DIR", str(state))
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok")
    return state


@pytest.fixture
def make_bridge(repo: Path, fake_codex: Path):
    def make(**cfg: object) -> Bridge:
        return Bridge(repo, BridgeConfig(**{"retry_backoff_s": 0, "lock_dir": str(fake_codex / "locks"), **cfg}))  # type: ignore[arg-type]
    return make


def run(coro):  # noqa: ANN001, ANN201 - tiny sync shim; no pytest-asyncio dependency
    return asyncio.run(coro)


def spec(**over: object) -> dict:
    base = {
        "objective": "Set x to 2 in src/app.py",
        "allowed_paths": ["src/**"],
        "acceptance_criteria": ["src/app.py contains x = 2"],
        "verification_commands": [
            "python3 -c \"import pathlib,sys; "
            "sys.exit(0 if 'x = 2' in pathlib.Path('src/app.py').read_text() else 1)\""
        ],
    }
    return {**base, **over}
