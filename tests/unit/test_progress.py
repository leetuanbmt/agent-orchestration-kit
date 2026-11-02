import hashlib
import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from bridge import progress
from bridge.models import Actor, State, TaskSpec
from bridge.state import Store


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
    assert len(calls) == 3
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
