"""Security reproductions from the 2026-11-08 audit (docs/audits/04-security-assessment.md),
written against revision 0541153. Each test asserts the CURRENT behaviour, so a PASS means the
finding is still present; after a fix the matching test should FAIL. Fake CLIs and throwaway
repos only (fixtures from tests/conftest.py); payloads write only under pytest's tmp_path.

Map to findings: SEC-01 test_verify_code_outside_worktree_and_forges_human_approval ·
SEC-02 test_web_action_executes_project_bridge_package · SEC-03 test_human_approve_tty_gate_via_script ·
SEC-04 test_protected_config_tamper_persists, test_gitignored_orchestrator_tamper ·
SEC-05 test_symlink_write_through · SEC-06 test_setsid_child_escapes_group_kill ·
SEC-07 test_old_sec05_untouched_file_not_bound · SEC-08 test_old_sec03_with_review_stage ·
SEC-09 test_redact_gaps, test_redact_json_tokens · SEC-11 test_old_sec07_interpreters_still_allowlisted.
Controls that should keep passing: test_web_auth_controls, test_old_rel01_real_git_failure_raises,
test_old_sec10_patterns.

Run (isolated HOME and bridge state, outside the default testpaths on purpose):
    docs/audits/evidence/run_audit_repro.sh docs/audits/evidence/test_audit_2026_11_08_security.py
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from bridge import progress, sandbox, webactions, webui
from bridge.config import BridgeConfig, load_config
from bridge.models import State
from bridge.security import redact
from bridge.service import Bridge
from tests.conftest import git, run, spec

VENV_PY = sys.executable


def _gates_only(repo: Path, fake_codex: Path, stages: str = "[execute, test]") -> Bridge:
    from bridge.orchestrator import parse
    orch = parse(f"""
version: 1
accounts:
  default: {{provider: openai}}
agents:
  codex: {{adapter: codex-cli, account: default}}
  me: {{adapter: client}}
roles:
  executor: codex
{"  reviewer: me" if "review" in stages else ""}
workflow:
  stages: {stages}
""")
    return Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=str(fake_codex / "locks")), orch)


def _writes(tmp: Path, files: dict[str, str]) -> str:
    """FAKE_CODEX_WRITE '@file' form so payloads may contain ';'."""
    f = tmp / "writes.txt"
    f.write_text(";".join(f"{k}={v}" for k, v in files.items()))
    return f"@{f}"


# Verification is unconfined on macOS: executor-authored code writes outside the worktree,
# sees no AGENT_BRIDGE_AGENT marker and closes ANOTHER high-risk task as "human".
def test_verify_code_outside_worktree_and_forges_human_approval(repo, fake_codex, tmp_path,
                                                                 monkeypatch):
    assert sandbox.describe("auto") == "unavailable on this system: checks run unconfined (auto)"
    outside = tmp_path / "OUTSIDE_WORKTREE.txt"
    b = _gates_only(repo, fake_codex)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    t1 = run(b.create_task(spec(risk_level="high")))["task_id"]
    run(b.execute_task(t1, wait=True))
    assert b.store.get(t1)["state"] == State.APPROVED          # waits for a human
    inner = (f"from bridge.service import Bridge\n"
             f"print(Bridge({str(repo)!r}).human_approve({t1!r})['state'])")
    payload = "\n".join([
        "import os, pathlib, subprocess",
        f"pathlib.Path({str(outside)!r}).write_text('marker=' + repr(os.environ.get('AGENT_BRIDGE_AGENT')))",
        f"subprocess.run([{VENV_PY!r}, '-c', {inner!r}], check=True)",
    ])
    monkeypatch.setenv("FAKE_CODEX_WRITE", _writes(tmp_path, {"src/b.py": "y = 1\n",
                                                              "src/check.py": payload}))
    t2 = run(b.create_task(spec(objective="other", acceptance_criteria=["y"],
                                verification_commands=["python3 src/check.py"])))["task_id"]
    run(b.execute_task(t2, wait=True))
    checks = b.store.attempts(t2)[-1]["verification"]["checks"]
    print("T2 checks:", json.dumps(checks)[:400])
    assert outside.read_text() == "marker=None"                 # wrote outside, no agent marker
    assert b.store.get(t1)["state"] == State.DONE               # high-risk task closed
    ev = [e for e in b.store.events(t1, 50) if e["kind"] == "human_approval"]
    print("forged event:", ev[-1]["actor"], ev[-1]["detail"])
    assert b.store.get(t2)["state"] == State.DONE               # and the attack task itself is DONE


# The dashboard runs `python -m bridge` with cwd = project root: a `bridge/` package in the
# project (written by an executor, even as a scope violation left in the worktree) runs instead.
def test_web_action_executes_project_bridge_package(repo, fake_codex, tmp_path, monkeypatch):
    marker = tmp_path / "WEB_ACTION_RAN_PROJECT_CODE"
    evil = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('pwned')\n"
    monkeypatch.setenv("FAKE_CODEX_WRITE", _writes(tmp_path, {"src/app.py": "x = 2\n",
                                                              "bridge/__init__.py": "",
                                                              "bridge/__main__.py": evil}))
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec(max_attempts=2)))["task_id"]
    run(b.execute_task(t, wait=True))
    row = b.store.get(t)
    print("state after run:", row["state"], b.store.attempts(t)[-1]["verification"]["scope_violations"])
    assert row["state"] == State.REWORK and (repo / "bridge" / "__main__.py").exists()
    done = []
    status, body = webactions.run_action([str(repo)], progress.project_id(str(repo)), t,
                                         {"action": "resume"},
                                         starter=lambda argv, root: done.append(
                                             webactions.run_sync(argv, root)))
    print("web resume:", status, body, done[0].returncode)
    assert status == 202 and marker.read_text() == "pwned"


# The interactive-terminal gate is satisfied by a pseudo-terminal any same-user process can open.
def test_human_approve_tty_gate_via_script(repo, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec(risk_level="high")))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.APPROVED
    env = {**os.environ, "BRIDGE_PROJECT_ROOT": str(repo)}
    feed = f"(sleep 3; echo {t}; sleep 3) | script -q /dev/null {VENV_PY} -m bridge approve {t}"
    out = subprocess.run(["sh", "-c", feed], stdin=subprocess.DEVNULL, text=True,
                         capture_output=True, cwd=repo, env=env, timeout=60)
    print("script rc:", out.returncode, out.stdout[-300:], out.stderr[-300:])
    assert b.store.get(t)["state"] == State.DONE
    # and the agent marker is just an environment variable
    out2 = subprocess.run(["env", "-u", "AGENT_BRIDGE_AGENT", VENV_PY, "-m", "bridge", "list",
                           "--json"], capture_output=True, text=True, cwd=repo,
                          env={**env, "AGENT_BRIDGE_AGENT": "1"}, timeout=60)
    assert out2.returncode == 0


# A protected write is BLOCKED but stays in the worktree; the next process loads it.
def test_protected_config_tamper_persists(repo, fake_codex, monkeypatch):
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / "config.json").write_text("{}")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "cfg")
    tampered = '{"protected_paths": [], "verify_allowlist": ["sh", "bash"]}'
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;.ai/config.json={tampered}")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.BLOCKED
    cfg = load_config(repo)
    assert cfg.protected_paths == [] and "bash" in cfg.verify_allowlist
    b2 = Bridge(repo)          # starts without refusing
    assert b2.cfg.protected_paths == []


def test_redact_gaps():
    cases = {
        "json_password": '{"password": "hunter2hunter2"}',
        "stripe": "sk_live_" + "a" * 24,
        "google": "AIza" + "B" * 35,
        "gh_fine_grained": "github_pat_" + "C" * 40,
        "npm": "npm_" + "D" * 36,
        "url_userinfo": "https://user:s3cr3tpass@example.com/x",
        "anthropic": "sk-ant-api03-" + "E" * 40,
        "env_line": "OPENAI_API_KEY=abcdefghijklmnop",
    }
    res = {k: redact(v) for k, v in cases.items()}
    print(json.dumps(res, indent=1))
    leaked = [k for k, v in cases.items() if res[k] == v]
    print("NOT redacted:", leaked)
    assert leaked


def _req(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    return r.status, dict(r.getheaders()), data


def test_web_auth_controls(repo, fake_codex, monkeypatch):
    code = "LAUNCHCODE123"
    srv = webui.make_server(0, roots=[str(repo)], launch_code=code, read_only=False)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        h = {"Host": f"127.0.0.1:{port}"}
        assert _req(port, "GET", "/api/overview", {"Host": f"evil.example:{port}"})[0] == 421
        assert _req(port, "GET", "/api/overview", h)[0] == 401
        s, hd, _ = _req(port, "GET", f"/?code={code}", h)
        cookie = hd["Set-Cookie"]
        print("Set-Cookie:", cookie)
        assert s == 303 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert _req(port, "GET", f"/?code={code}", h)[0] == 200    # page, but no new cookie
        sess = cookie.split(";")[0]
        auth = {**h, "Cookie": sess}
        assert _req(port, "GET", "/api/overview", auth)[0] == 200
        xs = {**auth, "Origin": "http://evil.example", "Content-Type": "application/json"}
        assert _req(port, "POST", "/api/projects/000000000000/tasks/T0001/actions", xs, b"{}")[0] == 403
        # A non-browser same-user client with the cookie only has to add one header.
        forged = {**auth, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"}
        st = _req(port, "POST", f"/api/projects/{progress.project_id(str(repo))}/tasks/T0001/actions",
                  forged, json.dumps({"action": "cancel", "reason": "x"}).encode())[0]
        print("forged non-browser action status:", st)
        assert st in (404, 409, 200)
    finally:
        srv.shutdown()


# A child that leaves the agent's process group (setsid) outlives the group kill.
def test_setsid_child_escapes_group_kill(repo, fake_codex, tmp_path, monkeypatch):
    import stat
    import time
    wrapper_dir = tmp_path / "wrap"
    wrapper_dir.mkdir()
    fakes = Path(os.environ["PATH"].split(os.pathsep)[0])
    w = wrapper_dir / "codex"
    w.write_text("#!/bin/sh\n"
                 "python3 -c \"import os,time\nif os.fork()==0:\n os.setsid()\n time.sleep(3)\n"
                 " open('LATE_OUTSIDE.txt','w').write('late')\" >/dev/null 2>&1 &\n"
                 f"exec {fakes / 'codex'} \"$@\"\n")
    w.chmod(w.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.DONE
    time.sleep(5)
    assert (repo / "LATE_OUTSIDE.txt").exists()


def test_old_sec03_with_review_stage(repo, fake_codex, monkeypatch):
    (repo / ".gitignore").write_text(".env\nbuild/\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "ignore build")
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;build/sitecustomize.py=print('pwn')")
    b = _gates_only(repo, fake_codex, "[execute, test, review]")
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    a = b.store.attempts(t)[-1]
    print("SEC03 review-stage:", b.store.get(t)["state"], a["verification"]["scope_violations"],
          a["changes"]["ignored_files"])
    assert b.store.get(t)["state"] == State.REVIEW
    assert a["verification"]["scope_violations"] == []
    assert a["changes"]["ignored_files"] == ["build/sitecustomize.py"]


def test_old_sec05_untouched_file_not_bound(repo, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec(risk_level="high")))["task_id"]
    run(b.execute_task(t, wait=True))
    assert b.store.get(t)["state"] == State.APPROVED
    (repo / "src" / "conftest.py").write_text("import os  # late, unverified\n")
    git(repo, "commit", "--allow-empty", "-qm", "head moved after verification")
    assert b.human_approve(t)["state"] == State.DONE


def test_old_sec07_interpreters_still_allowlisted():
    from bridge.review import parse_command
    cfg = BridgeConfig()
    assert parse_command("python3 -c \"import os; os.system('id')\"", cfg)[0] == "python3"
    assert parse_command("npx --yes some-remote-package", cfg)[0] == "npx"


def test_old_rel01_real_git_failure_raises(repo):
    import pytest
    from bridge.review import EvidenceError, diff, snapshot
    before = snapshot(repo, [])
    (repo / ".git" / "index").write_bytes(b"garbage")
    with pytest.raises(EvidenceError):
        diff(repo, before, [])


def test_old_sec10_patterns(repo):
    import pytest
    from bridge.security import PathError, validate_pattern
    prot = BridgeConfig().protected_paths
    for bad in ("**/*", "*/**", "**"):
        with pytest.raises(PathError):
            validate_pattern(bad, repo, prot)
    from bridge.review import touches_sensitive
    print("accepted:", validate_pattern(".claude/agents/x.md", repo, prot),
          validate_pattern(".vscode/tasks.json", repo, prot))
    assert touches_sensitive([".claude/agents/x.md"], BridgeConfig())
    assert touches_sensitive([".vscode/tasks.json"], BridgeConfig())


def test_gitignored_orchestrator_tamper(repo, fake_codex, monkeypatch):
    (repo / ".ai").mkdir(exist_ok=True)
    (repo / ".ai" / ".gitignore").write_text("*\n!.gitignore\n!config.json\n")
    yaml = ("version: 1\naccounts:\n  default: {provider: openai}\nagents:\n"
            "  codex: {adapter: codex-cli, account: default}\n  me: {adapter: client}\nroles:\n  executor: codex\n  reviewer: me\n"
            "workflow:\n  stages: [execute, test, review]\n")
    (repo / ".ai" / "orchestrator.yaml").write_text(yaml)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "ai")
    evil = yaml.replace("[execute, test, review]", "[execute, test]")
    monkeypatch.setenv("FAKE_CODEX_WRITE", f"src/app.py=x = 2\n;.ai/orchestrator.yaml={evil}")
    b = Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=str(fake_codex / "locks")))
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    print("tamper state:", b.store.get(t)["state"])
    assert b.store.get(t)["state"] == State.BLOCKED
    h = run(b.health())
    print("health protected_dirty:", h.get("protected_dirty"))
    assert h.get("protected_dirty") == []
    b2 = Bridge(repo, BridgeConfig(retry_backoff_s=0, lock_dir=str(fake_codex / "locks")))
    print("next bridge stages:", b2.orch.workflow.stages)
    assert b2.orch.workflow.stages == ["execute", "test"]


def test_redact_json_tokens():
    cases = ['{"refresh_token": "rt_' + "a" * 30 + '"}', '"access_token": "' + "b" * 30 + '"',
             "refresh_token: " + "c" * 30]
    out = [redact(c) for c in cases]
    print("json tokens:", out)
    assert out[0] == cases[0] and out[1] == cases[1] and out[2] != cases[2]


def test_symlink_write_through(repo, fake_codex, tmp_path, monkeypatch):
    import pytest
    from bridge.security import PathError, validate_pattern
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (repo / "src" / "out").symlink_to(outside)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "link")
    with pytest.raises(PathError):
        validate_pattern("src/out/**", repo, BridgeConfig().protected_paths)
    monkeypatch.setenv("FAKE_CODEX_WRITE", "src/app.py=x = 2\n;src/out/evil.txt=through link")
    b = _gates_only(repo, fake_codex)
    t = run(b.create_task(spec()))["task_id"]
    run(b.execute_task(t, wait=True))
    a = b.store.attempts(t)[-1]
    print("symlink:", b.store.get(t)["state"], a["changes"]["files"], a["verification"]["scope_violations"])
    assert (outside / "evil.txt").read_text() == "through link"
    assert b.store.get(t)["state"] == State.DONE
