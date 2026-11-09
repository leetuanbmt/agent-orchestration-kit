"""Verification commands run confined (audit SEC-01 fix 3, QA-BUG-001)."""

from __future__ import annotations

import socket
import threading

import pytest

from bridge import sandbox
from bridge.models import State
from bridge.orchestrator import parse
from tests.conftest import run, spec

needs_landlock = pytest.mark.skipif(not sandbox.landlock_abi(), reason="kernel without Landlock")


def flow(mode: str, writable: str = "[]") -> str:
    return f"""
version: 1
accounts:
  default: {{provider: openai}}
agents:
  codex: {{adapter: codex-cli, account: default}}
roles:
  executor: codex
workflow:
  stages: [execute, test]
  verify_sandbox: {mode}
  verify_writable: {writable}
"""


def check_output(b, command: str) -> tuple[str, dict]:
    t = run(b.create_task(spec(verification_commands=[command])))["task_id"]
    out = run(b.execute_task(t, wait=True))
    return out["state"], b.store.attempts(t)[-1]["verification"]["checks"][0]


# @pt.id KIT-UNIT-052-sandbox-wrap
# @pt.item How a check's argv is wrapped for each verify_sandbox mode
# @pt.condition none; auto and strict with Landlock; auto and strict without it
# @pt.expected none and auto-without-Landlock run the argv as is; with Landlock the argv runs under `python -I -c WRAPPER` with the writable dirs and the network flag; strict without Landlock returns None (never unconfined)
# @pt.target bridge.sandbox.wrap
# @pt.method unit
# @pt.legacy checks always ran unconfined
def test_wrap_modes(monkeypatch):
    argv = ["pytest", "-q"]
    assert sandbox.wrap(argv, ["/w"], "none") == argv
    monkeypatch.setattr(sandbox, "backend", lambda: "landlock")
    wrapped = sandbox.wrap(argv, ["/w", "/t"], "auto")
    assert wrapped[1:4] == ["-I", "-c", sandbox.WRAPPER] and wrapped[4:] == ["/w", "/t", "net", "--", *argv]
    assert sandbox.wrap(argv, ["/w"], "strict")[4:6] == ["/w", "nonet"]
    monkeypatch.setattr(sandbox, "backend", lambda: None)
    assert sandbox.wrap(argv, ["/w"], "auto") == argv
    assert sandbox.wrap(argv, ["/w"], "strict") is None


# @pt.id KIT-SEC-SBX-001-auto
# @pt.item Under the default mode a check cannot write outside the worktree but keeps the network
# @pt.condition verify_sandbox auto; a check writes inside src/, into its TMPDIR, then tries $HOME; another connects to a local TCP server
# @pt.expected the worktree and TMPDIR writes succeed, the $HOME write fails with Permission denied (task REWORK); the TCP connect succeeds
# @pt.target bridge.sandbox.WRAPPER, bridge.review.run_verification
# @pt.method integration
# @pt.legacy audit SEC-01 / QA-BUG-001: checks could write anywhere the user can
@needs_landlock
def test_auto_confines_writes(make_orch, repo, tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_orch(flow("auto"))
    target = tmp_path / "outside.txt"
    ok = ("python3 -c \"import os, pathlib; pathlib.Path('src/inside.txt').write_text('x'); "
          "pathlib.Path(os.environ['TMPDIR'], 't').write_text('x')\"")
    state, check = check_output(b, ok)
    assert check["passed"], check
    state, check = check_output(b, f"python3 -c \"open('{target}', 'w')\"")
    assert state == State.REWORK and "Permission denied" in check["output_tail"]
    assert not target.exists() and os.environ["HOME"]
    server = socket.create_server(("127.0.0.1", 0))
    threading.Thread(target=server.accept, daemon=True).start()
    port = server.getsockname()[1]
    state, check = check_output(b, f"python3 -c \"import socket; socket.create_connection(('127.0.0.1', {port}))\"")
    assert check["passed"], check
    server.close()


# @pt.id KIT-SEC-SBX-002-strict
# @pt.item Strict mode also cuts the network
# @pt.condition verify_sandbox strict; a check connects to a local TCP server
# @pt.expected the connect fails with Permission denied and the check fails
# @pt.target bridge.sandbox.WRAPPER
# @pt.method integration
# @pt.legacy none
@needs_landlock
def test_strict_denies_tcp(make_orch, monkeypatch):
    if sandbox.landlock_abi() < 4:
        pytest.skip("Landlock ABI < 4 has no network rules")
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_orch(flow("strict"))
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    state, check = check_output(b, f"python3 -c \"import socket; socket.create_connection(('127.0.0.1', {port}))\"")
    server.close()
    assert not check["passed"] and "Permission denied" in check["output_tail"], check


# @pt.id KIT-SEC-SBX-003-writable
# @pt.item A human-configured extra writable directory
# @pt.condition verify_writable lists a cache directory; a check writes there and into a sibling directory
# @pt.expected the cache write succeeds, the sibling write is denied
# @pt.target WorkflowConfig.verify_writable
# @pt.method integration
# @pt.legacy none
@needs_landlock
def test_verify_writable(make_orch, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    cache, sibling = tmp_path / "cache", tmp_path / "other"
    cache.mkdir()
    sibling.mkdir()
    b = make_orch(flow("auto", f"[{cache}]"))
    state, check = check_output(b, f"python3 -c \"open('{cache}/x', 'w')\"")
    assert check["passed"], check
    state, check = check_output(b, f"python3 -c \"open('{sibling}/x', 'w')\"")
    assert not check["passed"] and not (sibling / "x").exists()


# @pt.id KIT-SEC-SBX-004-strict-unavailable
# @pt.item Strict mode never falls back to running unconfined
# @pt.condition verify_sandbox strict on a system without Landlock (simulated)
# @pt.expected the check is not run and fails with a message naming the reason; health says checks are refused
# @pt.target bridge.sandbox.wrap, bridge.sandbox.describe
# @pt.method integration
# @pt.legacy none
def test_strict_without_landlock_refuses(make_orch, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    monkeypatch.setattr(sandbox, "backend", lambda: None)
    b = make_orch(flow("strict"))
    state, check = check_output(b, "python3 -c \"open('src/ran.txt', 'w')\"")
    assert not check["passed"] and "was not run" in check["error"]
    assert not (repo / "src" / "ran.txt").exists()
    assert "refused" in run(b.health())["verify_sandbox"]


# @pt.id KIT-UNIT-053-writable-validation
# @pt.item verify_writable takes absolute paths only
# @pt.condition a relative path; an absolute path and a ~ path
# @pt.expected the relative path is a config error; the others are accepted
# @pt.target bridge.orchestrator.WorkflowConfig
# @pt.method unit
# @pt.legacy none
def test_writable_validation():
    with pytest.raises(ValueError, match="verify_writable needs absolute paths"):
        parse(flow("auto", "[cache]"))
    assert parse(flow("auto", "[/var/cache/x, ~/.cache/uv]")).workflow.verify_writable == [
        "/var/cache/x", "~/.cache/uv"]


# @pt.id KIT-SEC-SBX-005-seatbelt-git-config
# @pt.item Under Seatbelt a check cannot write the repository's config even though the worktree is writable
# @pt.condition verify_sandbox auto on macOS; a check appends a line to .git/config
# @pt.expected the write is refused (Operation not permitted) and .git/config is unchanged
# @pt.target bridge.review.run_verification, bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy audit F-01: .git was writable inside the worktree
@pytest.mark.skipif(sandbox.backend() != "seatbelt", reason="Seatbelt unavailable or nested")
def test_seatbelt_denies_git_config_write(make_orch, repo, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    before = (repo / ".git" / "config").read_text()
    b = make_orch(flow("auto"))
    _, check = check_output(
        b, "python3 -c \"open('.git/config', 'a').write('[core]\\n')\"")
    assert not check["passed"] and "Operation not permitted" in check["output_tail"], check
    assert (repo / ".git" / "config").read_text() == before


# @pt.id KIT-SEC-SBX-006-read-deny
# @pt.item Under Seatbelt a check cannot read account credentials or the project's token files
# @pt.condition an account home holding auth.json and .ai/orchestrator.yaml; checks read each of them and src/app.py
# @pt.expected the credential reads fail with Operation not permitted; the src/app.py read passes
# @pt.target bridge.review.run_verification, bridge.review.verification_read_denies, bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy audit F-03: verification code could read every account's token
@pytest.mark.skipif(sandbox.backend() != "seatbelt", reason="Seatbelt unavailable or nested")
def test_seatbelt_denies_credential_reads(make_orch, repo, tmp_path):
    from bridge.review import run_verification, verification_read_denies
    b = make_orch(flow("auto"))
    account_home = tmp_path / "acct-home"
    account_home.mkdir()
    (account_home / "auth.json").write_text("{}")
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "orchestrator.yaml").write_text("token: x\n")
    secrets = [account_home / "auth.json", repo / ".ai" / "orchestrator.yaml"]
    cmds = [f"python3 -c \"open('{p}').read()\"" for p in secrets]
    cmds.append("python3 -c \"open('src/app.py').read()\"")
    checks = run(run_verification(
        cmds, b.root, b.cfg, confine="auto",
        read_deny=verification_read_denies(b.root, [str(account_home)])))
    for check in checks[:2]:
        assert not check["passed"] and "Operation not permitted" in check["output_tail"], check
    assert checks[2]["passed"], checks[2]


# @pt.id KIT-SEATBELT-006-verification
# @pt.item Verification provisions confined worktree and TMPDIR writes on macOS
# @pt.condition Seatbelt available, auto mode and a sibling outside the worktree
# @pt.expected worktree and TMPDIR writes pass, outside write fails without creating the file
# @pt.target bridge.review.run_verification, bridge.sandbox.wrap
# @pt.method integration
# @pt.legacy macOS verification ran unconfined
@pytest.mark.skipif(sandbox.backend() != "seatbelt", reason="Seatbelt unavailable or nested")
def test_seatbelt_verification_writes(make_orch, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = make_orch(flow("auto"))
    command = (
        "python3 -c \"import os, pathlib; pathlib.Path('src/inside.txt').write_text('x'); "
        "pathlib.Path(os.environ['TMPDIR'], 't').write_text('x')\""
    )
    _, check = check_output(b, command)
    assert check["passed"], check
    target = tmp_path / "outside-seatbelt.txt"
    state, check = check_output(b, f"python3 -c \"open('{target}', 'w')\"")
    assert state == State.REWORK and "Operation not permitted" in check["output_tail"]
    assert not target.exists()

