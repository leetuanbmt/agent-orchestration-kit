"""Provider adapters: start an agent CLI in its own process group, stream its JSONL events,
enforce timeout/cancel, and return a structured result.

Each adapter only builds argv/env and interprets its CLI's events; the process handling is shared.
Codex: verified against codex-cli 0.163.0. `exec resume` has no -s/-C, so the sandbox goes through
`-c sandbox_mode=...` and the cwd is set on the subprocess. Claude: verified against Claude Code
2.1.306 `-p --output-format stream-json --json-schema`.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
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
from .security import PathError, clean_env, process_started, redact, rule_unsafe

# Set in every agent process; the bridge entry points refuse to start under it, so an agent
# cannot orchestrate other agents.
AGENT_ENV = "AGENT_BRIDGE_AGENT"

# Final-message contract enforced through each CLI's structured-output flag (strict: all keys
# required, no extras).
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
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "notes"],
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "rework", "block"]},
        "notes": {"type": "string"},
    },
}
_PLAN_TASK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["objective", "context", "scope", "allowed_paths", "excluded_paths",
                 "acceptance_criteria", "verification_commands", "depends_on", "risk_level"],
    "properties": {
        "objective": {"type": "string"},
        "context": {"type": "string"},
        "scope": {"type": "string"},
        "allowed_paths": {"type": "array", "items": {"type": "string"}},
        "excluded_paths": {"type": "array", "items": {"type": "string"}},
        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
        "verification_commands": {"type": "array", "items": {"type": "string"}},
        "depends_on": {"type": "array", "items": {"type": "integer"}},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
    },
}
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tasks", "notes"],
    "properties": {"tasks": {"type": "array", "items": _PLAN_TASK}, "notes": {"type": "string"}},
}

# Heuristics over the CLI's own error text. Unmatched failures stay "crash", never guessed.
_CLASSES = [
    ("quota", re.compile(r"usage limit|rate.?limit|too many requests|\b429\b|quota|try again at",
                         re.I)),
    ("auth", re.compile(r"\b401\b|unauthori[sz]ed|not logged in|log ?in again|/login|"
                        r"authentication (?:required|failed)|missing bearer|invalid api key|"
                        r"not authenticated|please sign in",
                        re.I)),
    ("network", re.compile(r"could not resolve|connection (?:refused|reset|closed)|network|"
                           r"stream disconnected|error sending request|\b50[234]\b|dns", re.I)),
]

KILL_GRACE_S = 5.0
POLL_S = 1.0
_CLAUDE_READ_TOOLS = ["Read", "Glob", "Grep"]
# Path-scoped rules: under dontAsk, Edit/Write outside these patterns are refused (verified by
# probe on claude 2.1.306; unscoped Write reached files outside the working directory).
_CLAUDE_WRITE_TOOLS = ("Edit", "Write")


@functools.cache
def _claude_credential_denies() -> list[str]:
    """The kit settings' Read denies (credential paths). Claude agents load no settings files,
    so they get these on every run through --disallowedTools (verified to block on 2.1.308)."""
    from .install import KIT
    deny = json.loads((KIT / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    return [rule for rule in deny if rule.startswith("Read(")]


def classify(text: str) -> str:
    for name, rx in _CLASSES:
        if rx.search(text):
            return name
    return "crash"


def describe_event(ev: dict[str, Any]) -> str | None:
    """Return a short, safe summary for user-visible execution progress."""
    t = ev.get("type")
    item = ev.get("item") or {}
    summary = None
    if t == "thread.started":
        summary = f"session started ({ev.get('thread_id', 'unknown')})"
    elif t == "item.completed" and item.get("type") == "command_execution":
        summary = f"command finished (exit {item.get('exit_code', 'unknown')}): {item.get('command', '')}"
    elif t == "item.completed" and item.get("type") == "file_change":
        changes = item.get("changes") or []
        summary = "file edits: " + ", ".join(str(c.get("path", "")) for c in changes
                                               if isinstance(c, dict))
    elif t == "item.completed" and item.get("type") == "agent_message":
        summary = f"agent: {item.get('text', '')}"
    elif t == "turn.completed":
        summary = "turn completed"
    elif t == "turn.failed":
        summary = f"turn failed: {(ev.get('error') or {}).get('message', '')}"
    elif t == "error":
        summary = f"error: {ev.get('message', '')}"
    elif t == "system" and ev.get("subtype") == "init":
        summary = f"session started ({ev.get('session_id', 'unknown')})"
    elif t == "assistant":
        for block in (ev.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input") or {}
                detail = next((inp[k] for k in ("file_path", "command", "pattern")
                               if inp.get(k)), "")
                summary = f"tool {block.get('name', 'unknown')}: {detail}"
                break
    elif t == "result":
        summary = f"result {ev.get('subtype', 'unknown')}: {ev.get('result', '')}"
    return redact(" ".join(summary.split()), 200) if summary else None


@dataclass
class RunRequest:
    prompt: str
    account: Account
    sandbox: str  # read-only | workspace-write
    timeout_s: int
    session_id: str | None = None
    schema: dict[str, Any] = field(default_factory=lambda: OUTPUT_SCHEMA)
    model: str | None = None
    allow_bash: bool = False
    # Project-relative globs a writing run may change, and globs it must never change.
    write_paths: list[str] = field(default_factory=list)
    deny_paths: list[str] = field(default_factory=list)


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
    # Normalised {"primary"|"secondary": {used_percent, window_minutes, resets_at}} when the CLI
    # reports its limits during the run.
    rate_limits: dict[str, Any] | None = None


class Adapter:
    """Shared subprocess runner. Subclasses set `binary` and implement argv/handle/finish."""

    name = ""
    provider = ""
    supports_resume = True

    def __init__(self, cfg: BridgeConfig, root: Path):
        self.cfg, self.root = cfg, root
        self.state_dir = state_dir(root)

    @property
    def binary(self) -> str:
        raise NotImplementedError

    def available(self) -> str | None:
        return shutil.which(self.binary)

    def build(self, req: RunRequest, tmp: Path) -> list[str]:
        raise NotImplementedError

    def handle(self, ev: dict[str, Any], res: RunResult, ctx: dict[str, Any]) -> None:
        raise NotImplementedError

    def finish(self, res: RunResult, tmp: Path, ctx: dict[str, Any]) -> bool:
        """Fill res.final_text from files if needed; return True when the turn completed."""
        raise NotImplementedError

    def extra_env(self, req: RunRequest) -> dict[str, str]:
        return {}

    def stdin(self, req: RunRequest) -> str:
        return req.prompt

    def version(self) -> str:
        try:
            out = subprocess.run([self.binary, "--version"], capture_output=True, text=True,
                                 timeout=20, check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"error: {e}"
        line = (out.stdout or out.stderr).strip().splitlines()
        return redact(line[0] if line else "", 200)

    async def run(self, req: RunRequest, *,
                  on_start: Callable[[int], None] | None = None,
                  on_session: Callable[[str], None] | None = None,
                  should_cancel: Callable[[], bool] | None = None,
                  on_line: Callable[[str, str], None] | None = None) -> RunResult:
        if req.sandbox not in ("read-only", "workspace-write"):
            raise ValueError(f"sandbox not allowed: {req.sandbox}")
        if not self.available():
            return RunResult("missing_binary", error=f"{self.binary!r} not found on PATH")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.state_dir) as tmpname:
            tmp = Path(tmpname)
            argv = self.build(req, tmp)
            env = account_env(req.account,
                              clean_env(os.environ, self.cfg.env_deny, self.cfg.env_allow))
            env |= self.extra_env(req)
            env[AGENT_ENV] = "1"
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=self.root, env=env, start_new_session=True,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            )
            if on_start:
                on_start(proc.pid)
            res = RunResult("ok")
            stderr: deque[str] = deque(maxlen=60)
            ctx: dict[str, Any] = {"errors": [], "on_session": on_session}

            async def feed() -> None:
                assert proc.stdin
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    proc.stdin.write(self.stdin(req).encode())
                    await proc.stdin.drain()
                    proc.stdin.close()

            async def read_stdout() -> None:
                assert proc.stdout
                async for raw in proc.stdout:
                    line = raw.decode(errors="replace").rstrip()
                    if on_line:
                        with contextlib.suppress(Exception):
                            on_line("stdout", line)
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(ev, dict):
                        self.handle(ev, res, ctx)

            async def read_stderr() -> None:
                assert proc.stderr
                async for raw in proc.stderr:
                    line = raw.decode(errors="replace").rstrip()
                    if on_line:
                        with contextlib.suppress(Exception):
                            on_line("stderr", line)
                    stderr.append(line)

            async def watchdog() -> str:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + req.timeout_s
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
            completed = self.finish(res, tmp, ctx)

        if res.final is None:
            with contextlib.suppress(ValueError, TypeError):
                parsed = json.loads(res.final_text)
                res.final = parsed if isinstance(parsed, dict) else None
        res.final_text = redact(res.final_text)
        errors: list[str] = ctx["errors"]
        if stop:
            res.outcome = stop
        elif res.exit_code == 0 and completed:
            res.outcome = "ok"
        else:
            text = "\n".join(errors) + "\n" + res.stderr_tail
            res.outcome = classify(text)
            res.error = redact(errors[-1] if errors else res.stderr_tail[-1000:], 2000)
        return res


class CodexAdapter(Adapter):
    name, provider = "codex-cli", "openai"

    def __init__(self, cfg: BridgeConfig, root: Path):
        super().__init__(cfg, root)
        self.schema_path = self.state_dir / "output_schema.json"

    @property
    def binary(self) -> str:
        return self.cfg.codex_bin

    def argv(self, *, sandbox: str, last_msg: Path, account: Account,
             session_id: str | None = None, schema_path: Path | None = None,
             model: str | None = None) -> list[str]:
        if sandbox not in ("read-only", "workspace-write"):
            raise ValueError(f"sandbox not allowed: {sandbox}")
        a = [self.cfg.codex_bin, "exec"]
        if session_id:
            a.append("resume")
        a += ["--json", "-o", str(last_msg), "--output-schema",
              str(schema_path or self.schema_path)]
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
        model = model or self.cfg.model
        if model:
            a += ["-m", model]
        if session_id:
            a.append(session_id)
        a.append("-")  # prompt on stdin: no argv length limit, not visible in `ps`
        return a

    def build(self, req: RunRequest, tmp: Path) -> list[str]:
        schema = tmp / "output_schema.json"
        schema.write_text(json.dumps(req.schema))
        return self.argv(sandbox=req.sandbox, last_msg=tmp / "last_message.json",
                         account=req.account, session_id=req.session_id, schema_path=schema,
                         model=req.model)

    def handle(self, ev: dict[str, Any], res: RunResult, ctx: dict[str, Any]) -> None:
        t = ev.get("type")
        item = ev.get("item") or {}
        if t == "thread.started":
            res.session_id = ev.get("thread_id")
            if ctx["on_session"] and res.session_id:
                ctx["on_session"](res.session_id)  # recovery checkpoint before any work
        elif t == "turn.completed":
            ctx["completed"], res.usage = True, ev.get("usage")
        elif t == "turn.failed":
            ctx["errors"].append(str((ev.get("error") or {}).get("message", "")))
        elif t == "error":
            ctx["errors"].append(str(ev.get("message", "")))
        elif t == "item.completed" and item.get("type") == "agent_message":
            res.final_text = item.get("text", "")
        elif t == "item.completed" and item.get("type") == "command_execution":
            res.commands.append(str(item.get("command", ""))[:300])
        # item.type == "error" can be non-fatal noise (e.g. hook warnings): ignored.

    def finish(self, res: RunResult, tmp: Path, ctx: dict[str, Any]) -> bool:
        last_msg = tmp / "last_message.json"
        if last_msg.exists():
            res.final_text = last_msg.read_text() or res.final_text
        return bool(ctx.get("completed"))


class ClaudeAdapter(Adapter):
    """Claude Code headless. No OS sandbox: tools are limited by an allowlist instead, MCP
    servers are disabled (no recursive orchestration) and writing needs `allow_write`."""

    name, provider = "claude-cli", "anthropic"

    @property
    def binary(self) -> str:
        return self.cfg.claude_bin

    def build(self, req: RunRequest, tmp: Path) -> list[str]:
        tools = list(_CLAUDE_READ_TOOLS)
        denied = list(_claude_credential_denies())
        if req.sandbox == "workspace-write":
            if bad := rule_unsafe([*req.write_paths, *req.deny_paths]):
                raise PathError(f"not expressible as Claude permission rules: {bad}")
            tools += [f"{t}(./{p})" for p in req.write_paths for t in _CLAUDE_WRITE_TOOLS]
            denied += [f"{t}(./{p})" for p in req.deny_paths for t in _CLAUDE_WRITE_TOOLS]
        if req.allow_bash:
            tools.append("Bash")
        # No settings files at all: their permissions.allow rules extend --allowedTools and their
        # hooks run shell commands, so the target project could widen a read-only agent.
        a = [self.binary, "-p", "--output-format", "stream-json", "--verbose",
             "--strict-mcp-config", "--setting-sources", "",
             "--permission-mode", "dontAsk", "--allowedTools", ",".join(tools),
             "--json-schema", json.dumps(req.schema)]
        a += ["--disallowedTools", ",".join(denied)]
        if req.model:
            a += ["--model", req.model]
        if req.session_id:
            a += ["--resume", req.session_id]
        return a  # the prompt goes on stdin

    def handle(self, ev: dict[str, Any], res: RunResult, ctx: dict[str, Any]) -> None:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            res.session_id = ev.get("session_id")
            if ctx["on_session"] and res.session_id:
                ctx["on_session"](res.session_id)
        elif t == "rate_limit_event":
            windows = (ev.get("rate_limit_info") or {}).get("unifiedWindows") or {}
            limits = {}
            for key, name, minutes in (("primary", "five_hour", 300),
                                       ("secondary", "seven_day", 10080)):
                w = windows.get(name)
                if isinstance(w, dict) and isinstance(w.get("utilization"), (int, float)):
                    limits[key] = {"used_percent": round(w["utilization"] * 100, 1),
                                   "window_minutes": minutes, "resets_at": w.get("resetsAt")}
            if limits:
                res.rate_limits = limits
        elif t == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" \
                        and block.get("name") == "Bash":
                    res.commands.append(str((block.get("input") or {}).get("command", ""))[:300])
        elif t == "result":
            res.session_id = ev.get("session_id") or res.session_id
            res.usage = ev.get("usage")
            if isinstance(ev.get("structured_output"), dict):
                res.final = ev["structured_output"]
            res.final_text = str(ev.get("result") or "")
            ok = not ev.get("is_error") and ev.get("subtype") == "success"
            ctx["completed"] = ok and res.final is not None
            if not ok:
                ctx["errors"].append(f"{ev.get('subtype')}: {ev.get('result') or ''} "
                                     f"{ev.get('api_error_status') or ''}".strip())
            elif res.final is None:
                ctx["errors"].append("no structured output")

    def finish(self, res: RunResult, tmp: Path, ctx: dict[str, Any]) -> bool:
        return bool(ctx.get("completed"))


def _json_object(text: str) -> dict[str, Any] | None:
    """The JSON object in a final message that may be wrapped in a code fence or prose."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    with contextlib.suppress(ValueError):
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    return None


class ReadOnlyAdapter(Adapter):
    """An adapter whose CLI has no verified way to confine writes: it never gets a writing run."""

    def build(self, req: RunRequest, tmp: Path) -> list[str]:
        if req.sandbox != "read-only":
            raise ValueError(f"{self.name} is read-only (planner/reviewer)")
        return self.argv(req)

    def argv(self, req: RunRequest) -> list[str]:
        raise NotImplementedError


# Inline opencode config for every run: no edits, shell, web fetches or paths outside the
# project; the project's own opencode config is ignored. Probed on opencode 2.1.2: with it the
# model reported having no tool to write files, and nothing was written.
_OPENCODE_READ_ONLY = {"permission": {"edit": "deny", "bash": "deny", "webfetch": "deny",
                                      "external_directory": "deny"}}


class OpenCodeAdapter(ReadOnlyAdapter):
    """`opencode run`. It has no structured-output flag: the schema goes into the prompt and the
    final text is parsed as JSON."""

    name, provider = "opencode-cli", "opencode"

    @property
    def binary(self) -> str:
        return self.cfg.opencode_bin

    def argv(self, req: RunRequest) -> list[str]:
        # --standalone: a private server per run instead of the user's background service.
        a = [self.binary, "run", "--format", "json", "--standalone"]
        if req.model:
            a += ["-m", req.model]
        if req.session_id:
            a += ["--session", req.session_id]
        return a  # the prompt goes on stdin

    def extra_env(self, req: RunRequest) -> dict[str, str]:
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(_OPENCODE_READ_ONLY),
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"}

    def stdin(self, req: RunRequest) -> str:
        return (f"{req.prompt}\n\nReply with exactly one JSON object and nothing else (no prose, "
                f"no code fence), matching this JSON schema:\n{json.dumps(req.schema)}")

    def handle(self, ev: dict[str, Any], res: RunResult, ctx: dict[str, Any]) -> None:
        if not res.session_id and ev.get("sessionID"):
            res.session_id = ev["sessionID"]
            if ctx["on_session"]:
                ctx["on_session"](res.session_id)
        part = ev.get("part") or {}
        t = ev.get("type")
        if t == "text" and part.get("text"):
            res.final_text = str(part["text"])
        elif t == "step_finish":
            res.usage = {k: part.get(k) for k in ("tokens", "cost") if k in part}
        elif t == "tool_use" and part.get("tool") == "bash":
            res.commands.append(str((part.get("state") or {}).get("input", ""))[:300])
        elif t == "error":
            err = ev.get("error") or {}
            # e.g. {"type": "provider.quota", "status": 402}: keep both for classify().
            ctx["errors"].append(f"{err.get('type', '')} {err.get('status', '')}: "
                                 f"{err.get('message', '')}".strip())

    def finish(self, res: RunResult, tmp: Path, ctx: dict[str, Any]) -> bool:
        res.final = _json_object(res.final_text)
        if res.final is None and not ctx["errors"]:
            ctx["errors"].append("no JSON object in the final message")
        return res.final is not None and not ctx["errors"]


class AgyAdapter(ReadOnlyAdapter):
    """Antigravity CLI print mode in plan mode, which refused every file write when probed on
    agy 1.4.5. Its browser tools cannot be switched off from the command line."""

    name, provider = "agy-cli", "google"

    @property
    def binary(self) -> str:
        return self.cfg.agy_bin

    def argv(self, req: RunRequest) -> list[str]:
        # `-p=` with --input-format stream-json: the prompt comes from stdin, not argv.
        a = [self.binary, "--input-format", "stream-json", "--output-format", "stream-json",
             "-p=", "--mode", "plan", "--sandbox", "--disable-slash-commands",
             "--json-schema", json.dumps(req.schema)]
        if req.model:
            a += ["--model", req.model]
        if req.session_id:
            a += ["--conversation", req.session_id]
        return a

    def stdin(self, req: RunRequest) -> str:
        return json.dumps({"event": "user",
                           "message": {"role": "user", "content": req.prompt}}) + "\n"

    def handle(self, ev: dict[str, Any], res: RunResult, ctx: dict[str, Any]) -> None:
        t = ev.get("event")
        if t == "init":
            res.session_id = ev.get("conversation_id")
            if ctx["on_session"] and res.session_id:
                ctx["on_session"](res.session_id)
        elif t == "result":
            r = ev.get("result") or {}
            res.session_id = r.get("conversation_id") or res.session_id
            res.usage = r.get("usage")
            res.final_text = str(r.get("response") or "")
            if isinstance(r.get("structured_output"), dict):
                res.final = r["structured_output"]
            ok = r.get("status") == "SUCCESS"
            ctx["completed"] = ok and res.final is not None
            if not ok:
                ctx["errors"].append(f"{r.get('status')}: {r.get('error') or ''}".strip())
            elif res.final is None:
                ctx["errors"].append("no structured output")

    def finish(self, res: RunResult, tmp: Path, ctx: dict[str, Any]) -> bool:
        return bool(ctx.get("completed"))


ADAPTERS: dict[str, type[Adapter]] = {"codex-cli": CodexAdapter, "claude-cli": ClaudeAdapter,
                                      "opencode-cli": OpenCodeAdapter, "agy-cli": AgyAdapter}


async def kill_group(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the whole process group (agents spawn shells), then SIGKILL after a grace period."""
    for sig, wait in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, KILL_GRACE_S)):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        try:
            await asyncio.wait_for(proc.wait(), timeout=wait)
            return
        except TimeoutError:
            continue


def kill_stale(pid: int | None, started: str | None) -> bool:
    """Kill a leftover agent process group from a dead runner. The start time recorded at launch
    must match, so a pid the OS has since reused for another process is never touched."""
    if not pid or not started or process_started(pid) != started:
        return False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGTERM)
    return True


def build_prompt(task_id: str, spec: dict[str, Any], protected: list[str]) -> str:
    lines = [
        "You are the EXECUTOR agent in a multi-agent workflow run by a bridge.",
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
        "- Do not start other agents or the bridge itself.",
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


def build_review_prompt(task_id: str, spec: dict[str, Any], attempt: dict[str, Any]) -> str:
    changes = attempt.get("changes") or {}
    return "\n".join([
        "You are the REVIEWER agent. Read-only: inspect, never modify files.",
        f"Decide whether task {task_id} is done. The bridge already ran the verification "
        "commands below; you judge the diff against the acceptance criteria.",
        "", "## Task spec", json.dumps(spec, default=str)[:8000],
        "", "## Bridge verification", json.dumps(attempt.get("verification"), default=str)[:6000],
        "", "## Executor report", json.dumps(attempt.get("final"), default=str)[:4000],
        "", "## Diff stat", str(changes.get("stat", ""))[:4000],
        "", "## Patch", str(changes.get("patch", ""))[:60000],
        "", "## Decide",
        "- approve: every acceptance criterion is met with evidence and nothing is weakened.",
        "- rework: fixable problems; notes must say file, behaviour and expected result.",
        "- block: the spec is wrong or needs a human decision.",
        "Final message: JSON matching the provided schema.",
    ])


def build_plan_prompt(objective: str, context: str, protected: list[str]) -> str:
    return "\n".join([
        "You are the PLANNER agent. Read-only: inspect the repository, never modify files.",
        "Split the objective into the smallest set of reviewable tasks for an executor agent.",
        "Each task: one reviewable change with objectively checkable completion (about 400 "
        "changed lines at most), the narrowest allowed_paths (project-relative globs), "
        "observable acceptance_criteria, and verification_commands as plain argv strings (no "
        "shell syntax). depends_on lists indices of earlier tasks in your list.",
        "risk_level high for auth, secrets, money, data migrations, deployment or publishing.",
        "Never plan changes to protected paths: " + ", ".join(protected),
        "", f"## Objective\n{objective}",
        *([f"\n## Context\n{context}"] if context else []),
        "", "Final message: JSON matching the provided schema.",
    ])
