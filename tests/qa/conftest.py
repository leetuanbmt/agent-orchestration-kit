"""End-to-end sandbox: the real `agent-bridge` CLI, MCP server and web dashboard as a user runs
them, against fake agent CLIs, in a throwaway HOME and git repository. Nothing here touches the
user's real home, accounts or projects."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

KIT = Path(__file__).resolve().parents[2]
FAKES = KIT / "tests" / "fakes"
BIN = Path(sys.executable).parent


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def json(self) -> Any:
        return json.loads(self.out)


@dataclass
class Sandbox:
    base: Path
    home: Path
    root: Path
    env: dict[str, str]
    procs: list[subprocess.Popen] = field(default_factory=list)

    # ---- running the product -------------------------------------------------------------

    def cli(self, *args: str, cwd: Path | None = None, timeout: float = 120,
            env: dict[str, str] | None = None) -> Result:
        out = subprocess.run([str(BIN / "agent-bridge"), *args], cwd=cwd or self.root,
                             env={**self.env, **(env or {})}, capture_output=True, text=True,
                             timeout=timeout, stdin=subprocess.DEVNULL)
        return Result(out.returncode, out.stdout, out.stderr)

    def ok(self, *args: str, **kw: Any) -> Any:
        """Run a command that must succeed and print JSON (stdout is piped, so it does)."""
        res = self.cli(*args, **kw)
        assert res.code == 0, f"agent-bridge {' '.join(args)} -> {res.code}\n{res.err}\n{res.out}"
        return res.json

    def spawn(self, *args: str, env: dict[str, str] | None = None) -> subprocess.Popen:
        proc = subprocess.Popen([str(BIN / "agent-bridge"), *args], cwd=self.root,
                                env={**self.env, **(env or {})}, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        self.procs.append(proc)
        return proc

    def spec(self, objective: str = "Set x to 2 in src/app.py", **extra: Any) -> Path:
        data = {"objective": objective, "allowed_paths": ["src/**"],
                "acceptance_criteria": ["src/app.py contains x = 2"], **extra}
        path = self.base / f"spec-{time.monotonic_ns()}.json"
        path.write_text(json.dumps(data))
        return path

    def create(self, objective: str = "Set x to 2 in src/app.py", **extra: Any) -> str:
        return self.ok("create", str(self.spec(objective, **extra)))["task_id"]

    # ---- configuration (fixtures of this throwaway project, never the kit's own .ai/) -------

    def config(self, **values: Any) -> None:
        path = self.root / ".ai" / "config.json"
        path.parent.mkdir(exist_ok=True)
        current = json.loads(path.read_text()) if path.exists() else {}
        path.write_text(json.dumps({**current, **values}))

    def orchestrator(self, text: str) -> None:
        """Write .ai/orchestrator.yaml; `{home}` expands to a directory of logged-in homes."""
        homes = self.base / "homes"
        text = text.replace("{home}", str(homes))
        (self.root / ".ai").mkdir(exist_ok=True)
        (self.root / ".ai" / "orchestrator.yaml").write_text(text)
        for line in text.splitlines():
            if "home:" in line and str(homes) in line:
                home = Path(line.split("home:", 1)[1].strip().strip(",}").split(",")[0].strip())
                home.mkdir(parents=True, exist_ok=True)
                (home / ".credentials.json").touch()
                (home / "auth.json").touch()

    # ---- evidence ------------------------------------------------------------------------

    def db(self) -> sqlite3.Connection:
        status = self.cli("health").json
        conn = sqlite3.connect(f"file:{status['database']}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def events(self, task: str, kind: str | None = None) -> list[dict[str, Any]]:
        with self.db() as conn:
            rows = conn.execute("SELECT kind, actor, from_state, to_state, detail FROM events "
                                "WHERE task_id=? ORDER BY id", (task,)).fetchall()
        out = [dict(r) | {"detail": json.loads(r["detail"]) if r["detail"] else None}
               for r in rows]
        return [e for e in out if kind is None or e["kind"] == kind]

    def calls(self, agent: str = "codex") -> list[dict[str, Any]]:
        log = Path(self.env[f"FAKE_{agent.upper()}_DIR"]) / "log.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def wait_state(self, task: str, states: set[str], timeout: float = 30) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            row = self.ok("status", task)
            if row["state"] in states or time.monotonic() > deadline:
                return row
            time.sleep(0.3)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def make_project(path: Path) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "src" / "app.py").write_text("x = 1\n")
    (path / ".gitignore").write_text(".env\n")
    (path / ".env").write_text("SECRET_TOKEN=abc\n")
    git(path, "init", "-q")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init")
    return path.resolve()


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[Sandbox]:
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "tmp").mkdir()
    for exe in FAKES.iterdir():
        exe.chmod(exe.stat().st_mode | 0o111)
    env = {k: v for k, v in os.environ.items()
           if k not in {"CODEX_HOME", "CLAUDE_CONFIG_DIR", "AGENT_BRIDGE_AGENT",
                        "BRIDGE_PROJECT_ROOT", "AGENT_BRIDGE_HOME"}}
    env.update(HOME=str(home), TMPDIR=str(tmp_path / "tmp"),
               PATH=f"{FAKES}{os.pathsep}{BIN}{os.pathsep}{os.environ['PATH']}",
               FAKE_CODEX_DIR=str(tmp_path / "fake" / "codex"),
               FAKE_CLAUDE_DIR=str(tmp_path / "fake" / "claude"),
               FAKE_OPENCODE_DIR=str(tmp_path / "fake" / "opencode"),
               FAKE_AGY_DIR=str(tmp_path / "fake" / "agy"),
               FAKE_CODEX_SCENARIO="ok", FAKE_CLAUDE_SCENARIO="ok", PYTHONUNBUFFERED="1")
    for name in ("codex", "claude"):
        Path(env[f"FAKE_{name.upper()}_DIR"]).mkdir(parents=True)
    root = make_project(tmp_path / "proj")
    box = Sandbox(tmp_path, home, root, env)
    installed = box.cli("init")  # what a user does first; installs .mcp.json, CLAUDE.md, .claude/
    assert installed.code == 0 and (root / ".mcp.json").exists(), installed.err
    box.config(retry_backoff_s=0, lock_dir=str(tmp_path / "locks"))
    yield box
    for proc in box.procs:
        if proc.poll() is None:
            with _suppress():
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(5)
        for stream in (proc.stdout, proc.stderr):
            if stream:
                stream.close()


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return exc[0] is not None and issubclass(exc[0], (ProcessLookupError, PermissionError))


def alive(pid: int) -> bool:
    """True while `pid` runs (a zombie counts as gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """The end-to-end suite takes minutes; it runs with QA_E2E=1 (`scripts/check.sh --e2e`)."""
    if os.environ.get("QA_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="end-to-end suite: set QA_E2E=1 or run scripts/check.sh --e2e")
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(skip)
