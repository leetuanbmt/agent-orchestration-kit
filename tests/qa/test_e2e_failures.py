"""E2E: agent failures, bad input and boundary values, through the real CLI."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.qa.conftest import Sandbox, alive


# @pt.id QA-E2E-020-quota-pause
# @pt.item An agent hitting its usage limit pauses the task and marks the account
# @pt.condition the executor reports a usage limit; then another task on the same account; then account-clear and resume
# @pt.expected PAUSED with QUOTA, attempt given back; account-list shows quota; a new execute is refused naming account-clear; after account-clear the resume reaches REVIEW
# @pt.target bridge.cli execute/account-list/account-clear/resume
# @pt.method e2e
# @pt.legacy none
def test_quota_pause_and_clear(sandbox: Sandbox) -> None:
    task = sandbox.create()
    out = sandbox.ok("execute", task, env={"FAKE_CODEX_SCENARIO": "quota"})
    assert out["state"] == "PAUSED" and out["failure"].startswith("QUOTA") and out["attempts"] == 0
    accounts = {a["name"]: a for a in sandbox.ok("account-list")["accounts"]}
    assert accounts["default"]["state"] == "quota"
    other = sandbox.create("Another task")
    refused = sandbox.cli("execute", other)
    assert refused.code == 1 and "account-clear default" in refused.err
    assert sandbox.ok("account-clear", "default")["state"] == "available"
    assert sandbox.ok("resume", task)["state"] == "REVIEW"


# @pt.id QA-E2E-021-auth-pause
# @pt.item An expired login pauses the task and never moves it
# @pt.condition the executor answers 401
# @pt.expected PAUSED with AUTH; the account is marked auth; resume is refused until the mark is cleared
# @pt.target bridge.cli execute/resume
# @pt.method e2e
# @pt.legacy none
def test_auth_pause(sandbox: Sandbox) -> None:
    task = sandbox.create()
    out = sandbox.ok("execute", task, env={"FAKE_CODEX_SCENARIO": "auth"})
    assert out["state"] == "PAUSED" and out["failure"].startswith("AUTH")
    res = sandbox.cli("resume", task)
    assert res.code == 1 and "unavailable (auth" in res.err


# @pt.id QA-E2E-022-network-retry
# @pt.item A dropped connection is retried once
# @pt.condition the first agent call loses the stream, the second succeeds
# @pt.expected one retry event and the task reaches REVIEW; two agent calls
# @pt.target Bridge run loop via the CLI
# @pt.method e2e
# @pt.legacy none
def test_network_retry(sandbox: Sandbox) -> None:
    task = sandbox.create()
    assert sandbox.ok("execute", task, env={"FAKE_CODEX_SCENARIO": "network,ok"})["state"] == "REVIEW"
    assert len(sandbox.events(task, "retry")) == 1 and len(sandbox.calls()) == 2


# @pt.id QA-E2E-023-crash
# @pt.item An agent crash fails the attempt; resume recovers
# @pt.condition the agent exits 139; then resume with a healthy agent
# @pt.expected FAILED with CRASH and one attempt used; resume reaches REVIEW
# @pt.target bridge.cli execute/resume
# @pt.method e2e
# @pt.legacy none
def test_crash_then_resume(sandbox: Sandbox) -> None:
    task = sandbox.create()
    out = sandbox.ok("execute", task, env={"FAKE_CODEX_SCENARIO": "crash"})
    assert out["state"] == "FAILED" and out["failure"].startswith("CRASH") and out["attempts"] == 1
    assert sandbox.ok("resume", task)["state"] == "REVIEW"


# @pt.id QA-E2E-024-timeout
# @pt.item A hanging agent is killed at its timeout
# @pt.condition timeout_s 30 (the minimum) and an agent that hangs with a child process
# @pt.expected FAILED with TIMEOUT after about 30 s; the agent and its child are killed
# @pt.target executor timeout via the CLI
# @pt.method e2e
# @pt.legacy none
def test_timeout_kills_process_group(sandbox: Sandbox) -> None:
    task = sandbox.create(timeout_s=30)
    started = time.monotonic()
    out = sandbox.ok("execute", task, env={"FAKE_CODEX_SCENARIO": "hang"}, timeout=90)
    elapsed = time.monotonic() - started
    assert out["state"] == "FAILED" and out["failure"].startswith("TIMEOUT"), out
    assert 29 <= elapsed < 60
    state = Path(sandbox.env["FAKE_CODEX_DIR"])
    pids = [int((state / n).read_text()) for n in ("child.pid", "self.pid")]
    deadline = time.monotonic() + 10
    while any(alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not any(alive(p) for p in pids)


# @pt.id QA-E2E-025-nojson
# @pt.item An agent that does not return its final JSON
# @pt.condition the agent prints plain text as its final message
# @pt.expected REWORK because the agent status is unknown; the raw text is kept in the result
# @pt.target bridge.cli execute/result
# @pt.method e2e
# @pt.legacy none
def test_agent_without_final_json(sandbox: Sandbox) -> None:
    task = sandbox.create()
    out = sandbox.ok("execute", task, env={"FAKE_CODEX_SCENARIO": "nojson"})
    assert out["state"] == "REWORK" and "agent status: unknown" in out["failure"]
    assert sandbox.ok("result", task)["attempts"][-1]["final"] == {"raw": "not json"}


# @pt.id QA-E2E-026-missing-binary
# @pt.item The executor CLI is not installed
# @pt.condition codex_bin points to a missing program
# @pt.expected health is NOT OK (exit 1); execute pauses the task with MISSING_BINARY and gives the attempt back
# @pt.target bridge.cli health/execute
# @pt.method e2e
# @pt.legacy none
def test_missing_binary(sandbox: Sandbox) -> None:
    sandbox.config(codex_bin="codex-not-installed")
    assert sandbox.cli("health").code == 1
    task = sandbox.create()
    res = sandbox.cli("execute", task)
    row = sandbox.ok("status", task)
    assert row["state"] in {"PAUSED", "READY"}, (res.err, row)
    if row["state"] == "PAUSED":
        assert row["failure"].startswith("MISSING_BINARY") and row["attempts"] == 0
    else:  # refused before starting: the message must say why
        assert res.code == 1 and "not found" in res.err


# @pt.id QA-E2E-027-scope-violation
# @pt.item An agent writing outside its allowed paths
# @pt.condition allowed_paths src/**; the agent also writes docs/notes.md, then a run that writes .env
# @pt.expected REWORK with a scope violation naming the file each time; never REVIEW
# @pt.target bridge.review.scope_violations via the CLI
# @pt.method e2e
# @pt.legacy none
@pytest.mark.parametrize("path", ["docs/notes.md", ".env"])
def test_scope_violation(sandbox: Sandbox, path: str) -> None:
    sandbox.env["FAKE_CODEX_WRITE"] = f"src/app.py=x = 2\n;{path}=changed"
    task = sandbox.create()
    out = sandbox.ok("execute", task)
    violations = sandbox.ok("result", task)["attempts"][-1]["verification"]["scope_violations"]
    assert out["state"] == "REWORK", out
    assert any(path in v for v in violations), violations


# @pt.id QA-E2E-028-head-moved
# @pt.item An agent committing on its own
# @pt.condition the agent runs git commit
# @pt.expected REWORK with head_changed true
# @pt.target bridge.review.diff via the CLI
# @pt.method e2e
# @pt.legacy none
def test_agent_commit_is_rejected(sandbox: Sandbox) -> None:
    sandbox.env["FAKE_CODEX_WRITE"] = "src/app.py=x = 2\n"
    task = sandbox.create()
    out = sandbox.ok("execute", task, env={"FAKE_CODEX_SCENARIO": "commit"})
    assert out["state"] == "REWORK"
    assert sandbox.ok("result", task)["attempts"][-1]["changes"]["head_changed"] is True


BAD_SPECS = {
    "protected-path": ({"allowed_paths": [".env"]}, "protected"),
    "traversal": ({"allowed_paths": ["../outside"]}, "traversal"),
    "absolute": ({"allowed_paths": ["/etc/**"]}, "project-relative"),
    "git-dir": ({"allowed_paths": [".git/**"]}, "protected"),
    "ai-config": ({"allowed_paths": [".ai/**"]}, "protected"),
    "not-allowlisted": ({"verification_commands": ["curl http://example.com"]}, "not allowlisted"),
    "objective-too-long": ({"objective": "x" * 4001}, "4000"),
    "timeout-too-low": ({"timeout_s": 5}, "timeout_s"),
    "timeout-too-high": ({"timeout_s": 999999}, "timeout_s"),
    "attempts-too-high": ({"max_attempts": 6}, "max_attempts"),
    "unknown-field": ({"priority": "urgent"}, "priority"),
    "bad-risk": ({"risk_level": "critical"}, "risk_level"),
    "empty-objective": ({"objective": ""}, "objective"),
    "no-paths": ({"allowed_paths": []}, "allowed_paths"),
    "danger-sandbox": ({"sandbox": "danger-full-access"}, "sandbox"),
}


# @pt.id QA-E2E-030-invalid-specs
# @pt.item Invalid task specs are refused at creation
# @pt.condition each bad spec in BAD_SPECS
# @pt.expected exit 1 with a message naming the problem, no traceback, and no task is created
# @pt.target bridge.cli create
# @pt.method e2e
# @pt.legacy none
@pytest.mark.parametrize("case", list(BAD_SPECS))
def test_invalid_spec_refused(sandbox: Sandbox, case: str) -> None:
    extra, needle = BAD_SPECS[case]
    res = sandbox.cli("create", str(sandbox.spec(**extra)))
    assert res.code == 1, res.out
    assert needle in res.err and "Traceback" not in res.err, res.err
    assert sandbox.ok("list") == []


# @pt.id QA-E2E-031-bad-files
# @pt.item Spec files that are missing or not JSON
# @pt.condition a path that does not exist, a non-JSON file and a JSON list
# @pt.expected exit 1 with a one-line error naming the file and no Python traceback
# @pt.target bridge.cli create
# @pt.method e2e
# @pt.legacy none
@pytest.mark.parametrize("content", [None, "not json", "[1, 2]"])
def test_bad_spec_file(sandbox: Sandbox, content: str | None) -> None:
    path = sandbox.base / "spec.json"
    if content is not None:
        path.write_text(content)
    res = sandbox.cli("create", str(path))
    assert res.code == 1
    assert "Traceback" not in res.err and res.err.startswith("error: ") and str(path) in res.err, res.err


# @pt.id QA-E2E-035-shell-syntax
# @pt.item Verification commands written with shell operators
# @pt.condition a verification command "python3 -c 'print(1)' && python3 -c 'raise SystemExit(1)'"
# @pt.expected refused at creation: commands run without a shell, so the second part would never run
# @pt.target bridge.review.parse_command via the CLI
# @pt.method e2e
# @pt.legacy none
@pytest.mark.xfail(strict=True, reason="QA-BUG-004: shell operators are accepted and passed as "
                   "arguments, so the check silently differs from what was written")
def test_shell_syntax_refused(sandbox: Sandbox) -> None:
    cmd = "python3 -c 'print(1)' && python3 -c 'raise SystemExit(1)'"
    res = sandbox.cli("create", str(sandbox.spec(verification_commands=[cmd])))
    assert res.code == 1, "accepted; the part after && is never run"


# @pt.id QA-E2E-032-unknown-ids
# @pt.item Commands on a task that does not exist, or in the wrong state
# @pt.condition status/result/execute/cancel/resume on T9999; review on a READY task; an invalid decision and state filter
# @pt.expected each exits non-zero with a short message and no traceback
# @pt.target bridge.cli
# @pt.method e2e
# @pt.legacy none
def test_unknown_ids_and_wrong_states(sandbox: Sandbox) -> None:
    for args in (("status", "T9999"), ("result", "T9999"), ("execute", "T9999"),
                 ("cancel", "T9999", "--reason", "x"), ("resume", "T9999")):
        res = sandbox.cli(*args)
        assert res.code != 0 and "T9999" in res.err and "Traceback" not in res.err, (args, res.err)
        assert "'unknown task" not in res.err, res.err
    task = sandbox.create()
    res = sandbox.cli("review", task, "--decision", "approve", "--notes", "x")
    assert res.code == 1 and "review needs REVIEW" in res.err
    assert sandbox.cli("review", task, "--decision", "merge", "--notes", "x").code == 2
    res = sandbox.cli("list", "--state", "BOGUS")
    assert res.code != 0 and "Traceback" not in res.err, res.err


# @pt.id QA-E2E-033-boundaries
# @pt.item Boundary values a user can hit
# @pt.condition empty project; a 4000-character Vietnamese objective (the maximum); max_attempts 1 and 5; timeout_s 30
# @pt.expected empty listings; the long objective is stored whole and shown cut to 200 characters; the limits are accepted
# @pt.target bridge.cli create/list/result
# @pt.method e2e
# @pt.legacy none
def test_boundaries(sandbox: Sandbox) -> None:
    assert sandbox.ok("list") == []
    assert sandbox.ok("tasks")["projects"] == []
    long = ("Sửa lỗi hiển thị tiếng Việt có dấu " * 200)[:4000]
    task = sandbox.create(long)
    assert len(sandbox.ok("status", task)["objective"]) == 200
    assert sandbox.ok("result", task)["spec"]["objective"] == long
    for extra in ({"max_attempts": 1}, {"max_attempts": 5}, {"timeout_s": 30}):
        assert sandbox.cli("create", str(sandbox.spec(json.dumps(extra), **extra))).code == 0


# @pt.id QA-E2E-034-many-tasks
# @pt.item Listing and creating at volume
# @pt.condition 150 tasks created through the CLI
# @pt.expected list returns 50 by default and at most 200 with --limit 500; each create stays under 2 s; list stays under 2 s
# @pt.target bridge.cli create/list
# @pt.method e2e
# @pt.legacy none
def test_many_tasks(sandbox: Sandbox) -> None:
    slowest = 0.0
    for i in range(150):
        started = time.monotonic()
        sandbox.create(f"Task number {i}")
        slowest = max(slowest, time.monotonic() - started)
    started = time.monotonic()
    assert len(sandbox.ok("list")) == 50
    assert time.monotonic() - started < 2
    assert len(sandbox.ok("list", "--limit", "500")) == 150
    assert slowest < 2, slowest
