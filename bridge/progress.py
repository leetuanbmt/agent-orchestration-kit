"""Read-only, language-neutral dashboard views of bridge databases."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from . import accounts, config, install, orchestrator, quota, storage
from .security import redact

TASK_ID = re.compile(r"^T\d{4,}$")
WORKFLOW_ID = re.compile(r"^W\d{4,}$")
_STAGES = {"BACKLOG": "queue", "READY": "queue", "RUNNING": "build", "REWORK": "build",
           "VERIFYING": "check", "REVIEW": "review", "APPROVED": "review", "DONE": "done",
           "CANCELLED": None}
_ORDER = ["needs", "blocked", "failed", "paused", "review_wait"]
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def project_id(root: str) -> str:
    return hashlib.sha256(root.encode()).hexdigest()[:12]


def discover_roots(here: Path | None = None) -> list[str]:
    roots: dict[str, None] = {}
    for root in dict.fromkeys([*accounts.known_projects(), *([str(here)] if here else [])]):
        if not Path(root).is_dir():
            continue
        out = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, check=False)
        roots |= dict.fromkeys([root, *(line.removeprefix("worktree ")
                                        for line in out.stdout.splitlines()
                                        if line.startswith("worktree ")
                                        and Path(line.removeprefix("worktree ")).is_dir())])
    try:
        roots |= dict.fromkeys(storage.central_stores())
    except (OSError, ValueError):
        pass
    return list(roots)


def stage_of(state: str, prev_state: str | None) -> str | None:
    if state in {"PAUSED", "BLOCKED"}:
        return _STAGES.get(prev_state, "build")
    return _STAGES.get(state, "build")


def exception_of(state: str, needs_human: bool, failure: str | None) -> str | None:
    if state == "APPROVED" and needs_human:
        return "needs"
    return {"BLOCKED": "blocked", "FAILED": "failed", "PAUSED": "paused",
            "REVIEW": "review_wait"}.get(state)


def pause_reason(failure: str | None) -> str:
    prefix = (failure or "").partition(":")[0]
    return prefix if prefix in {"QUOTA", "AUTH", "NETWORK", "INTERRUPTED", "MISSING_BINARY"} else "OTHER"


def percent(counts: dict[str, int]) -> int | None:
    total = sum(counts.values()) - counts.get("cancelled", 0)
    return 100 * counts.get("done", 0) // total if total else None


def _text(value, limit=500):
    value = str(value or "")
    text = redact(value, max(len(value), 1))
    return text[:limit] + ("…" if len(text) > limit else "")


def _json(value):
    try:
        return json.loads(value) if isinstance(value, str) else value or {}
    except (TypeError, ValueError):
        return {}


def _read(root):
    try:
        path = storage.find_store(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FileNotFoundError(root) from exc
    path = path.resolve()
    with _CACHE_LOCK:
        signature = []
        for file in (path, Path(str(path) + "-wal")):
            try:
                stat = file.stat()
                signature.append((stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                if file == path:
                    raise
                signature.append(None)
        signature = tuple(signature)
        cached = _CACHE.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "tasks" not in tables:
                raise sqlite3.DatabaseError("missing tasks")
            data = {}
            for name in ("tasks", "workflows", "attempts", "events", "account_quota"):
                columns = ("task_id, n, kind, outcome, started_at, ended_at, exit_code"
                           if name == "attempts" else "*")
                data[name] = [dict(r) for r in db.execute(f"SELECT {columns} FROM {name}")] if name in tables else []
            # Cached rows are shared; callers only build new projections.
            _CACHE[path] = (signature, data)
            return data
        finally:
            db.close()


def _latest_attempt(root, tid):
    path = storage.find_store(root).resolve()
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY n DESC LIMIT 1", (tid,)).fetchone()
        return dict(row) if row else {}
    finally:
        db.close()


def _rank(kind, reason=None):
    return (_ORDER.index(kind) if kind in _ORDER else len(_ORDER),
            {"AUTH": 0, "QUOTA": 1}.get(reason, 2) if kind == "paused" else 0)


def _command(tid: str, state: str, needs_human: bool) -> str | None:
    if state == "APPROVED" and needs_human:
        return f"agent-bridge approve {tid}"
    if state in {"REVIEW", "FAILED", "BLOCKED"}:
        return f"agent-bridge result {tid}"
    if state == "PAUSED":
        return f"agent-bridge resume {tid}"
    return None


def _tasks(data):
    result = []
    states = {row["id"]: row["state"] for row in data["tasks"]}
    transitions = {}
    for event in data["events"]:
        tid = event.get("task_id")
        if event.get("kind") != "transition" or tid not in states or event.get("to_state") != states[tid]:
            continue
        previous = transitions.get(tid)
        if previous is None or (event.get("ts", 0), event.get("id", 0)) >= (previous.get("ts", 0), previous.get("id", 0)):
            transitions[tid] = event
    for row in data["tasks"]:
        tid = row["id"]
        if not TASK_ID.fullmatch(tid):
            continue
        spec = _json(row.get("spec"))
        failure = _text(row.get("failure")) or None
        task = {"task_id": tid, "objective": _text(spec.get("objective"), 300),
                "state": row["state"], "stage": stage_of(row["state"], transitions.get(tid, {}).get("from_state")),
                "exception": exception_of(row["state"], bool(row.get("needs_human")), failure),
                "needs_human": bool(row.get("needs_human")), "failure": failure,
                "updated_at": row.get("updated_at", 0), "agent": row.get("agent"),
                "account": row.get("account"), "workflow_id": row.get("workflow_id")}
        task["command"] = _command(tid, task["state"], task["needs_human"])
        result.append(task)
    return result


def _counts(tasks):
    counts = {}
    for task in tasks:
        stage = task["stage"] or "cancelled"
        counts[stage] = counts.get(stage, 0) + 1
    return counts


def _requests(data, tasks, pid):
    groups = [(w["id"], _text(w.get("objective"), 300),
               [t for t in tasks if t["workflow_id"] == w["id"]], w.get("updated_at", 0))
              for w in data["workflows"] if WORKFLOW_ID.fullmatch(w["id"])]
    groups += [(t["task_id"], (t["objective"].splitlines() or [""])[0], [t], t["updated_at"])
               for t in tasks if not t["workflow_id"]]
    result = []
    for rid, name, members, updated in groups:
        active = [t["stage"] for t in members if t["stage"] not in {None, "done"}]
        exceptions = sorted((t for t in members if t["exception"]),
                            key=lambda t: _rank(t["exception"], pause_reason(t["failure"])))
        counts = _counts(members)
        result.append({"pid": pid, "id": rid, "name": name,
                       "stage": min(active, key=["queue", "build", "check", "review"].index)
                       if active else ("done" if counts.get("done") else None),
                       "exception": exceptions[0]["exception"] if exceptions else None,
                       "done": counts.get("done", 0),
                       "total": sum(counts.values()) - counts.get("cancelled", 0),
                       "updated_at": max([updated, *(t["updated_at"] for t in members)])})
    return result


def _timeout(root, agent):
    timeout = 1800
    try:
        cfg = config.load_config(Path(root))
        timeout = cfg.default_timeout_s
        orch = orchestrator.load(Path(root), cfg)
        if agent in orch.agents:
            timeout = orch.agents[agent].timeout_s or timeout
    except Exception:
        pass
    return timeout


def snapshot(roots: list[str] | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    result = {"generated_at": now, "projects": [], "attention": [], "running": [],
              "activity": {"done_24h": 0, "per_hour": [0] * 24, "latest": None}, "requests": [], "tasks": []}
    request_ranks = {}
    for root in dict.fromkeys(discover_roots() if roots is None else roots):
        pid = project_id(root)
        path = Path(root)
        home, shown = str(Path.home()), str(path)
        if shown == home or shown.startswith(home + "/"):
            shown = "~" + shown[len(home):]
        project = {"pid": pid, "name": path.name, "root": shown, "worktree_of": None,
                   "root_missing": not path.is_dir(), "error": None, "counts": {}, "percent": None, "badge": None,
                   "branch": None, "updated_at": None}
        try:
            data = _read(root)
        except FileNotFoundError:
            continue
        except (OSError, subprocess.CalledProcessError, sqlite3.Error, ValueError):
            project["error"] = "unreadable"
            result["projects"].append(project)
            continue
        result["projects"].append(project)
        tasks = _tasks(data)
        result["tasks"].extend({"pid": pid, **{k: t[k] for k in (
            "task_id", "objective", "state", "stage", "exception", "needs_human",
            "updated_at", "agent", "workflow_id", "command")}} for t in tasks)
        project["updated_at"] = max((t["updated_at"] for t in tasks), default=None)
        project["counts"] = _counts(tasks)
        project["percent"] = percent(project["counts"])
        trees = []
        if not project["root_missing"]:
            out = subprocess.run(["git", "-C", root, "worktree", "list", "--porcelain"],
                                 capture_output=True, text=True, check=False)
            trees = list(dict.fromkeys(line.removeprefix("worktree ") for line in out.stdout.splitlines()
                                       if line.startswith("worktree ")))
            current_tree = None
            for line in out.stdout.splitlines():
                if line.startswith("worktree "):
                    current_tree = line.removeprefix("worktree ")
                elif not line:
                    current_tree = None
                elif current_tree == root:
                    if line.startswith("branch refs/heads/"):
                        project["branch"] = line.removeprefix("branch refs/heads/")
                    elif line == "detached":
                        project["branch"] = "HEAD"
        if root in trees and trees[0] != root:
            project["worktree_of"] = project_id(trees[0])
        result["requests"].extend(_requests(data, tasks, pid))
        attention = []
        for task in tasks:
            rid = task["workflow_id"] or task["task_id"]
            rank = _rank(task["exception"], pause_reason(task["failure"]))
            request_ranks[pid, rid] = min(request_ranks.get((pid, rid), rank), rank)
            if task["exception"]:
                attention.append({"pid": pid, "task_id": task["task_id"], "kind": task["exception"],
                                  "objective": task["objective"], "since": task["updated_at"],
                                  "reason": pause_reason(task["failure"]) if task["exception"] == "paused" else None,
                                  "command": task["command"]})
            if task["state"] in {"RUNNING", "VERIFYING"}:
                attempts = [a for a in data["attempts"] if a.get("task_id") == task["task_id"]]
                latest = max(attempts, key=lambda a: a.get("n", 0), default={})
                quotas = [q for q in data["account_quota"] if q.get("name") == task["account"]]
                q = max(quotas, key=lambda q: q.get("observed_at", 0), default={})
                result["running"].append({"pid": pid, "task_id": task["task_id"], "stage": task["stage"],
                                          "agent": task["agent"], "started_at": latest.get("started_at"),
                                          "timeout_s": _timeout(root, task["agent"]),
                                          "quota_left": quota.remaining_pct(_json(q.get("rate_limits")), now)})
        attention.sort(key=lambda a: (*_rank(a["kind"], a["reason"]), a["since"]))
        result["attention"].extend(attention)
        if attention:
            first = attention[0]
            ref = next(t["workflow_id"] or t["task_id"] for t in tasks if t["task_id"] == first["task_id"])
            project["badge"] = {"kind": first["kind"], "text_key": first["kind"], "ref": ref}
        elif root in trees and len(trees) > 1:
            project["badge"] = {"kind": "worktrees", "count": len(trees) - 1}
        tasks_by_id = {task["task_id"]: task for task in tasks}
        for event in data["events"]:
            ts = event.get("ts", 0)
            if event.get("kind") != "transition" or not now - 86400 <= ts <= now:
                continue
            activity = result["activity"]
            index = int(ts // 3600 - now // 3600) + 23
            if 0 <= index < 24:
                activity["per_hour"][index] += 1
            activity["done_24h"] += event.get("to_state") == "DONE"
            if activity["latest"] is None or ts > activity["latest"]["ts"]:
                task = tasks_by_id.get(event.get("task_id"), {})
                activity["latest"] = {"task_id": event.get("task_id"), "to_state": event.get("to_state"),
                                      "kind": "paused" if event.get("to_state") == "PAUSED" else
                                      stage_of(event.get("to_state"), event.get("from_state")),
                                      "ts": ts, "reason": pause_reason(task.get("failure"))
                                      if event.get("to_state") == "PAUSED" else None}
    result["attention"].sort(key=lambda a: (*_rank(a["kind"], a["reason"]), a["since"]))
    result["requests"].sort(key=lambda r: (*request_ranks.get((r["pid"], r["id"]), _rank(None)),
                                          -r["updated_at"]))
    result["requests"] = result["requests"][:50]
    result["tasks"].sort(key=lambda t: t["updated_at"], reverse=True)
    result["tasks"] = result["tasks"][:1000]
    return result


def _detail_data(pid, roots):
    for root in discover_roots() if roots is None else roots:
        if project_id(root) == pid:
            try:
                return _read(root)
            except (OSError, subprocess.CalledProcessError, sqlite3.Error, ValueError):
                return None
    return None


def request_detail(pid: str, rid: str, roots: list[str] | None = None) -> dict | None:
    if not (TASK_ID.fullmatch(rid) or WORKFLOW_ID.fullmatch(rid)):
        return None
    data = _detail_data(pid, roots)
    if data is None:
        return None
    tasks = _tasks(data)
    request = next((r for r in _requests(data, tasks, pid) if r["id"] == rid), None)
    if request is None:
        return None
    members = [t for t in tasks if t["workflow_id"] == rid or (not t["workflow_id"] and t["task_id"] == rid)]
    exceptions = {}
    for task in members:
        if task["exception"]:
            exceptions[task["exception"]] = exceptions.get(task["exception"], 0) + 1
    return {**request, "summary": {"counts": _counts(members), "exceptions": exceptions},
            "tasks": [{k: t[k] for k in ("task_id", "objective", "stage", "exception", "updated_at")}
                      for t in members]}


def task_detail(pid: str, tid: str, roots: list[str] | None = None) -> dict | None:
    if not TASK_ID.fullmatch(tid):
        return None
    roots = list(discover_roots() if roots is None else roots)
    data = _detail_data(pid, roots)
    if data is None:
        return None
    task = next((t for t in _tasks(data) if t["task_id"] == tid), None)
    if task is None:
        return None
    row = next(r for r in data["tasks"] if r["id"] == tid)
    spec = _json(row.get("spec"))
    attempts = sorted((a for a in data["attempts"] if a.get("task_id") == tid), key=lambda a: a.get("n", 0))
    try:
        root = next(root for root in roots if project_id(root) == pid)
        latest = _latest_attempt(root, tid) if attempts else {}
    except (OSError, subprocess.CalledProcessError, sqlite3.Error, ValueError):
        return None
    verification = _json(latest.get("verification"))
    changes = _json(latest.get("changes"))
    reviews = []
    for event in data["events"]:
        if event.get("task_id") == tid and event.get("kind") == "review":
            detail = _json(event.get("detail"))
            reviews.append({"decision": detail.get("decision"), "notes": _text(detail.get("notes")),
                            "actor": event.get("actor"), "ts": event.get("ts")})
    return {**{k: task[k] for k in ("task_id", "objective", "state", "stage", "exception", "needs_human",
                                  "agent", "account", "command", "failure")},
            "risk_level": spec.get("risk_level"), "attempts_used": row.get("attempts", 0),
            "max_attempts": spec.get("max_attempts") or 3,
            "attempts": [{k: a.get(k) for k in ("n", "kind", "outcome", "started_at", "ended_at", "exit_code")}
                         for a in attempts],
            "checks": [{"command": _text(c.get("command")), "passed": c.get("passed"),
                        "error": _text(c.get("error"))} for c in verification.get("checks", [])],
            "scope_violations": [_text(v) for v in verification.get("scope_violations", [])],
            "head_changed": changes.get("head_changed", False), "files": changes.get("files", []),
            "stat": _text(changes.get("stat"), len(str(changes.get("stat") or ""))), "reviews": reviews,
            "final": _text(latest.get("final"))}
