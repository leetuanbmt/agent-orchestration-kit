"""Account routing and availability with real fake-Codex subprocesses."""

import hashlib
import json

import pytest

from bridge.models import Actor
from tests.conftest import run, spec


@pytest.fixture
def accounts(tmp_path):
    homes = {name: tmp_path / name for name in ("a", "b")}
    for home in homes.values():
        home.mkdir()
        (home / "auth.json").touch()
    return {name: {"codex_home": str(home)} for name, home in homes.items()}


def calls(state):
    log = state / "log.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


# @pt.id KIT-ACCT-101-selection
# @pt.item Account selection is persisted and passed to Codex
# @pt.condition Explicit, configured default, built-in default and unknown accounts
# @pt.expected Names persist; isolated homes use file auth; unknown account rejected
# @pt.target Bridge.create_task, CodexAdapter.run
# @pt.method integration
# @pt.legacy none (new kit)
def test_selection(make_bridge, accounts, fake_codex, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    b = make_bridge(accounts=accounts, default_account="a")
    for selected, expected in ((None, "a"), ("b", "b"), ("default", "default")):
        task = run(b.create_task(spec(account=selected, verification_commands=[])))
        assert task["account"] == expected
        row = b.store.get(task["task_id"])
        assert row["account"] == row["spec"]["account"] == expected
        assert run(b.execute_task(task["task_id"], wait=True))["state"] == "REVIEW"
        call = calls(fake_codex)[-1]
        assert call["codex_home"] == (accounts[expected]["codex_home"]
                                      if expected != "default" else None)
        assert ('cli_auth_credentials_store="file"' in call["argv"]) == (expected != "default")
    with pytest.raises(ValueError, match="unknown account"):
        run(b.create_task(spec(account="missing")))
    assert len(b.store.list()) == 3


# @pt.id KIT-ACCT-102-resume
# @pt.item Rework retains account and session affinity
# @pt.condition Configured default changes after a named account starts a session
# @pt.expected Resume uses the original home and the session recorded in that home
# @pt.target Bridge.resume_task
# @pt.method integration
# @pt.legacy none (new kit)
def test_resume(make_bridge, accounts, fake_codex):
    b = make_bridge(accounts=accounts, default_account="a")
    task = run(b.create_task(spec(verification_commands=[])))["task_id"]
    first = run(b.execute_task(task, wait=True))
    run(b.review_task(task, "rework", "Repeat the check"))
    b.cfg.default_account = "b"
    result = run(b.resume_task(task, wait=True))
    assert result["state"] == "REVIEW"
    assert result["session_id"] == first["session_id"]
    call = calls(fake_codex)[-1]
    assert call["argv"][:2] == ["exec", "resume"]
    assert call["argv"][-2] == first["session_id"]
    home = accounts["a"]["codex_home"]
    assert call["codex_home"] == home
    key = hashlib.sha256(home.encode()).hexdigest()
    assert first["session_id"] in (fake_codex / "sessions" / f"{key}.txt").read_text().splitlines()


# @pt.id KIT-ACCT-103-availability
# @pt.item Auth and quota isolate account availability without rotation
# @pt.condition Account A fails; another A task and a B task are submitted
# @pt.expected A pauses and refuses work until clear; B reaches REVIEW independently
# @pt.target Bridge._start, Bridge._run_inner, Bridge.clear_account, Store
# @pt.method integration
# @pt.legacy none (new kit)
@pytest.mark.parametrize("failure", ["auth", "quota"])
def test_availability(make_bridge, accounts, fake_codex, monkeypatch, failure):
    from pathlib import Path

    home = Path(accounts["a"]["codex_home"])
    if failure == "auth":
        (home / "auth.json").unlink()
    else:
        monkeypatch.setenv("FAKE_CODEX_SCENARIO", "quota,ok")
    b = make_bridge(accounts=accounts)
    task = run(b.create_task(spec(account="a", verification_commands=[])))["task_id"]
    result = run(b.execute_task(task, wait=True))
    assert result["state"] == "PAUSED" and result["failure"].startswith(failure.upper())
    state = b.store.account_state("a")
    assert state["state"] == failure and state["last_error"]
    second = run(b.create_task(spec(account="a", objective="Second task",
                                    verification_commands=[])))["task_id"]
    with pytest.raises(ValueError, match=rf"account a unavailable \({failure}:"):
        run(b.execute_task(second, wait=True))
    assert len(calls(fake_codex)) == 1
    other = run(b.create_task(spec(account="b", verification_commands=[])))["task_id"]
    assert run(b.execute_task(other, wait=True))["state"] == "REVIEW"
    assert [c["codex_home"] for c in calls(fake_codex)] == [
        accounts["a"]["codex_home"], accounts["b"]["codex_home"]]
    listing = {a["name"]: a for a in b.list_accounts()["accounts"]}
    assert listing["a"]["state"] == failure and listing["a"]["quota"] == "UNKNOWN"
    if failure == "auth":
        (home / "auth.json").touch()
    b.clear_account("a")
    event = b.store.db.execute(
        "SELECT * FROM events WHERE kind='account_state' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert event["task_id"] is None and event["actor"] == Actor.CLAUDE
    assert run(b.resume_task(task, wait=True))["state"] == "REVIEW"
    assert calls(fake_codex)[-1]["codex_home"] == accounts["a"]["codex_home"]
    assert b.store.account_state("a")["state"] == "available"
