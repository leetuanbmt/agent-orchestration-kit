"""Task actions for the web dashboard, always routed through the agent-bridge CLI."""

import json
import os
import re
import subprocess
import sys

from . import osutil, progress
from .security import redact

STARTABLE = {"execute": {"READY"}, "resume": {"REWORK", "FAILED", "PAUSED"}}
TERMINAL = {"DONE", "CANCELLED", "BLOCKED"}
DECISIONS = {"approve", "rework", "block"}
FIELDS = {
    "review": {"decision", "notes"},
    "cancel": {"reason"},
    "resume": {"feedback"},
    "execute": set(),
}
NOT_FOUND = (404, {"error": "not found"})


def _env(root: str) -> dict:
    env = dict(os.environ)
    env["BRIDGE_PROJECT_ROOT"] = root
    return env


def run_sync(argv: list[str], root: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=root, env=_env(root), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)


def start_detached(argv: list[str], root: str) -> None:
    subprocess.Popen(argv, cwd=root, env=_env(root), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **osutil.GROUP_KWARGS)


def _text_field(body: dict, name: str, limit: int, required: bool):
    value = body.get(name)
    if value is None and not required:
        return None, None
    if not isinstance(value, str) or (required and not value.strip()):
        return None, f"{name} must be a non-empty string" if required else f"{name} must be a string"
    if len(value) > limit:
        return None, f"{name} is too long"
    return value, None


def run_action(roots, pid, tid, body, runner=None, starter=None):
    runner = runner or run_sync
    starter = starter or start_detached
    if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-f]{12}", pid) \
            or not isinstance(tid, str) or not progress.TASK_ID.fullmatch(tid):
        return NOT_FOUND
    candidates = progress.discover_roots() if roots is None else list(roots)
    root = next((r for r in candidates if progress.project_id(r) == pid), None)
    if root is None:
        return NOT_FOUND
    if not isinstance(body, dict):
        return 400, {"error": "body must be an object"}
    action = body.get("action")
    if not isinstance(action, str) or action not in FIELDS:
        return 400, {"error": "unknown action"}
    if set(body) - FIELDS[action] - {"action"}:
        return 400, {"error": "unexpected fields"}
    args: list[str] = []
    if action == "review":
        decision = body.get("decision")
        if not isinstance(decision, str) or decision not in DECISIONS:
            return 400, {"error": "decision must be approve, rework or block"}
        notes, error = _text_field(body, "notes", 4000, True)
        if error:
            return 400, {"error": error}
        args = ["--decision", decision, "--notes", notes]
    elif action == "cancel":
        reason, error = _text_field(body, "reason", 500, True)
        if error:
            return 400, {"error": error}
        args = ["--reason", reason]
    elif action == "resume":
        feedback, error = _text_field(body, "feedback", 4000, False)
        if error:
            return 400, {"error": error}
        if feedback is not None:
            args = ["--feedback", feedback]
    try:
        detail = progress.task_detail(pid, tid, [root])
    except Exception:
        return 500, {"error": "internal"}
    state = detail.get("state") if detail else None
    if state is None:
        return NOT_FOUND
    if action in STARTABLE:
        allowed = state in STARTABLE[action]
    elif action == "review":
        allowed = state == "REVIEW"
    else:
        allowed = state not in TERMINAL
    if not allowed:
        return 409, {"error": f"cannot {action} a task in state {state}", "state": state}
    argv = [sys.executable, "-P", "-m", "bridge", action, tid, *args]
    if action in STARTABLE:
        try:
            starter(argv, root)
        except OSError:
            return 500, {"error": "could not start"}
        return 202, {"ok": True, "action": action, "started": True}
    try:
        done = runner(argv, root)
    except subprocess.TimeoutExpired:
        return 409, {"error": "timed out"}
    except OSError:
        return 500, {"error": "could not start"}
    if done.returncode != 0:
        return 409, {"error": redact((done.stderr or "")[-500:])}
    try:
        result = json.loads(done.stdout)
    except (TypeError, ValueError):
        result = None
    return 200, {"ok": True, "action": action, "result": result}
