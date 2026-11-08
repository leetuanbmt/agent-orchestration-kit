import hashlib
import json
import os
import sqlite3
import subprocess
import time
from types import SimpleNamespace

import pytest

from bridge import progress, registry
from bridge.models import Actor, State, TaskSpec
from bridge.security import process_started
from bridge.state import Store


# @pt.id T0120-progress-runner-dead
# @pt.item Read-only runner liveness
# @pt.condition Active tasks have dead, missing, or current-process runner PIDs
# @pt.expected Dead runners need resume while state and stage stay unchanged
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy none
@pytest.mark.parametrize("state,stage", [("RUNNING", "build"), ("VERIFYING", "check")])
def test_snapshot_dead_runner(repo, store, state, stage):
    dead = add(store, state)
    missing = add(store, state)
    owned = add(store, state)
    store.update(dead, runner_pid=2_147_483_647, runner_started="dead runner")
    store.update(owned, runner_pid=os.getpid(), runner_started="different start")
    before = store.db.total_changes
    rows = store.list()
    snap = progress.snapshot([str(repo)], now=1000)
    attention = {task["task_id"]: task for task in snap["attention"]}
    for tid in (dead, missing):
        assert attention[tid]["kind"] == "runner_dead"
        assert attention[tid]["command"] == f"agent-bridge resume {tid}"
        detail = progress.task_detail(progress.project_id(str(repo)), tid, [str(repo)])
        assert (detail["state"], detail["stage"]) == (state, stage)
    assert owned not in attention
    assert {task["task_id"] for task in snap["running"]} == {dead, missing, owned}
    assert store.list() == rows
    assert store.db.total_changes == before


# @pt.id PERF-01-topology-cache
# @pt.item Dashboard requests reuse git topology but never cached liveness
# @pt.condition two snapshots and a task detail within the TTL; the runner dies between them; then the TTL passes
# @pt.expected no git subprocess after the first request, the dead runner still shows at once, and git runs again after the TTL
# @pt.target bridge.progress.snapshot, task_detail
# @pt.method unit
# @pt.legacy audit PERF-01/02: three git calls per project on every request
def test_topology_cached_liveness_not(repo, store, monkeypatch):
    registry.remember_project(repo)
    tid = add(store, "RUNNING")
    store.update(tid, runner_pid=os.getpid(), runner_started=process_started(os.getpid()))
    calls = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or real(*a, **k))
    clock = [100.0]
    monkeypatch.setattr(progress.time, "monotonic", lambda: clock[0])
    pid = progress.project_id(str(repo))
    progress.snapshot()
    first = len(calls)
    assert first > 0
    store.update(tid, runner_pid=2_147_483_647, runner_started="dead runner")
    snap = progress.snapshot()
    assert progress.task_detail(pid, tid) is not None
    assert len(calls) == first  # topology came from the cache
    assert {t["task_id"]: t["kind"] for t in snap["attention"]}[tid] == "runner_dead"
    clock[0] += progress._TOPOLOGY_TTL + 1
    progress.snapshot()
    assert len(calls) > first


# @pt.id T0048-log-cursor
# @pt.item Safe attempt entries and byte cursors
# @pt.condition Codex, Claude, stderr, reasoning and a partial record
# @pt.expected Safe summaries and complete-line cursor resumption
# @pt.target bridge.progress.task_log
# @pt.method unit
# @pt.legacy none
def test_task_log_entries_and_cursor(repo, store):
    tid = add(store)
    store.start_attempt(tid, 1, "execute")
    path = repo / f".git/agent-bridge/logs/{tid}-1.jsonl"
    path.parent.mkdir()
    events = [
        ("stdout", json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Codex"}})),
        ("stdout", json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Claude"}]}})),
        ("stdout", json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "private"}})),
        ("stderr", "warning"), ("stdout", "plain output")]
    complete = ''.join(json.dumps({"ts": 1, "stream": stream, "line": line}) + '\n' for stream, line in events)
    partial = json.dumps({"ts": 2, "stream": "stdout", "line": "next"})
    path.write_text(complete + partial)
    pid, roots = progress.project_id(str(repo)), [str(repo)]
    result = progress.task_log(pid, tid, 1, roots=roots)
    assert [e["text"] for e in result["entries"]] == ["agent: Codex", "agent: Claude", "warning", "plain output"]
    assert [e["kind"] for e in result["entries"]] == ["event", "event", "stderr", "stdout"]
    assert all("line" not in entry for entry in result["entries"])
    assert result["offset"] == len(complete.encode())
    assert progress.task_log(pid, tid, 1, result["offset"], roots)["entries"] == []
    with path.open('a') as log:
        log.write('\n')
    resumed = progress.task_log(pid, tid, 1, result["offset"], roots)
    assert [e["text"] for e in resumed["entries"]] == ["next"]
    assert resumed["offset"] == path.stat().st_size
    for task, attempt in (("T9999", 1), (tid, 2), (tid, 0), (tid, True), ("../bad", 1)):
        assert progress.task_log(pid, task, attempt, roots=roots) is None
    assert progress.task_log("000000000000", tid, 1, roots=roots) is None


# @pt.id T0048-log-tail
# @pt.item Large log pagination
# @pt.condition More than 256 KB and 500 records
# @pt.expected Initial tail is truncated and next page resumes
# @pt.target bridge.progress.task_log
# @pt.method unit
# @pt.legacy none
def test_task_log_tail_and_page(repo, store):
    tid = add(store)
    store.start_attempt(tid, 1, "execute")
    path = repo / f".git/agent-bridge/logs/{tid}-1.jsonl"
    path.parent.mkdir()
    row = json.dumps({"ts": 1, "stream": "stdout", "line": "é" * 100}, ensure_ascii=False) + '\n'
    path.write_text(row * 2000)
    pid, roots = progress.project_id(str(repo)), [str(repo)]
    result = progress.task_log(pid, tid, 1, roots=roots)
    assert result["truncated"] is True
    assert len(result["entries"]) == 500
    assert result["offset"] > path.stat().st_size - 256 * 1024
    page = progress.task_log(pid, tid, 1, result["offset"], roots)
    assert page["truncated"] is False and page["entries"]
    assert page["offset"] > result["offset"]


@pytest.fixture
def store(repo):
    value = Store(repo / ".git/agent-bridge/bridge.db")
    yield value
    value.db.close()


def add(store, state="READY", objective="Implement change", workflow=None, failure=None,
        needs=False, updated=100):
    spec = TaskSpec(objective=objective, allowed_paths=["src/**"], acceptance_criteria=["Works"])
    task, _ = store.create(spec, str(store.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
                           workflow_id=workflow)
    store.db.execute("UPDATE tasks SET state=?, failure=?, needs_human=?, updated_at=? WHERE id=?",
                     (state, failure, needs, updated, task["id"]))
    return task["id"]


# @pt.id T0022-stage
# @pt.item State mapping
# @pt.condition Every state and previous-stage fallback
# @pt.expected Exact dashboard stages
# @pt.target bridge.progress.stage_of
# @pt.method unit
# @pt.legacy None
def test_stage_mapping_covers_every_state():
    assert {s: progress.stage_of(s, None) for s in State} == {
        "BACKLOG": "queue", "READY": "queue", "RUNNING": "build", "REWORK": "build",
        "VERIFYING": "check", "REVIEW": "review", "APPROVED": "review", "DONE": "done",
        "PAUSED": "build", "FAILED": "build", "BLOCKED": "build", "CANCELLED": None}
    assert progress.stage_of("BLOCKED", "VERIFYING") == "check"
    assert progress.stage_of("PAUSED", "REVIEW") == "review"
    for previous in ("PAUSED", "BLOCKED", "FAILED", None):
        assert progress.stage_of("PAUSED", previous) == "build"


# @pt.id T0022-exceptions
# @pt.item Exception classification
# @pt.condition All states and recognized pause prefixes
# @pt.expected Exact exception and reason keys
# @pt.target bridge.progress.exception_of
# @pt.method unit
# @pt.legacy None
def test_exceptions_and_pause_reason():
    assert progress.exception_of("APPROVED", True, None) == "needs"
    assert progress.exception_of("APPROVED", False, None) is None
    for state, expected in (("REVIEW", "review_wait"), ("BLOCKED", "blocked"),
                            ("FAILED", "failed"), ("PAUSED", "paused")):
        assert progress.exception_of(state, False, None) == expected
    for state in ("BACKLOG", "READY", "RUNNING", "REWORK", "VERIFYING", "DONE", "CANCELLED"):
        assert progress.exception_of(state, True, "AUTH: error") is None
    for reason in ("QUOTA", "AUTH", "NETWORK", "INTERRUPTED", "MISSING_BINARY"):
        assert progress.pause_reason(reason + ": detail") == reason
    assert progress.pause_reason("weird") == "OTHER"
    assert progress.pause_reason(None) == "OTHER"


# @pt.id T0022-percent
# @pt.item Percentage arithmetic
# @pt.condition Fractional completion and cancellations
# @pt.expected Floor percentage excludes cancelled tasks
# @pt.target bridge.progress.percent
# @pt.method unit
# @pt.legacy None
def test_percent_rounds_down_and_ignores_cancelled():
    assert progress.percent({"done": 2, "queue": 1}) == 66
    assert progress.percent({"done": 9, "build": 1, "cancelled": 5}) == 90


# @pt.id T0022-cancelled
# @pt.item Empty denominator
# @pt.condition All tasks cancelled or no tasks
# @pt.expected No percentage
# @pt.target bridge.progress.percent
# @pt.method unit
# @pt.legacy None
def test_percent_all_cancelled_is_none():
    assert progress.percent({"cancelled": 3}) is None
    assert progress.percent({}) is None


# @pt.id T0022-attention
# @pt.item Attention priority and previous stage
# @pt.condition All exception classes including pause reasons and equal ranks
# @pt.expected Severity then oldest update with shared CLI command
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_attention_order(repo, store):
    needs = add(store, "APPROVED", needs=True)
    review = add(store, "REVIEW")
    quota = add(store, "PAUSED", failure="QUOTA: wait", updated=1)
    auth = add(store, "PAUSED", failure="AUTH: login", updated=200)
    blocked = add(store, "BLOCKED")
    failed = add(store, "FAILED")
    other = add(store, "PAUSED", failure="NETWORK: down", updated=0)
    older = add(store, "FAILED", updated=1)
    store.db.execute("INSERT INTO events(ts,task_id,actor,kind,from_state,to_state) "
                     "VALUES (50,?,'bridge','transition','VERIFYING','BLOCKED')", (blocked,))
    snap = progress.snapshot([str(repo)], now=1000)
    assert [a["task_id"] for a in snap["attention"]] == [needs, blocked, older, failed, auth, quota, other, review]
    assert snap["attention"][0]["command"] == f"agent-bridge approve {needs}"
    assert snap["projects"][0]["badge"]["ref"] == needs
    detail = progress.task_detail(progress.project_id(str(repo)), blocked, [str(repo)])
    assert detail["stage"] == "check"


# @pt.id T0028-command
# @pt.item Runnable dashboard commands
# @pt.condition Paused failure contains shell syntax and review or failed tasks exist
# @pt.expected Attention and task detail commands contain only a fixed verb and task id
# @pt.target bridge.progress.snapshot, bridge.progress.task_detail
# @pt.method unit
# @pt.legacy None
def test_command_never_contains_free_text(repo, store):
    paused = add(store, "PAUSED", failure="QUOTA: x; rm -rf ~ $(id)\nnext")
    review = add(store, "REVIEW")
    failed = add(store, "FAILED")
    assert paused == "T0001"
    expected = {paused: "agent-bridge resume T0001",
                review: f"agent-bridge result {review}",
                failed: f"agent-bridge result {failed}"}
    roots = [str(repo)]
    snap = progress.snapshot(roots, now=1000)
    assert {a["task_id"]: a["command"] for a in snap["attention"]} == expected
    for tid, command in expected.items():
        detail = progress.task_detail(progress.project_id(str(repo)), tid, roots)
        assert detail["command"] == command


# @pt.id T0022-groups
# @pt.item Workflow grouping
# @pt.condition Three workflow tasks and one lone task
# @pt.expected Two requests with correct counts and lowest active stage
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_groups_workflows_and_lone_tasks(repo, store):
    workflow = store.create_workflow("Ship feature", None, "READY")["id"]
    add(store, "DONE", workflow=workflow)
    add(store, "VERIFYING", workflow=workflow)
    add(store, "READY", workflow=workflow)
    lone = add(store, objective="Lone request\nMore detail")
    snap = progress.snapshot([str(repo)], now=1000)
    assert len(snap["requests"]) == 2
    request = next(r for r in snap["requests"] if r["id"] == workflow)
    assert (request["name"], request["done"], request["total"], request["stage"]) == ("Ship feature", 1, 3, "queue")
    assert next(r for r in snap["requests"] if r["id"] == lone)["name"] == "Lone request"
    detail = progress.request_detail(progress.project_id(str(repo)), workflow, [str(repo)])
    assert len(detail["tasks"]) == 3
    assert detail["summary"]["counts"] == {"done": 1, "check": 1, "queue": 1}


# @pt.id T0022-running
# @pt.item Running attempts and activity
# @pt.condition Frozen clock with old and recent transitions and quota
# @pt.expected Latest attempt timing, timeout fallback, and 24 hour counts
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_running_and_activity(repo, store, monkeypatch):
    now = 180000.0
    tid = add(store)
    store.transition(tid, State.RUNNING, Actor.BRIDGE)
    store.start_attempt(tid, 1, "execute")
    store.db.execute("UPDATE attempts SET started_at=?", (now - 240,))
    store.db.execute("UPDATE tasks SET account='worker',agent='worker'")
    store.save_quota("worker", {"primary": {"used_percent": 36}})
    store.db.execute("DELETE FROM events")
    for ts in (now - 60, now - 3600, now - 25 * 3600):
        store.db.execute("INSERT INTO events(ts,task_id,actor,kind,from_state,to_state) "
                         "VALUES (?,?,'bridge','transition','APPROVED','DONE')", (ts, tid))
    def unavailable(*args):
        raise ValueError("No config")
    monkeypatch.setattr(progress.config, "load_config", unavailable)
    snap = progress.snapshot([str(repo)], now=now)
    assert snap["running"] == [{"pid": progress.project_id(str(repo)), "task_id": tid,
                                "stage": "build", "agent": "worker", "started_at": now - 240,
                                "timeout_s": 1800, "quota_left": 64.0}]
    assert snap["activity"]["done_24h"] == 2
    assert len(snap["activity"]["per_hour"]) == 24
    assert sum(snap["activity"]["per_hour"]) == 2
    assert snap["activity"]["latest"]["to_state"] == "DONE"
    monkeypatch.setattr(progress.config, "load_config", lambda root: SimpleNamespace(default_timeout_s=900))
    monkeypatch.setattr(progress.orchestrator, "load", lambda root, cfg:
                        SimpleNamespace(agents={"worker": SimpleNamespace(timeout_s=None)}))
    assert progress.snapshot([str(repo)], now=now)["running"][0]["timeout_s"] == 900
    monkeypatch.setattr(progress.orchestrator, "load", lambda root, cfg:
                        SimpleNamespace(agents={"worker": SimpleNamespace(timeout_s=600)}))
    assert progress.snapshot([str(repo)], now=now)["running"][0]["timeout_s"] == 600


# @pt.id T0022-redaction
# @pt.item Free text sanitization and detail projection
# @pt.condition Secrets and long fields in tasks, reviews, and attempts
# @pt.expected Redacted bounded fields with no patch or stderr
# @pt.target bridge.progress.task_detail
# @pt.method unit
# @pt.legacy None
def test_free_text_is_redacted_and_truncated(repo, store):
    token = "sk-" + "a" * 40
    text = token + "x" * 600
    tid = add(store, "PAUSED", objective=text, failure="AUTH: " + text)
    store.start_attempt(tid, 1, "execute")
    store.db.execute("UPDATE attempts SET final=?,stderr_tail=?,changes=?,verification=?",
                     (text, "private stderr", json.dumps({"patch": "private patch", "files": ["src/app.py"],
                       "stat": "1 file", "head_changed": True}),
                      json.dumps({"checks": [{"command": "pytest", "passed": False, "error": text}],
                                  "scope_violations": ["outside scope"]})))
    store.log(tid, Actor.CLIENT, "review", {"decision": "rework", "notes": text})
    snap = progress.snapshot([str(repo)], now=1000)
    detail = progress.task_detail(progress.project_id(str(repo)), tid, [str(repo)])
    assert token not in json.dumps([snap, detail])
    assert "[REDACTED]" in detail["objective"]
    assert len(detail["objective"]) <= 301
    assert all(len(detail[k]) <= 501 for k in ("failure", "final"))
    assert len(detail["reviews"][0]["notes"]) <= 501
    assert detail["files"] == ["src/app.py"] and detail["head_changed"]
    assert detail["checks"][0]["passed"] is False
    assert "patch" not in detail and "stderr_tail" not in detail
    assert "private patch" not in json.dumps(detail) and "private stderr" not in json.dumps(detail)


# @pt.id T0022-unreadable
# @pt.item Per-project read failure
# @pt.condition Corrupt second database
# @pt.expected Good project renders and bad project reports unreadable
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_skips_unreadable_project(repo, store, tmp_path, monkeypatch):
    add(store)
    bad = tmp_path / "bad"
    directory = bad / "agent-bridge"
    directory.mkdir(parents=True)
    (directory / "bridge.db").write_text("not sqlite")
    original = progress.install.git_dir
    monkeypatch.setattr(progress.install, "git_dir", lambda root: bad if root == bad else original(root))
    snap = progress.snapshot([str(repo), str(bad)], now=1000)
    assert snap["projects"][0]["counts"] == {"queue": 1}
    assert snap["projects"][1]["error"] == "unreadable"


# @pt.id T0022-missing-database
# @pt.item Roots without bridge databases
# @pt.condition Second root has no bridge directory or database
# @pt.expected Only the initialized project is listed and no files are created
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_skips_root_without_database(repo, store, tmp_path, monkeypatch):
    add(store)
    empty = tmp_path / "empty"
    empty.mkdir()
    original = progress.install.git_dir
    monkeypatch.setattr(progress.install, "git_dir", lambda root: empty if root == empty else original(root))
    roots = [str(repo), str(empty)]
    snap = progress.snapshot(roots, now=1000)
    assert [p["pid"] for p in snap["projects"]] == [progress.project_id(str(repo))]
    assert snap["projects"][0]["error"] is None
    assert snap["projects"][0]["counts"] == {"queue": 1}
    assert progress.task_detail(progress.project_id(str(empty)), "T0001", roots) is None
    assert progress.request_detail(progress.project_id(str(empty)), "W0001", roots) is None
    assert list(empty.iterdir()) == []


# @pt.id T0026-not-repository
# @pt.item Roots that are not git repositories
# @pt.condition Existing directory without a git repository and a missing root
# @pt.expected Only the initialized project is listed without errors
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_skips_root_that_is_not_a_repository(repo, store, tmp_path, monkeypatch):
    add(store)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    empty = tmp_path / "not-a-repository"
    empty.mkdir()
    roots = [str(repo), str(empty), str(tmp_path / "missing")]
    snap = progress.snapshot(roots, now=1000)
    assert [p["pid"] for p in snap["projects"]] == [progress.project_id(str(repo))]
    assert snap["projects"][0]["error"] is None
    assert snap["projects"][0]["counts"] == {"queue": 1}


# @pt.id T0022-legacy
# @pt.item Legacy database compatibility
# @pt.condition No optional columns or auxiliary tables
# @pt.expected Lone task request with nullable agent and account
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_old_schema_without_workflow_column(tmp_path, monkeypatch):
    directory = tmp_path / "agent-bridge"
    directory.mkdir()
    with sqlite3.connect(directory / "bridge.db") as db:
        db.execute("CREATE TABLE tasks(id TEXT, spec TEXT, state TEXT, updated_at REAL)")
        db.execute("INSERT INTO tasks VALUES ('T0001',?, 'RUNNING',100)",
                   (json.dumps({"objective": "Legacy task"}),))
    monkeypatch.setattr(progress.install, "git_dir", lambda root: tmp_path)
    snap = progress.snapshot([str(tmp_path)], now=1000)
    assert snap["projects"][0]["error"] is None
    assert snap["requests"][0]["id"] == "T0001"
    assert snap["running"][0]["agent"] is None
    detail = progress.task_detail(progress.project_id(str(tmp_path)), "T0001", [str(tmp_path)])
    assert detail["attempts"] == [] and detail["account"] is None


# @pt.id T0022-readonly
# @pt.item Database immutability
# @pt.condition Snapshot and details while WAL connection is open
# @pt.expected Identical database and WAL hashes and mtimes; mode ro connection
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_does_not_modify_databases(repo, store, monkeypatch):
    tid = add(store)
    files = [p for p in store.path.parent.iterdir() if p.name in {"bridge.db", "bridge.db-wal"}]
    def signature():
        return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in files}
    before = signature()
    connect = sqlite3.connect
    calls = []
    def readonly(path, **kwargs):
        calls.append(path)
        assert path.startswith("file:") and path.endswith("?mode=ro") and kwargs["uri"] is True
        return connect(path, **kwargs)
    monkeypatch.setattr(progress.sqlite3, "connect", readonly)
    roots = [str(repo)]
    progress.snapshot(roots, now=1000)
    progress.task_detail(progress.project_id(str(repo)), tid, roots)
    progress.request_detail(progress.project_id(str(repo)), tid, roots)
    assert len(calls) == 1
    assert signature() == before


# @pt.id T0022-ids
# @pt.item Detail identifier validation
# @pt.condition Invalid IDs and unknown project/task/request
# @pt.expected None for invalid lookup and populated known details
# @pt.target bridge.progress.request_detail
# @pt.method unit
# @pt.legacy None
def test_details_validate_ids(repo, store):
    workflow = store.create_workflow("Feature", None, "READY")["id"]
    tid = add(store, workflow=workflow)
    pid, roots = progress.project_id(str(repo)), [str(repo)]
    for invalid in ("../etc", "T-0012", "T0001\n", "W0001", "T001"):
        assert progress.task_detail(pid, invalid, roots) is None
    for invalid in ("../etc", "W-0012", "W0001\n", "W001"):
        assert progress.request_detail(pid, invalid, roots) is None
    assert progress.task_detail("0" * 12, tid, roots) is None
    assert progress.request_detail("0" * 12, workflow, roots) is None
    assert progress.task_detail(pid, "T9999", roots) is None
    assert progress.request_detail(pid, "W9999", roots) is None
    assert progress.request_detail(pid, workflow, roots)["tasks"]
    assert progress.task_detail(pid, tid, roots)["max_attempts"] == 3


# @pt.id T0022-worktrees
# @pt.item Shared root discovery and project badges
# @pt.condition Registered root with a linked worktree and explicit-root snapshot
# @pt.expected Stable deduplicated roots, worktree count, and parent project identifier
# @pt.target bridge.progress.discover_roots
# @pt.method unit
# @pt.legacy None
def test_discover_roots_and_worktree_badge(repo, store, monkeypatch):
    tree = repo.parent / "feature"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tree)],
                   check=True, capture_output=True)
    monkeypatch.setattr(progress.accounts, "known_projects", lambda: [str(repo), str(repo)])
    assert progress.discover_roots(repo) == [str(repo), str(tree)]
    snap = progress.snapshot([str(repo)], now=1000)
    assert snap["projects"][0]["badge"] == {"kind": "worktrees", "count": 1}
    gdir = progress.install.git_dir(tree)
    other = Store(gdir / "agent-bridge/bridge.db")
    try:
        snap = progress.snapshot([str(tree)], now=1000)
        assert snap["projects"][0]["worktree_of"] == progress.project_id(str(repo))
    finally:
        other.db.close()


# @pt.id T0026-missing-directories
# @pt.item Root discovery directory filtering
# @pt.condition Missing known project and deleted worktree alongside existing roots
# @pt.expected Existing roots retain order and deduplication; git runs only for existing roots
# @pt.target bridge.progress.discover_roots
# @pt.method unit
# @pt.legacy None
def test_discover_roots_drops_missing_directories(tmp_path, monkeypatch):
    root = tmp_path / "project"
    tree = tmp_path / "worktree"
    here = tmp_path / "here"
    for directory in (root, tree, here):
        directory.mkdir()
    missing = tmp_path / "missing-project"
    deleted = tmp_path / "deleted-worktree"
    monkeypatch.setattr(progress.accounts, "known_projects",
                        lambda: [str(missing), str(root), str(root)])
    calls = []

    def worktrees(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=f"worktree {root}\n\nworktree {deleted}\n\nworktree {tree}\n")

    monkeypatch.setattr(progress.subprocess, "run", worktrees)
    assert progress.discover_roots(here) == [str(root), str(tree), str(here)]
    assert calls == [["git", "-C", str(directory), "worktree", "list", "--porcelain"]
                     for directory in (root, here)]
    assert progress.discover_roots(missing) == [str(root), str(tree)]


# @pt.id T0022-request-limit
# @pt.item Request limit and ordering
# @pt.condition More than fifty requests with differing pause reasons and updates
# @pt.expected Fifty rows with AUTH before QUOTA before recent normal requests
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_requests_limit_and_severity(repo, store):
    for index in range(52):
        add(store, updated=index)
    quota = add(store, "PAUSED", failure="QUOTA: wait", updated=999)
    auth = add(store, "PAUSED", failure="AUTH: login", updated=0)
    snap = progress.snapshot([str(repo)], now=1000)
    assert len(snap["requests"]) == 50
    assert [r["id"] for r in snap["requests"][:2]] == [auth, quota]
    assert snap["requests"][2]["updated_at"] == 51


# @pt.id T0032-deleted
# @pt.item Retained migrated history
# @pt.condition Repository removed after migration
# @pt.expected Snapshot details and CLI projects retain tasks with root_missing
# @pt.target bridge.progress, bridge.service.project_tasks
# @pt.method integration
# @pt.legacy agent-bridge
def test_deleted_migrated_root(repo, store, monkeypatch, tmp_path):
    from bridge import accounts, storage
    from bridge.service import project_tasks
    tid = add(store)
    store.db.commit()
    monkeypatch.setattr(storage, "running_processes", lambda: [])
    storage.migrate(repo, apply=True)
    store.db.close()
    repo.rename(tmp_path / "removed")
    monkeypatch.setattr(accounts, "known_projects", lambda: [])
    snapshot = progress.snapshot()
    project = next(p for p in snapshot["projects"] if p["pid"] == progress.project_id(str(repo)))
    assert project["root_missing"] is True
    assert project["counts"]["queue"] == 1
    assert progress.task_detail(project["pid"], tid)["objective"] == "Implement change"
    listed = project_tasks()["projects"]
    assert listed[0]["root_missing"] is True
    assert listed[0]["tasks"][0]["task_id"] == tid


# @pt.id T0032-invalid-home
# @pt.item Invalid home read errors
# @pt.condition Home override is relative
# @pt.expected Snapshot reports unreadable and CLI skips without raising
# @pt.target bridge.progress.snapshot, bridge.service.project_tasks
# @pt.method unit
# @pt.legacy None
def test_invalid_home_readers(repo, store, monkeypatch):
    from bridge import storage
    from bridge.service import project_tasks
    monkeypatch.setenv(storage.HOME_ENV, "relative")
    assert progress.snapshot([str(repo)])["projects"][0]["error"] == "unreadable"
    assert project_tasks(here=repo)["projects"] == []


# @pt.id T0034-cache
# @pt.item Cached overview reads
# @pt.condition Unchanged database followed by a Store write
# @pt.expected No second connection and refreshed task counts after write
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_cache_invalidates_on_write(repo, store, monkeypatch):
    add(store)
    connect = progress.sqlite3.connect
    calls = []
    def counted(*args, **kwargs):
        calls.append(args[0])
        return connect(*args, **kwargs)
    monkeypatch.setattr(progress.sqlite3, "connect", counted)
    roots = [str(repo)]
    first = progress.snapshot(roots, now=1000)
    assert len(calls) == 1
    assert progress.snapshot(roots, now=1000) == first
    assert len(calls) == 1
    add(store, "DONE")
    assert progress.snapshot(roots, now=1000)["projects"][0]["counts"] == {"queue": 1, "done": 1}
    assert len(calls) == 2


# @pt.id T0034-latest-attempt
# @pt.item Full latest attempt detail
# @pt.condition Two attempts with different heavy columns
# @pt.expected Latest checks changes and final survive overview projection
# @pt.target bridge.progress.task_detail
# @pt.method unit
# @pt.legacy None
def test_latest_attempt_detail_with_light_overview(repo, store):
    tid = add(store)
    for n in (1, 2):
        store.start_attempt(tid, n, "execute")
        store.db.execute("UPDATE attempts SET verification=?, changes=?, final=? WHERE task_id=? AND n=?",
                         (json.dumps({"checks": [{"command": f"check {n}", "passed": True}]}),
                          json.dumps({"head_changed": True, "files": [f"src/{n}.py"], "stat": f"{n} files"}),
                          f"final {n}", tid, n))
    roots = [str(repo)]
    progress.snapshot(roots)
    assert set(progress._read(str(repo))["attempts"][0]) == {
        "task_id", "n", "kind", "outcome", "started_at", "ended_at", "exit_code"}
    detail = progress.task_detail(progress.project_id(str(repo)), tid, roots)
    assert detail["checks"] == [{"command": "check 2", "passed": True, "error": ""}]
    assert detail["files"] == ["src/2.py"]
    assert detail["stat"] == "2 files"
    assert detail["head_changed"] is True
    assert detail["final"] == "final 2"


# @pt.id T0034-performance
# @pt.item First overview load cost
# @pt.condition Database containing 1000 tasks and 20000 transitions
# @pt.expected Snapshot finishes within two seconds
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_large_snapshot_first_load(repo, store):
    tid = add(store, "PAUSED")
    template = dict(store.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
    columns = list(template)
    rows = []
    for index in range(2, 1001):
        row = dict(template, id=f"T{index:04d}")
        rows.append(tuple(row[column] for column in columns))
    store.db.executemany(
        f"INSERT INTO tasks ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", rows)
    store.db.execute("DELETE FROM events")
    store.db.executemany(
        "INSERT INTO events(ts,task_id,actor,kind,from_state,to_state) "
        "VALUES (?,?,'bridge','transition',?,'PAUSED')",
        [(index, f"T{index % 1000 + 1:04d}", "REVIEW" if index >= 19000 else "RUNNING")
         for index in range(20000)])
    started = time.perf_counter()
    snap = progress.snapshot([str(repo)], now=20000)
    elapsed = time.perf_counter() - started
    assert snap["projects"][0]["counts"] == {"review": 1000}
    assert elapsed < 2


# @pt.id T0045-tasks
# @pt.item Flat task projection and project metadata
# @pt.condition Multiple tasks across main checkout and linked worktree
# @pt.expected All tasks sorted by update with exact fields and per-root metadata
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_tasks_and_worktree_metadata(repo, store):
    older = add(store, updated=100)
    newer = add(store, "FAILED", failure="private failure", updated=300)
    branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                            check=True, capture_output=True, text=True).stdout.strip()
    tree = repo.parent / "task-feature"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "task-feature", str(tree)],
                   check=True, capture_output=True)
    other = Store(progress.install.git_dir(tree) / "agent-bridge/bridge.db")
    try:
        roots = [str(repo), str(tree)]
        empty = progress.snapshot(roots)["projects"][1]
        assert empty["updated_at"] is None
        assert empty["branch"] == "task-feature"
        assert empty["worktree_of"] == progress.project_id(str(repo))
        middle = add(other, updated=200)
        snap = progress.snapshot(roots)
        assert [(t["pid"], t["task_id"], t["updated_at"]) for t in snap["tasks"]] == [
            (progress.project_id(str(repo)), newer, 300),
            (progress.project_id(str(tree)), middle, 200),
            (progress.project_id(str(repo)), older, 100)]
        expected = {"pid", "task_id", "objective", "state", "stage", "exception", "needs_human",
                    "updated_at", "agent", "account", "reason", "workflow_id", "command"}
        assert all(set(task) == expected for task in snap["tasks"])
        assert snap["projects"][0]["branch"] == branch
        assert [p["updated_at"] for p in snap["projects"]] == [300, 200]
    finally:
        other.db.close()


# @pt.id T0047-task-actions
# @pt.item Task account, pause reason and rework command
# @pt.condition Legacy account and paused or rework tasks
# @pt.expected Account retained, reason only for pause, fixed resume command
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_task_account_reason_and_rework_command(repo, store):
    paused = add(store, "PAUSED", failure="QUOTA: detail")
    rework = add(store, "REWORK")
    store.db.execute("UPDATE tasks SET account=? WHERE id=?", ("legacy", paused))
    tasks = {t["task_id"]: t for t in progress.snapshot([str(repo)])["tasks"]}
    assert tasks[paused]["account"] == "legacy"
    assert tasks[paused]["reason"] == "QUOTA"
    assert tasks[rework]["reason"] is None
    assert tasks[rework]["command"] == f"agent-bridge resume {rework}"


# @pt.id T0045-task-limit
# @pt.item Flat task limit
# @pt.condition More than one thousand tasks with distinct update timestamps
# @pt.expected Newest thousand tasks retained in descending order
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_tasks_limit(repo, store):
    tid = add(store, updated=0)
    template = dict(store.db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone())
    columns = list(template)
    rows = [dict(template, id=f"T{index + 1:04d}", updated_at=index) for index in range(1, 1002)]
    store.db.executemany(
        f"INSERT INTO tasks ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        [tuple(row[column] for column in columns) for row in rows])
    snap = progress.snapshot([str(repo)])
    assert [t["updated_at"] for t in snap["tasks"]] == list(range(1001, 1, -1))


# @pt.id T0045-detached
# @pt.item Detached branch metadata
# @pt.condition Readable repository checked out at detached HEAD
# @pt.expected Project branch is HEAD and empty update is None
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_detached_branch(repo, store):
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", "-q"],
                   check=True, capture_output=True)
    project = progress.snapshot([str(repo)])["projects"][0]
    assert project["branch"] == "HEAD"
    assert project["updated_at"] is None


ORCHESTRATOR = """
accounts:
  work: {provider: openai, home: ~/private-home-work}
  spare: {provider: openai, home: ~/private-home-spare, max_concurrency: 2}
  login: {provider: anthropic}
agents:
  lead: {adapter: client}
  coder: {adapter: codex-cli, account: work}
  backup: {adapter: codex-cli, account: spare}
  helper: {adapter: claude-cli, account: login, enabled: false}
roles:
  executor: {agents: [coder, backup]}
  reviewer: helper
routing:
  quota_fallback: {enabled: true, min_remaining_pct: 15, agents: [backup]}
"""


def configure(monkeypatch):
    orch = progress.orchestrator.parse(ORCHESTRATOR, "test.yaml")
    monkeypatch.setattr(progress.config, "load_config", lambda root: SimpleNamespace(default_timeout_s=900))
    monkeypatch.setattr(progress.orchestrator, "load", lambda root, cfg: orch)


def forbid_probes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the dashboard must not probe accounts")
    for name in ("discover", "login_status"):
        monkeypatch.setattr(progress.accounts, name, forbidden, raising=False)
    monkeypatch.setattr(progress.quota, "read_quota", forbidden, raising=False)


# @pt.id T0075-account-quota
# @pt.item Account capacity in the overview
# @pt.condition Account marked quota with 5 h and weekly windows and a running task
# @pt.expected State quota, two windows, remaining quota, running count, since and config agents
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_account_quota_capacity(repo, store, monkeypatch):
    configure(monkeypatch)
    forbid_probes(monkeypatch)
    now = time.time()
    tid = add(store)
    store.db.execute("UPDATE tasks SET state='RUNNING', account='work', agent='coder' WHERE id=?", (tid,))
    store.set_account_state("work", "quota", "QUOTA: usage limit", actor=Actor.BRIDGE)
    since = store.db.execute("SELECT updated_at FROM accounts WHERE name='work'").fetchone()[0]
    store.save_quota("work", {
        "primary": {"used_percent": 100, "window_minutes": 300, "resets_at": now + 3600},
        "secondary": {"used_percent": 40, "window_minutes": 10080, "resets_at": now + 86400}})
    snap = progress.snapshot([str(repo)], now=now)
    pid = progress.project_id(str(repo))
    work = next(a for a in snap["accounts"] if a["name"] == "work")
    assert work["state"] == "quota" and work["since"] == since
    assert work["error"] == "QUOTA: usage limit"
    assert work["windows"] == [
        {"slot": "primary", "window_minutes": 300, "used_pct": 100.0, "resets_at": now + 3600},
        {"slot": "secondary", "window_minutes": 10080, "used_pct": 40.0, "resets_at": now + 86400}]
    assert work["remaining_pct"] == 0.0 and work["observed_at"] is not None
    assert work["running"] == 1 and work["max_concurrency"] == 1
    assert work["provider"] == "openai" and work["projects"] == [pid]
    assert work["agents"] == [{"agent": "coder", "roles": ["executor"], "enabled": True}]
    spare = next(a for a in snap["accounts"] if a["name"] == "spare")
    assert (spare["state"], spare["since"], spare["windows"], spare["remaining_pct"], spare["running"],
            spare["max_concurrency"]) == ("available", None, [], None, 0, 2)
    assert spare["agents"] == [{"agent": "backup", "roles": ["executor"], "enabled": True}]
    assert all(a["agent"] != "lead" for account in snap["accounts"] for a in account["agents"])
    assert "private-home" not in json.dumps(snap)
    assert snap["running"][0]["timeout_s"] == 900


# @pt.id T0075-account-order
# @pt.item Account severity order
# @pt.condition Auth, quota and available accounts
# @pt.expected Auth first, then quota, then available sorted by name
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_accounts_sorted_by_severity(repo, store, monkeypatch):
    configure(monkeypatch)
    store.set_account_state("work", "quota", "QUOTA: wait", actor=Actor.BRIDGE)
    store.set_account_state("login", "auth", "AUTH: expired", actor=Actor.BRIDGE)
    tid = add(store)
    store.db.execute("UPDATE tasks SET account='adhoc' WHERE id=?", (tid,))
    snap = progress.snapshot([str(repo)], now=time.time())
    assert [(a["name"], a["state"]) for a in snap["accounts"]] == [
        ("login", "auth"), ("work", "quota"), ("adhoc", "available"), ("spare", "available")]
    login = snap["accounts"][0]
    assert login["agents"] == [{"agent": "helper", "roles": ["reviewer"], "enabled": False}]
    assert login["provider"] == "anthropic"
    assert snap["accounts"][2]["provider"] is None


# @pt.id T0075-switches
# @pt.item Automatic account switches
# @pt.condition Valid, unknown-reason, malformed and week-old account_auto_switched events
# @pt.expected Recent valid events newest first; unknown reason is other; others skipped
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_account_switches(repo, store, monkeypatch):
    configure(monkeypatch)
    tid = add(store)
    store.log(tid, Actor.BRIDGE, "account_auto_switched",
              {"from": "coder", "to": "backup", "from_account": "work", "to_account": "spare",
               "reason": "quota", "remaining_pct": 0.0, "to_remaining_pct": 80.0})
    now = time.time() + 10
    insert = ("INSERT INTO events(ts,task_id,actor,kind,detail) "
              "VALUES (?,?,'bridge','account_auto_switched',?)")
    store.db.execute(insert, (now - 5, tid, json.dumps(
        {"from": "backup", "to": "coder", "from_account": "spare", "to_account": "work",
         "reason": "mystery"})))
    store.db.execute(insert, (now - 4, tid, "{not json"))
    store.db.execute(insert, (now - 3, tid, json.dumps(["not", "a", "mapping"])))
    store.db.execute(insert, (now - 8 * 86400, tid, json.dumps(
        {"from": "coder", "to": "backup", "reason": "limit"})))
    snap = progress.snapshot([str(repo)], now=now)
    pid = progress.project_id(str(repo))
    switches = snap["switches"]
    assert [s["reason"] for s in switches] == ["other", "quota"]
    assert switches[0]["ts"] == now - 5
    assert {k: switches[1][k] for k in ("pid", "task_id", "from", "to", "from_account", "to_account")} == {
        "pid": pid, "task_id": tid, "from": "coder", "to": "backup",
        "from_account": "work", "to_account": "spare"}
    assert set(switches[0]) == {"pid", "task_id", "ts", "from", "to", "from_account", "to_account", "reason"}


# @pt.id T0075-fallback
# @pt.item Quota fallback policy per project
# @pt.condition Configured routing.quota_fallback, then an unreadable config
# @pt.expected Enabled policy with candidates; None without config while database accounts remain
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_fallback_policy(repo, store, monkeypatch):
    configure(monkeypatch)
    store.set_account_state("work", "quota", "QUOTA: wait", actor=Actor.BRIDGE)
    snap = progress.snapshot([str(repo)], now=time.time())
    assert snap["projects"][0]["fallback"] == {"enabled": True, "min_remaining_pct": 15.0,
                                               "agents": ["backup"]}
    def unavailable(*args):
        raise ValueError("No config")
    monkeypatch.setattr(progress.config, "load_config", unavailable)
    snap = progress.snapshot([str(repo)], now=time.time())
    assert snap["projects"][0]["fallback"] is None
    assert [(a["name"], a["state"], a["provider"], a["max_concurrency"], a["agents"])
            for a in snap["accounts"]] == [("work", "quota", None, 1, [])]


# @pt.id T0075-redaction
# @pt.item Account error sanitization
# @pt.condition Stored account last_error contains a secret and long text
# @pt.expected Redacted error bounded to 200 characters
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_account_error_is_redacted(repo, store, monkeypatch):
    configure(monkeypatch)
    token = "sk-" + "a" * 40
    store.set_account_state("work", "auth", "AUTH: login", actor=Actor.BRIDGE)
    store.db.execute("UPDATE accounts SET last_error=? WHERE name='work'", ("AUTH: " + token + "x" * 600,))
    snap = progress.snapshot([str(repo)], now=time.time())
    work = snap["accounts"][0]
    assert work["name"] == "work" and work["state"] == "auth"
    assert token not in json.dumps(snap)
    assert "[REDACTED]" in work["error"]
    assert len(work["error"]) <= 201


# @pt.id T0076-lifted-reset
# @pt.item Effective account state in the overview
# @pt.condition Quota mark whose stored window reset after the mark and before now, used below 100 %
# @pt.expected State available, error None, since kept, lifted reset
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_lapsed_quota_mark_is_available(repo, store, monkeypatch):
    configure(monkeypatch)
    forbid_probes(monkeypatch)
    store.set_account_state("work", "quota", "QUOTA: usage limit", actor=Actor.BRIDGE)
    since = store.db.execute("SELECT updated_at FROM accounts WHERE name='work'").fetchone()[0]
    store.save_quota("work", {
        "primary": {"used_percent": 40, "window_minutes": 300, "resets_at": since + 50}})
    snap = progress.snapshot([str(repo)], now=since + 100)
    work = next(a for a in snap["accounts"] if a["name"] == "work")
    assert (work["state"], work["error"], work["since"], work["lifted"]) == (
        "available", None, since, "reset")
    assert work["remaining_pct"] == 100.0
    assert all(a["state"] == "available" for a in snap["accounts"])


# @pt.id T0076-quota-stands
# @pt.item Effective account state in the overview
# @pt.condition Quota mark with an exhausted window whose reset lies in the future
# @pt.expected State quota with lifted None
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_quota_mark_with_future_reset_stands(repo, store, monkeypatch):
    configure(monkeypatch)
    forbid_probes(monkeypatch)
    store.set_account_state("work", "quota", "QUOTA: usage limit", actor=Actor.BRIDGE)
    since = store.db.execute("SELECT updated_at FROM accounts WHERE name='work'").fetchone()[0]
    store.save_quota("work", {
        "primary": {"used_percent": 100, "window_minutes": 300, "resets_at": since + 3600}})
    snap = progress.snapshot([str(repo)], now=since + 100)
    work = next(a for a in snap["accounts"] if a["name"] == "work")
    assert (work["state"], work["error"], work["lifted"]) == ("quota", "QUOTA: usage limit", None)


# @pt.id T0076-available-not-lifted
# @pt.item Effective account state in the overview
# @pt.condition Account stored as available and accounts without any stored state
# @pt.expected Every account entry has lifted None
# @pt.target bridge.progress.snapshot
# @pt.method unit
# @pt.legacy None
def test_snapshot_available_account_not_lifted(repo, store, monkeypatch):
    configure(monkeypatch)
    forbid_probes(monkeypatch)
    store.set_account_state("work", "available", actor=Actor.BRIDGE)
    snap = progress.snapshot([str(repo)], now=time.time())
    work = next(a for a in snap["accounts"] if a["name"] == "work")
    assert work["state"] == "available" and work["lifted"] is None
    assert all("lifted" in a and a["lifted"] is None for a in snap["accounts"])
