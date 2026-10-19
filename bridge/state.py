"""SQLite persistence: tasks, execution attempts and an append-only audit log.

WAL + BEGIN IMMEDIATE so the MCP server and the CLI can share one database safely.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import ACTIVE, TERMINAL, Actor, State, TaskSpec, TransitionError, check_transition
from .security import redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    spec TEXT NOT NULL,
    account TEXT,
    spec_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    session_id TEXT,
    runner_pid INTEGER,
    codex_pid INTEGER,
    failure TEXT,
    needs_human INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, state TEXT NOT NULL, last_error TEXT, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    n INTEGER NOT NULL,
    kind TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    exit_code INTEGER,
    outcome TEXT,
    session_id TEXT,
    final TEXT,
    usage TEXT,
    stderr_tail TEXT,
    changes TEXT,
    verification TEXT,
    PRIMARY KEY (task_id, n)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    task_id TEXT,
    actor TEXT NOT NULL,
    kind TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
"""


class ConflictError(RuntimeError):
    pass


class NotFound(KeyError):
    pass


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("spec", "final", "usage", "changes", "verification", "detail"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        if "account" not in {r["name"] for r in self.db.execute("PRAGMA table_info(tasks)")}:
            self.db.execute("ALTER TABLE tasks ADD COLUMN account TEXT")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _event(self, c: sqlite3.Connection, task_id: str | None, actor: Actor | str, kind: str,
               src: str | None = None, dst: str | None = None, detail: Any = None) -> None:
        c.execute(
            "INSERT INTO events(ts, task_id, actor, kind, from_state, to_state, detail)"
            " VALUES (?,?,?,?,?,?,?)",
            (time.time(), task_id, str(actor), kind, src, dst,
             redact(json.dumps(detail, default=str), 8000) if detail is not None else None),
        )

    def log(self, task_id: str | None, actor: Actor | str, kind: str, detail: Any = None) -> None:
        with self.tx() as c:
            self._event(c, task_id, actor, kind, detail=detail)

    def set_account_state(self, name: str, state: str, error: str | None = None, *,
                          actor: Actor | str) -> None:
        if state not in {"available", "quota", "auth"}:
            raise ValueError(f"invalid account state: {state}")
        error = redact(error, 1000) if error else None
        with self.tx() as c:
            previous = self.account_state(name)["state"]
            c.execute(
                "INSERT INTO accounts(name, state, last_error, updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET state=excluded.state,"
                " last_error=excluded.last_error, updated_at=excluded.updated_at",
                (name, state, error, time.time()),
            )
            self._event(c, None, actor, "account_state", previous, state,
                        {"name": name, "last_error": error})

    def account_state(self, name: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()
        return dict(row) if row else {"name": name, "state": "available", "last_error": None}

    # ---- tasks -----------------------------------------------------------------------------

    def create(self, spec: TaskSpec, spec_hash: str) -> tuple[dict[str, Any], bool]:
        """Insert a task, or return the live task with the same spec (duplicate submission)."""
        with self.tx() as c:
            dup = c.execute(
                "SELECT * FROM tasks WHERE spec_hash=? AND state NOT IN (?,?,?)",
                (spec_hash, *TERMINAL),
            ).fetchone()
            if dup:
                return _row(dup) or {}, True
            for dep in spec.depends_on:
                if not c.execute("SELECT 1 FROM tasks WHERE id=?", (dep,)).fetchone():
                    raise NotFound(f"unknown dependency: {dep}")
            n = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] + 1
            tid = f"T{n:04d}"
            state = State.READY if self._deps_done(c, spec.depends_on) else State.BACKLOG
            now = time.time()
            c.execute(
                "INSERT INTO tasks(id, spec, spec_hash, state, needs_human, created_at, updated_at, account)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (tid, spec.model_dump_json(), spec_hash, state, int(spec.risk_level == "high"),
                 now, now, spec.account),
            )
            self._event(c, tid, Actor.CLAUDE, "created", None, state, spec.model_dump())
            return _row(c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()) or {}, False

    @staticmethod
    def _deps_done(c: sqlite3.Connection, deps: list[str]) -> bool:
        if not deps:
            return True
        q = f"SELECT COUNT(*) FROM tasks WHERE state=? AND id IN ({','.join('?' * len(deps))})"
        return bool(c.execute(q, (State.DONE, *deps)).fetchone()[0] == len(set(deps)))

    def get(self, tid: str) -> dict[str, Any]:
        r = _row(self.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
        if r is None:
            raise NotFound(f"unknown task: {tid}")
        return r

    def list(self, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM tasks", ()
        if state:
            q, args = q + " WHERE state=?", (state,)
        rows = self.db.execute(q + " ORDER BY created_at DESC LIMIT ?", (*args, limit)).fetchall()
        return [r for r in map(_row, rows) if r]

    def transition(self, tid: str, dst: State, actor: Actor, detail: Any = None,
                   expect: set[State] | None = None, **fields: Any) -> dict[str, Any]:
        """Validated state change + audit event in one transaction."""
        with self.tx() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if row is None:
                raise NotFound(f"unknown task: {tid}")
            src = State(row["state"])
            if expect is not None and src not in expect:
                raise TransitionError(f"task {tid} is {src}, expected one of {sorted(expect)}")
            check_transition(src, dst, actor)
            if dst == State.RUNNING:
                active = c.execute(
                    f"SELECT id, account, spec FROM tasks WHERE id<>? "
                    f"AND state IN ({','.join('?' * len(ACTIVE))})",
                    (tid, *ACTIVE),
                ).fetchall()
                for busy in active:
                    if (busy["account"] or "default") == (row["account"] or "default"):
                        raise ConflictError(f"account {row['account'] or 'default'} is already running")
                    if ((json.loads(row["spec"]).get("sandbox") or "workspace-write")
                            == "workspace-write" and
                            (json.loads(busy["spec"]).get("sandbox") or "workspace-write")
                            == "workspace-write"):
                        # ponytail: parallel writers need separate worktrees.
                        raise ConflictError(f"task {busy['id']} is already writing in this worktree")
            sets = {"state": dst, "updated_at": time.time(), **fields}
            c.execute(
                f"UPDATE tasks SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                (*sets.values(), tid),
            )
            self._event(c, tid, actor, "transition", src, dst, detail)
            if dst == State.DONE:
                self._promote_dependents(c)
            return _row(c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()) or {}

    def _promote_dependents(self, c: sqlite3.Connection) -> None:
        for r in c.execute("SELECT id, spec FROM tasks WHERE state=?", (State.BACKLOG,)).fetchall():
            if self._deps_done(c, json.loads(r["spec"])["depends_on"]):
                c.execute("UPDATE tasks SET state=?, updated_at=? WHERE id=?",
                          (State.READY, time.time(), r["id"]))
                self._event(c, r["id"], Actor.BRIDGE, "transition", State.BACKLOG, State.READY,
                            {"reason": "dependencies done"})

    def update(self, tid: str, **fields: Any) -> None:
        with self.tx() as c:
            sets = {"updated_at": time.time(), **fields}
            c.execute(f"UPDATE tasks SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                      (*sets.values(), tid))

    # ---- attempts / events -----------------------------------------------------------------

    def start_attempt(self, tid: str, n: int, kind: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO attempts(task_id, n, kind, started_at) VALUES (?,?,?,?)",
                      (tid, n, kind, time.time()))
            self._event(c, tid, Actor.BRIDGE, "attempt_started", detail={"n": n, "kind": kind})

    def finish_attempt(self, tid: str, n: int, **fields: Any) -> None:
        enc = {k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v
               for k, v in fields.items()}
        with self.tx() as c:
            c.execute(
                f"UPDATE attempts SET ended_at=?, {', '.join(f'{k}=?' for k in enc)}"
                " WHERE task_id=? AND n=?",
                (time.time(), *enc.values(), tid, n),
            )
            self._event(c, tid, Actor.BRIDGE, "attempt_finished",
                        detail={"n": n, "outcome": fields.get("outcome"),
                                "exit_code": fields.get("exit_code")})

    def attempts(self, tid: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY n", (tid,))
        return [r for r in map(_row, rows.fetchall()) if r]

    def events(self, tid: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT ?", (tid, limit)
        ).fetchall()
        return [r for r in map(_row, reversed(rows)) if r]
