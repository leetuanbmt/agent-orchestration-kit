"""SQLite persistence: tasks, execution attempts and an append-only audit log.

WAL + BEGIN IMMEDIATE so the MCP server and the CLI can share one database safely.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import osutil
from .models import ACTIVE, TERMINAL, Actor, State, TaskSpec, TransitionError, check_transition
from .security import process_started, redact

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
CREATE TABLE IF NOT EXISTS account_quota (name TEXT PRIMARY KEY, rate_limits TEXT NOT NULL, observed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    state TEXT NOT NULL,
    planner TEXT,
    detail TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
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
    review TEXT,
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


# Machine-wide usage ledger (in accounts.db when shared, else the local database): one row per
# run, reserved before it starts and settled with its cost afterwards.
USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    name TEXT,
    task_id TEXT,
    ts REAL NOT NULL,
    state TEXT NOT NULL,            -- reserved | settled
    reserved_usd REAL NOT NULL DEFAULT 0,
    cost_usd REAL,
    source TEXT,                    -- actual | estimated | unavailable
    owner_pid INTEGER,
    owner_started TEXT,
    gated INTEGER NOT NULL DEFAULT 0  -- 1: charged against a budget (unknown cost blocks it)
);
CREATE INDEX IF NOT EXISTS idx_account_usage_key_ts ON account_usage(key, ts);
"""
USAGE_SOURCES = {"actual", "estimated", "unavailable"}
USAGE_RETENTION = 40 * 86400  # seconds; ledger rows older than this are pruned


class ConflictError(RuntimeError):
    pass


class LimitExceeded(ValueError):
    """A budget check refused a usage reservation; the reason is the message."""


class NotFound(KeyError):
    pass


def _clean_detail(value: Any) -> Any:
    if isinstance(value, dict):
        return {redact(str(k), 8000): _clean_detail(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_detail(v) for v in value]
    if isinstance(value, str):
        return redact(value, 8000)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact(str(value), 8000)


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("spec", "final", "usage", "changes", "verification", "detail", "review"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


def overview(path: Path, state: str | None, limit: int) -> dict[str, Any]:
    """State counts and the newest tasks of a database, opened read-only: no schema change,
    no migration, safe while another process runs it."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        counts = {r[0]: r[1] for r in
                  db.execute("SELECT state, COUNT(*) FROM tasks GROUP BY state")}
        where, args = ("WHERE state=?", (state,)) if state else ("", ())
        rows = db.execute(f"SELECT * FROM tasks {where} ORDER BY updated_at DESC LIMIT ?",
                          (*args, limit)).fetchall()
    finally:
        db.close()
    return {"counts": counts, "tasks": [r for r in map(_row, rows) if r]}


class _LockedCursor(sqlite3.Cursor):
    """A cursor whose statements and fetches hold its connection's lock."""

    def __init__(self, connection: _LockedConnection):
        super().__init__(connection)
        self._lock = connection.lock

    def execute(self, *args: Any) -> _LockedCursor:
        with self._lock:
            return super().execute(*args)  # type: ignore[return-value]

    def executemany(self, *args: Any) -> _LockedCursor:
        with self._lock:
            return super().executemany(*args)  # type: ignore[return-value]

    def executescript(self, *args: Any) -> _LockedCursor:
        with self._lock:
            return super().executescript(*args)  # type: ignore[return-value]

    def fetchone(self) -> Any:
        with self._lock:
            return super().fetchone()

    def fetchall(self) -> list[Any]:
        with self._lock:
            return super().fetchall()

    def fetchmany(self, *args: Any) -> list[Any]:
        with self._lock:
            return super().fetchmany(*args)

    def __next__(self) -> Any:
        with self._lock:
            return super().__next__()


class _LockedConnection(sqlite3.Connection):
    """A connection whose cursors (Connection.execute included) hold `lock`, so statements
    from two threads never interleave on it."""

    lock: threading.RLock

    def cursor(self, factory: Any = None) -> Any:  # type: ignore[override]
        return super().cursor(factory or _LockedCursor)

    # Explicitly through the locked cursor: the C shortcuts need not call the overrides above.
    def execute(self, *args: Any) -> Any:  # type: ignore[override]
        return self.cursor().execute(*args)

    def executemany(self, *args: Any) -> Any:  # type: ignore[override]
        return self.cursor().executemany(*args)

    def executescript(self, *args: Any) -> Any:  # type: ignore[override]
        return self.cursor().executescript(*args)


def _connect(path: Path, lock: threading.RLock) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False,
                           factory=_LockedConnection)
    conn.lock = lock  # type: ignore[attr-defined]
    conn.row_factory = sqlite3.Row
    return conn


class Store:
    def __init__(self, path: Path, shared: Path | None = None):
        memory = str(path) == ":memory:"
        # One lock for every statement on this Store's connections: the TUI reads from a worker
        # thread while the event loop writes. Transactions hold it throughout (re-entrant).
        self._lock = threading.RLock()
        # Bumped on every ledger or quota write through this Store (cache invalidation).
        self.usage_revision = 0
        if not memory:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = _connect(path, self._lock)
        self.db.execute("PRAGMA journal_mode=WAL")
        if not memory:
            path.chmod(0o600)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        for suffix in (() if memory else ("-wal", "-shm")):
            try:
                Path(str(path) + suffix).chmod(0o600)
            except OSError:
                pass
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(tasks)")}
        for column in ("account", "agent", "workflow_id", "codex_started"):  # databases from older versions
            if column not in columns:
                self.db.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(workflows)")}
        for column, kind in (("runner_pid", "INTEGER"), ("runner_started", "TEXT")):
            if column not in columns:
                self.db.execute(f"ALTER TABLE workflows ADD COLUMN {column} {kind}")
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(attempts)")}
        for column in ("review",):
            if column not in columns:
                self.db.execute(f"ALTER TABLE attempts ADD COLUMN {column} TEXT")
        self.shared = None
        if shared is not None:
            shared.parent.mkdir(parents=True, exist_ok=True)
            self.shared = _connect(shared, self._lock)
            self.shared.execute("PRAGMA journal_mode=WAL")
            shared.chmod(0o600)
            self.shared.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    key TEXT PRIMARY KEY, name TEXT, state TEXT NOT NULL,
                    last_error TEXT, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS account_quota (
                    key TEXT PRIMARY KEY, name TEXT, rate_limits TEXT NOT NULL,
                    observed_at REAL NOT NULL);
            """)
            for suffix in ("-wal", "-shm"):
                try:
                    Path(str(shared) + suffix).chmod(0o600)
                except OSError:
                    pass
        self.usage_db.executescript(USAGE_SCHEMA)
        columns = {r["name"] for r in self.usage_db.execute("PRAGMA table_info(account_usage)")}
        if "gated" not in columns:  # ledgers from older versions: their rows are ungated
            self.usage_db.execute(
                "ALTER TABLE account_usage ADD COLUMN gated INTEGER NOT NULL DEFAULT 0")
        self._started = process_started(os.getpid())

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
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
             json.dumps(_clean_detail(detail)) if detail is not None else None),
        )

    def log(self, task_id: str | None, actor: Actor | str, kind: str, detail: Any = None) -> None:
        with self.tx() as c:
            self._event(c, task_id, actor, kind, detail=detail)

    def set_account_state(self, name: str, state: str, error: str | None = None, *,
                          actor: Actor | str, key: str | None = None) -> None:
        if state not in {"available", "quota", "auth"}:
            raise ValueError(f"invalid account state: {state}")
        error = redact(error, 1000) if error else None
        now = time.time()
        with self.tx() as c:
            previous = self.account_state(name)["state"]
            c.execute(
                "INSERT INTO accounts(name, state, last_error, updated_at) VALUES (?,?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET state=excluded.state,"
                " last_error=excluded.last_error, updated_at=excluded.updated_at",
                (name, state, error, now),
            )
            self._event(c, None, actor, "account_state", previous, state,
                        {"name": name, "last_error": error})

        self._share("accounts", key, name, state=state, last_error=error,
                    updated_at=now)

    def _share(self, table: str, key: str | None, name: str, **values: Any) -> None:
        if self.shared is None or key is None:
            return
        columns = ["key", "name", *values]
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        with self._lock:
            self.shared.execute("BEGIN IMMEDIATE")
            try:
                self.shared.execute(
                    f"INSERT INTO {table}({','.join(columns)})"
                    f" VALUES ({','.join('?' for _ in columns)})"
                    f" ON CONFLICT(key) DO UPDATE SET {updates}",
                    (key, name, *values.values()))
                self.shared.execute("COMMIT")
            except BaseException:
                self.shared.execute("ROLLBACK")
                raise

    def account_state(self, name: str, *, key: str | None = None) -> dict[str, Any]:
        if self.shared is not None and key is not None:
            row = self.shared.execute(
                "SELECT state, last_error, updated_at FROM accounts WHERE key=?", (key,)
            ).fetchone()
            if row is not None:
                return {"name": name, **dict(row)}
        row = self.db.execute("SELECT * FROM accounts WHERE name=?", (name,)).fetchone()
        return dict(row) if row else {"name": name, "state": "available", "last_error": None}

    def save_quota(self, name: str, rate_limits: dict[str, Any], *,
                   key: str | None = None) -> None:
        now = time.time()
        with self.tx() as c:
            c.execute("INSERT INTO account_quota(name, rate_limits, observed_at) VALUES (?,?,?)"
                      " ON CONFLICT(name) DO UPDATE SET rate_limits=excluded.rate_limits,"
                      " observed_at=excluded.observed_at",
                      (name, json.dumps(rate_limits), now))
        self.usage_revision += 1

        self._share("account_quota", key, name, rate_limits=json.dumps(rate_limits),
                    observed_at=now)

    def quota(self, name: str, *, key: str | None = None) -> dict[str, Any] | None:
        row = None
        if self.shared is not None and key is not None:
            row = self.shared.execute(
                "SELECT * FROM account_quota WHERE key=?", (key,)).fetchone()
        if row is None:
            row = self.db.execute("SELECT * FROM account_quota WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        return {"rate_limits": json.loads(row["rate_limits"]), "observed_at": row["observed_at"]}

    # ---- usage ledger ----------------------------------------------------------------------

    @property
    def usage_db(self) -> sqlite3.Connection:
        """Where the usage ledger lives: machine-wide accounts.db, else this worktree's db."""
        return self.shared if self.shared is not None else self.db

    @contextmanager
    def _usage_tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self.usage_db
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            finally:
                self.usage_revision += 1

    def _dead_owners(self, key: str) -> set[tuple[Any, Any]]:
        """Owners (pid, start time) of open reservations of `key` whose process is gone.

        Runs outside any transaction so the ps calls never hold the ledger's write lock. An
        owner is gone when its pid is unset or dead, or when ps reports a different start time
        (the pid was reused); an unknown start time (recorded or from a failed ps) proves
        nothing, so that owner counts as alive while its pid is."""
        rows = self.usage_db.execute(
            "SELECT DISTINCT owner_pid, owner_started FROM account_usage"
            " WHERE key=? AND state='reserved'", (key,)).fetchall()
        me, dead = (os.getpid(), self._started), set()
        for r in rows:
            owner = (r["owner_pid"], r["owner_started"])
            if owner == me:
                continue  # this process: still running the reservation
            pid = owner[0]
            if not pid or not osutil.alive(pid):
                dead.add(owner)
                continue
            if owner[1] is None:
                continue  # no recorded start time to compare with
            started = process_started(pid)
            if started is not None and started != owner[1]:
                dead.add(owner)
        return dead

    @staticmethod
    def _expire_usage(conn: sqlite3.Connection, key: str, dead: set[tuple[Any, Any]]) -> None:
        """Settle reservations of `key` held by the `dead` owners (crash safety, fail closed):
        charged their reserved amount as estimated, or as unknown cost when nothing was reserved.
        Only rows still reserved are touched, so a settle that won the race is kept."""
        for owner_pid, owner_started in dead:
            rows = conn.execute(
                "SELECT id, reserved_usd FROM account_usage WHERE key=? AND state='reserved'"
                " AND owner_pid IS ? AND owner_started IS ?",
                (key, owner_pid, owner_started)).fetchall()
            for r in rows:
                reserved = r["reserved_usd"] or 0.0
                conn.execute("UPDATE account_usage SET state='settled', cost_usd=?, source=?"
                             " WHERE id=? AND state='reserved'",
                             (reserved if reserved > 0 else None,
                              "estimated" if reserved > 0 else "unavailable", r["id"]))

    @staticmethod
    def _spend(conn: sqlite3.Connection, key: str,
               starts: dict[str, float]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for period, start in starts.items():
            r = conn.execute(
                "SELECT"
                " COALESCE(SUM(CASE WHEN state='settled' THEN cost_usd END), 0) AS spent,"
                " COALESCE(SUM(CASE WHEN state='reserved' THEN reserved_usd END), 0) AS reserved,"
                " COALESCE(SUM(state='settled' AND cost_usd IS NULL AND gated=1), 0) AS unknown,"
                " COALESCE(MAX(state='settled' AND source='estimated'), 0) AS estimated,"
                " COUNT(*) AS runs"
                " FROM account_usage WHERE key=? AND ts>=?", (key, start)).fetchall()[0]
            out[period] = {"spent": float(r["spent"]), "reserved": float(r["reserved"]),
                           "unknown": int(r["unknown"]), "estimated": bool(r["estimated"]),
                           "runs": int(r["runs"])}
        return out

    def reserve_usage(self, name: str, *, key: str, task_id: str | None, reserve: float,
                      starts: dict[str, float],
                      check: Callable[[dict[str, dict[str, Any]]], str | None]) -> int:
        """Atomically check the budget and reserve `reserve` USD for one run.
        Raises LimitExceeded (nothing inserted) when check(spend) returns a reason."""
        dead = self._dead_owners(key)  # ps runs before the write lock is taken
        now = time.time()
        with self._usage_tx() as c:
            c.execute("DELETE FROM account_usage WHERE ts<?", (now - USAGE_RETENTION,))
            self._expire_usage(c, key, dead)
            reason = check(self._spend(c, key, starts))
            if reason:
                raise LimitExceeded(reason)
            cur = c.execute(
                "INSERT INTO account_usage(key, name, task_id, ts, state, reserved_usd,"
                " owner_pid, owner_started, gated) VALUES (?,?,?,?,'reserved',?,?,?,1)",
                (key, name, task_id, now, float(reserve), os.getpid(), self._started))
            return int(cur.lastrowid)

    def settle_usage(self, usage_id: int, cost_usd: float | None, source: str) -> None:
        """Record a reserved run's cost; a second settle of the same row does nothing."""
        if source not in USAGE_SOURCES:
            raise ValueError(f"invalid usage source: {source}")
        with self._usage_tx() as c:
            c.execute("UPDATE account_usage SET state='settled', cost_usd=?, source=?"
                      " WHERE id=? AND state='reserved'", (cost_usd, source, usage_id))

    def record_usage(self, name: str, *, key: str, task_id: str | None,
                     cost_usd: float | None, source: str, gated: bool = False) -> int:
        """Record a run that had no reservation, already settled: for reporting (no budget), or
        charged against a budget when `gated` (an unknown cost then blocks that budget)."""
        if source not in USAGE_SOURCES:
            raise ValueError(f"invalid usage source: {source}")
        with self._usage_tx() as c:
            cur = c.execute(
                "INSERT INTO account_usage(key, name, task_id, ts, state, reserved_usd, cost_usd,"
                " source, owner_pid, owner_started, gated)"
                " VALUES (?,?,?,?,'settled',0,?,?,?,?,?)",
                (key, name, task_id, time.time(), cost_usd, source, os.getpid(), self._started,
                 int(gated)))
            return int(cur.lastrowid)

    def usage_spend(self, key: str, starts: dict[str, float], *,
                    expire: bool = True) -> dict[str, dict[str, Any]]:
        """Spend per period: a plain read unless a dead owner's reservation needs settling.
        With expire=False it never writes (and runs no ps): dead owners' rows stay reserved."""
        if expire and (dead := self._dead_owners(key)):
            with self._usage_tx() as c:
                self._expire_usage(c, key, dead)
        return self._spend(self.usage_db, key, starts)

    def active_on_account(self, name: str) -> int:
        q = (f"SELECT COUNT(*) FROM tasks WHERE COALESCE(account, 'default')=? "
             f"AND state IN ({','.join('?' * len(ACTIVE))})")
        return int(self.db.execute(q, (name, *ACTIVE)).fetchone()[0])

    # ---- workflows -------------------------------------------------------------------------

    def create_workflow(self, objective: str, planner: str | None, state: str) -> dict[str, Any]:
        with self.tx() as c:
            n = c.execute("SELECT COUNT(*) FROM workflows").fetchone()[0] + 1
            wid, now = f"W{n:04d}", time.time()
            c.execute("INSERT INTO workflows(id, objective, state, planner, runner_pid,"
                      " runner_started, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      (wid, objective, state, planner, os.getpid(), self._started, now, now))
            self._event(c, None, Actor.CLIENT, "workflow_created", None, state,
                        {"workflow_id": wid, "planner": planner})
        return self.workflow(wid)

    def workflow(self, wid: str) -> dict[str, Any]:
        r = _row(self.db.execute("SELECT * FROM workflows WHERE id=?", (wid,)).fetchone())
        if r is None:
            raise NotFound(f"unknown workflow: {wid}")
        return r

    def set_workflow(self, wid: str, state: str, detail: Any = None) -> None:
        with self.tx() as c:
            prev = c.execute("SELECT state FROM workflows WHERE id=?", (wid,)).fetchone()
            # The process planning or driving a workflow owns it until it leaves those states.
            owned = state in {"PLANNING", "RUNNING"}
            c.execute("UPDATE workflows SET state=?, detail=?, runner_pid=?, runner_started=?,"
                      " updated_at=? WHERE id=?",
                      (state, json.dumps(detail, default=str) if detail is not None else None,
                       os.getpid() if owned else None, self._started if owned else None,
                       time.time(), wid))
            if prev and prev["state"] != state:
                self._event(c, None, Actor.BRIDGE, "workflow_state", prev["state"], state,
                            {"workflow_id": wid, "detail": detail})

    def workflows_in(self, *states: str) -> list[dict[str, Any]]:
        rows = self.db.execute(f"SELECT * FROM workflows WHERE state IN "
                               f"({','.join('?' * len(states))})", states)
        return [r for r in map(_row, rows.fetchall()) if r]

    def workflow_tasks(self, wid: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM tasks WHERE workflow_id=? ORDER BY id", (wid,))
        return [r for r in map(_row, rows.fetchall()) if r]

    # ---- tasks -----------------------------------------------------------------------------

    def create(self, spec: TaskSpec, spec_hash: str, agent: str | None = None,
               workflow_id: str | None = None, account: str | None = None) -> tuple[dict[str, Any], bool]:
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
                "INSERT INTO tasks(id, spec, spec_hash, state, needs_human, created_at,"
                " updated_at, account, agent, workflow_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tid, spec.model_dump_json(), spec_hash, state, int(spec.risk_level == "high"),
                 now, now, account or spec.account, agent, workflow_id),
            )
            self._event(c, tid, Actor.CLIENT, "created", None, state, spec.model_dump())
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
                   expect: set[State] | None = None, account_limit: int = 1,
                   *, attempt_review: dict | None = None,
                   extra_event: tuple[str, Any] | None = None,
                   **fields: Any) -> dict[str, Any]:
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
                account = row["account"] or "default"
                if sum((b["account"] or "default") == account for b in active) >= account_limit:
                    raise ConflictError(f"account {account} is already running "
                                        f"{account_limit} task(s)")
                for busy in active:
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
            if attempt_review is not None:
                c.execute(
                    "UPDATE attempts SET review=? WHERE task_id=? AND n="
                    "(SELECT MAX(n) FROM attempts WHERE task_id=?)",
                    (json.dumps(_clean_detail(attempt_review)), tid, tid),
                )
                self._event(c, tid, actor, "review", detail=attempt_review)
            if extra_event is not None:
                self._event(c, tid, actor, extra_event[0], detail=extra_event[1])
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

    def count_events(self, tid: str, kind: str) -> int:
        """How many events of `kind` task `tid` has, over its whole history."""
        return int(self.db.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind=?",
                                   (tid, kind)).fetchone()[0])
