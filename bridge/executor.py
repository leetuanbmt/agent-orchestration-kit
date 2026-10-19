"""Codex CLI adapter: builds argv for `codex exec` / `codex exec resume`, runs it in its own
process group, parses the JSONL event stream and classifies failures.

Verified against codex-cli 0.163.0 (see docs/COMPATIBILITY.md). `exec resume` has no -s/-C,
so the sandbox goes through `-c sandbox_mode=...` and the cwd is set on the subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .accounts import Account, account_env, codex_overrides
from .config import BridgeConfig, state_dir
from .security import clean_env, redact

# Final-message contract enforced by `codex exec --output-schema` (strict: all keys required).
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "files_changed", "tests_run", "notes"],
    "properties": {
        "status": {"type": "string", "enum": ["completed", "blocked"]},
        "summary": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "tests_run": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
}

# Heuristics over Codex's own error text. Unmatched failures stay "crash", never guessed.
_CLASSES = [
    ("quota", re.compile(r"usage limit|rate.?limit|too many requests|\b429\b|quota|try again at",
                         re.I)),
    ("auth", re.compile(r"\b401\b|unauthori[sz]ed|not logged in|log ?in again|"
                        r"authentication (?:required|failed)|missing bearer", re.I)),
    ("network", re.compile(r"could not resolve|connection (?:refused|reset|closed)|network|"
                           r"stream disconnected|error sending request|\b50[234]\b|dns", re.I)),
]

KILL_GRACE_S = 5.0
POLL_S = 1.0


def classify(text: str) -> str:
    for name, rx in _CLASSES:
        if rx.search(text):
            return name
    return "crash"


@dataclass
class RunResult:
    outcome: str  # ok | quota | auth | network | timeout | cancelled | crash | missing_binary
    exit_code: int | None = None
    session_id: str | None = None
    final: dict[str, Any] | None = None
    final_text: str = ""
    usage: dict[str, Any] | None = None
    error: str = ""
    stderr_tail: str = ""
    commands: list[str] = field(default_factory=list)


class CodexAdapter:
    def __init__(self, cfg: BridgeConfig, root: Path):
        self.cfg, self.root = cfg, root
        self.state_dir = state_dir(root)
        self.schema_path = self.state_dir / "output_schema.json"

    def available(self) -> str | None:
        return shutil.which(self.cfg.codex_bin)

    def argv(self, *, sandbox: str, last_msg: Path, account: Account,
             session_id: str | None = None) -> list[str]:
        if sandbox not in ("read-only", "workspace-write"):
            raise ValueError(f"sandbox not allowed: {sandbox}")
        a = [self.cfg.codex_bin, "exec"]
        if session_id:
            a.append("resume")
        a += ["--json", "-o", str(last_msg), "--output-schema", str(self.schema_path)]
        if self.cfg.ignore_user_config:
            a.append("--ignore-user-config")
        if session_id:
            a += ["-c", f'sandbox_mode="{sandbox}"']
        else:
            a += ["-s", sandbox, "-C", str(self.root)]
        # workspace-write otherwise leaves /tmp and $TMPDIR writable (verified by probe).
        a += ["-c", "sandbox_workspace_write.exclude_slash_tmp=true",
              "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true"]
        a += codex_overrides(account)
        if self.cfg.model:
            a += ["-m", self.cfg.model]
        if session_id:
            a.append(session_id)
        a.append("-")  # prompt on stdin: no argv length limit, not visible in `ps`
        return a

    async def run(self, prompt: str, *, account: Account, sandbox: str, timeout_s: int,
                  session_id: str | None = None,
                  on_start: Callable[[int], None] | None = None,
                  on_session: Callable[[str], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None) -> RunResult:
        if not self.available():
            return RunResult("missing_binary", error=f"{self.cfg.codex_bin!r} not found on PATH")
        self.schema_path.parent.mkdir(parents=True, exist_ok=True)
        self.schema_path.write_text(json.dumps(OUTPUT_SCHEMA))
        with tempfile.TemporaryDirectory(dir=self.state_dir) as tmp:
            last_msg = Path(tmp) / "last_message.json"
            argv = self.argv(sandbox=sandbox, last_msg=last_msg, session_id=session_id,
                             account=account)
            env = account_env(account, clean_env(os.environ, self.cfg.env_deny, self.cfg.env_allow))
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self.root, env=env, start_new_session=True,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            )
            if on_start:
                on_start(proc.pid)
            res = RunResult("ok")
            stderr: deque[str] = deque(maxlen=60)
            errors: list[str] = []
            completed = False

            async def feed() -> None:
                assert proc.stdin
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    proc.stdin.write(prompt.encode())
                    await proc.stdin.drain()
                    proc.stdin.close()

            async def read_stdout() -> None:
                nonlocal completed
                assert proc.stdout
                async for raw in proc.stdout:
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    t = ev.get("type")
                    item = ev.get("item") or {}
                    if t == "thread.started":
                        res.session_id = ev.get("thread_id")
                        if on_session and res.session_id:
                            on_session(res.session_id)  # recovery checkpoint before any work
                    elif t == "turn.completed":
                        completed, res.usage = True, ev.get("usage")
                    elif t == "turn.failed":
                        errors.append(str((ev.get("error") or {}).get("message", "")))
                    elif t == "error":
                        errors.append(str(ev.get("message", "")))
                    elif t == "item.completed" and item.get("type") == "agent_message":
                        res.final_text = item.get("text", "")
                    elif t == "item.completed" and item.get("type") == "command_execution":
                        res.commands.append(str(item.get("command", ""))[:300])
                    # item.type == "error" can be non-fatal noise (e.g. hook warnings): ignored.

            async def read_stderr() -> None:
                assert proc.stderr
                async for raw in proc.stderr:
                    stderr.append(raw.decode(errors="replace").rstrip())

            async def watchdog() -> str:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout_s
                while proc.returncode is None:
                    if should_cancel and should_cancel():
                        return "cancelled"
                    if loop.time() >= deadline:
                        return "timeout"
                    await asyncio.sleep(POLL_S)
                return ""

            io = asyncio.gather(feed(), read_stdout(), read_stderr())
            dog = asyncio.create_task(watchdog())
            waiter = asyncio.create_task(proc.wait())
            await asyncio.wait({dog, waiter}, return_when=asyncio.FIRST_COMPLETED)
            stop = dog.result() if dog.done() else ""
            if stop:
                await kill_group(proc)
            dog.cancel()
            await waiter
            with contextlib.suppress(Exception):
                await asyncio.wait_for(io, timeout=5)
            res.exit_code = proc.returncode
            res.stderr_tail = redact("\n".join(stderr))
            if last_msg.exists():
                res.final_text = last_msg.read_text() or res.final_text

        with contextlib.suppress(ValueError, TypeError):
            parsed = json.loads(res.final_text)
            res.final = parsed if isinstance(parsed, dict) else None
        res.final_text = redact(res.final_text)
        if stop:
            res.outcome = stop
        elif res.exit_code == 0 and completed:
            res.outcome = "ok"
        else:
            text = "\n".join(errors) + "\n" + res.stderr_tail
            res.outcome = classify(text)
            res.error = redact(errors[-1] if errors else res.stderr_tail[-1000:], 2000)
        return res


async def kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the whole process group (Codex spawns shells), then SIGKILL after a grace period."""
    for sig, wait in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, KILL_GRACE_S)):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(proc.wait(), timeout=wait)
            return
        except TimeoutError:
            continue


def kill_stale(pid: int | None) -> bool:
    """Kill a leftover Codex process group from a dead runner, only if it really is codex."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    ps = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))],
                        capture_output=True, text=True, check=False)
    if "codex" not in ps.stdout:
        return False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGTERM)
    return True


def build_prompt(task_id: str, spec: dict[str, Any], protected: list[str]) -> str:
    lines = [
        "You are the EXECUTOR in a Claude (planner/reviewer) -> Codex (executor) workflow.",
        f"Implement task {task_id} exactly as specified. A reviewer will inspect your diff, and the",
        "bridge re-runs the verification commands itself, so report results honestly.",
        "", f"## Objective\n{spec['objective']}",
    ]
    if spec.get("context"):
        lines.append(f"\n## Context\n{spec['context']}")
    if spec.get("scope"):
        lines.append(f"\n## Scope\n{spec['scope']}")
    lines.append("\n## Allowed paths (create/modify ONLY these)")
    lines += [f"- {p}" for p in spec["allowed_paths"]]
    lines.append("\n## Never touch")
    lines += [f"- {p}" for p in [*spec.get("excluded_paths", []), *protected]]
    lines.append("\n## Acceptance criteria")
    lines += [f"- {c}" for c in spec["acceptance_criteria"]]
    if spec.get("verification_commands"):
        lines.append("\n## Verification commands (run them before finishing)")
        lines += [f"- `{c}`" for c in spec["verification_commands"]]
    lines += [
        "\n## Rules",
        "- Stay inside the allowed paths. Do not create commits, branches or tags; do not push.",
        "- Do not read, print or modify secrets or credentials.",
        "- No network access: do not install or upgrade dependencies.",
        "- If the task cannot be done within scope, stop and return status \"blocked\" with why.",
        "- Final message: JSON matching the provided output schema.",
    ]
    return "\n".join(lines)


def build_rework_prompt(task_id: str, feedback: str, last_verification: Any) -> str:
    return "\n".join([
        f"REWORK for task {task_id}. Same objective, scope and rules as before.",
        "", "## Reviewer feedback", feedback.strip() or "(none)",
        "", "## Last bridge verification", json.dumps(last_verification, default=str)[:6000],
        "", "Fix only what is needed. Final message: JSON matching the output schema.",
    ])
