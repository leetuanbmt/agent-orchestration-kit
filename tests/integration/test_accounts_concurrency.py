"""Concurrency and human account reassignment through real subprocesses."""

import asyncio
import json
import os
import shutil
import subprocess
import sys

import pytest

from bridge.config import BridgeConfig
from bridge.service import Bridge
from bridge.state import ConflictError
from tests.conftest import run, spec


@pytest.fixture
def pair(repo, tmp_path, fake_codex):
    accounts = {}
    for name in ("a", "b"):
        home = tmp_path / name
        home.mkdir()
        (home / "auth.json").touch()
        accounts[name] = {"codex_home": str(home)}
    other = tmp_path / "other"
    shutil.copytree(repo, other)
    cfg = BridgeConfig(accounts=accounts, lock_dir=str(tmp_path / "locks"))
    return Bridge(repo, cfg), Bridge(other, cfg)


def task(b, account, **kwargs):
    return run(b.create_task(spec(account=account, verification_commands=[], **kwargs)))["task_id"]


def calls(state):
    return [json.loads(line) for line in (state / "log.jsonl").read_text().splitlines()]


# @pt.id KIT-ACCT-201-parallel
# @pt.item Independent accounts run concurrently
# @pt.condition Two repositories execute slow tasks with separate accounts
# @pt.expected Both reach REVIEW and subprocess intervals overlap
# @pt.target Bridge._start, AccountLock
# @pt.method integration
# @pt.legacy none (new kit)
def test_parallel(pair, fake_codex, monkeypatch):
    a, b = pair
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "slow")
    x, y = task(a, "a"), task(b, "b")

    async def scenario():
        return await asyncio.gather(a.execute_task(x, wait=True), b.execute_task(y, wait=True))

    assert all(r["state"] == "REVIEW" for r in run(scenario()))
    left, right = calls(fake_codex)
    assert max(left["t_start"], right["t_start"]) < min(left["t_end"], right["t_end"])


# @pt.id KIT-ACCT-202-conflicts
# @pt.item Account and worktree ownership are exclusive
# @pt.condition Another account writer or another repository using the same account starts
# @pt.expected Conflicts refuse execution and released locks can be acquired afterward
# @pt.target Store.transition, AccountLock
# @pt.method integration
# @pt.legacy none (new kit)
def test_conflicts(pair, monkeypatch):
    a, b = pair
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "slow")
    first = task(a, "a")
    same_account = task(b, "a")
    writer = task(a, "b", objective="second writer")

    async def scenario():
        await a.execute_task(first)
        with pytest.raises(ConflictError, match="busy in another bridge process"):
            await b.execute_task(same_account)
        with pytest.raises(ConflictError, match="worktree"):
            await a.execute_task(writer)
        await a._runs[first]
        assert (await a.execute_task(writer, wait=True))["state"] == "REVIEW"
        assert (await b.execute_task(same_account, wait=True))["state"] == "REVIEW"

    run(scenario())


# @pt.id KIT-ACCT-203-reader
# @pt.item Reader and writer may share a worktree
# @pt.condition Distinct accounts execute slow read-only and workspace-write tasks
# @pt.expected Both reach REVIEW with overlapping execution intervals
# @pt.target Store.transition, Bridge.create_task
# @pt.method integration
# @pt.legacy none (new kit)
def test_reader_writer(pair, fake_codex, monkeypatch):
    a, _ = pair
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "slow")
    x = task(a, "a", sandbox="read-only")
    y = task(a, "b")
    assert a.store.get(y)["spec"]["sandbox"] == "workspace-write"

    async def scenario():
        return await asyncio.gather(a.execute_task(x, wait=True), a.execute_task(y, wait=True))

    assert all(r["state"] == "REVIEW" for r in run(scenario()))
    left, right = calls(fake_codex)
    assert max(left["t_start"], right["t_start"]) < min(left["t_end"], right["t_end"])


# @pt.id KIT-ACCT-204-cancel
# @pt.item Cancellation frees account after process group termination
# @pt.condition Hanging task is cancelled before another repository uses its account
# @pt.expected Parent and child die and the next task reaches REVIEW
# @pt.target Bridge.cancel_task, AccountLock.release
# @pt.method integration
# @pt.legacy none (new kit)
def test_cancel(pair, fake_codex, monkeypatch):
    a, b = pair
    monkeypatch.setenv("FAKE_CODEX_SCENARIO", "hang")
    x, y = task(a, "a"), task(b, "a")

    async def scenario():
        await a.execute_task(x)
        for _ in range(100):
            if (fake_codex / "child.pid").exists():
                break
            await asyncio.sleep(0.05)
        assert (fake_codex / "child.pid").exists()
        await a.cancel_task(x, "stop")
        for filename in ("self.pid", "child.pid"):
            pid = int((fake_codex / filename).read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        monkeypatch.setenv("FAKE_CODEX_SCENARIO", "ok")
        assert (await b.execute_task(y, wait=True))["state"] == "REVIEW"

    run(scenario())


# @pt.id KIT-ACCT-205-reassign
# @pt.item Human reassignment clears session affinity
# @pt.condition Noninteractive CLI refuses; service reassigns a rework task
# @pt.expected Refusal changes nothing; fresh exec uses new home and logs human audit
# @pt.target Bridge.reassign_account, cli._human_confirm
# @pt.method integration
# @pt.legacy none (new kit)
def test_reassign(pair, fake_codex):
    a, _ = pair
    x = task(a, "a")
    first = run(a.execute_task(x, wait=True))
    run(a.review_task(x, "rework", "repeat"))
    env = {**os.environ, "BRIDGE_PROJECT_ROOT": str(a.root)}
    result = subprocess.run([sys.executable, "-m", "bridge.cli", "reassign", x, "b"],
                            env=env, stdin=subprocess.DEVNULL, capture_output=True)
    assert result.returncode == 2
    assert a.store.get(x)["session_id"] == first["session_id"]
    assert a.store.get(x)["account"] == "a"
    a.reassign_account(x, "b")
    row = a.store.get(x)
    assert row["session_id"] is None
    assert row["account"] == row["spec"]["account"] == "b"
    event = a.store.events(x)[-1]
    assert event["actor"] == "human" and event["kind"] == "account_reassigned"
    assert event["detail"] == {"from": "a", "to": "b"}
    assert run(a.resume_task(x, wait=True))["state"] == "REVIEW"
    call = calls(fake_codex)[-1]
    assert call["argv"][:2] != ["exec", "resume"]
    assert call["codex_home"] == a.cfg.accounts["b"].codex_home


# @pt.id KIT-ACCT-206-process-lock
# @pt.item Account ownership spans bridge processes
# @pt.condition A separate process holds the same home lock
# @pt.expected Execution is refused until that process releases its lock
# @pt.target AccountLock
# @pt.method integration
# @pt.legacy none (new kit)
def test_process_lock(pair):
    a, _ = pair
    x = task(a, "a")
    code = (
        "import sys; from pathlib import Path; "
        "from bridge.accounts import Account, AccountLock; "
        "lock=AccountLock(Account('alias', Path(sys.argv[1])), sys.argv[2]); "
        "lock.acquire(); print('locked', flush=True); sys.stdin.read(); lock.release()"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, a.cfg.accounts["a"].codex_home, a.cfg.lock_dir],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "locked"
        with pytest.raises(ConflictError, match="busy in another bridge process"):
            run(a.execute_task(x, wait=True))
    finally:
        proc.communicate(timeout=5)
    assert run(a.execute_task(x, wait=True))["state"] == "REVIEW"
