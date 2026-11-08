from http.client import HTTPConnection
import json
import os
import subprocess
import sys
import threading

import pytest

from bridge import cli, progress, webactions, webui
from bridge.models import TaskSpec
from bridge.state import Store


@pytest.fixture
def env(repo, monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    store = Store(repo / ".git/agent-bridge/bridge.db")
    store.create(TaskSpec(objective="Change", allowed_paths=["src/**"],
                          acceptance_criteria=["Works"]), "0")
    store.db.close()
    return repo


def set_state(repo, state):
    store = Store(repo / ".git/agent-bridge/bridge.db")
    store.update("T0001", state=state)
    store.db.close()


class Fakes:
    def __init__(self, returncode=0, stdout="{}", stderr=""):
        self.runs, self.starts = [], []
        self.completed = (returncode, stdout, stderr)

    def runner(self, argv, root):
        self.runs.append((argv, root))
        code, out, err = self.completed
        return subprocess.CompletedProcess(argv, code, out, err)

    def starter(self, argv, root):
        self.starts.append((argv, root))


def act(repo, body, fakes, tid="T0001", pid=None):
    pid = pid or progress.project_id(str(repo))
    return webactions.run_action([str(repo)], pid, tid, body, runner=fakes.runner, starter=fakes.starter)


# @pt.id T0082-review-approve
# @pt.item Review through the CLI
# @pt.condition Approve a task in REVIEW with a fake runner
# @pt.expected 200 and exact argv, cwd root
# @pt.target bridge.webactions.run_action
# @pt.method integration
# @pt.legacy none
def test_review_approve(env):
    set_state(env, "REVIEW")
    fakes = Fakes(stdout='{"state": "DONE"}')
    status, body = act(env, {"action": "review", "decision": "approve", "notes": "ok"}, fakes)
    assert status == 200 and body == {"ok": True, "action": "review", "result": {"state": "DONE"}}
    assert fakes.runs == [([sys.executable, "-P", "-m", "bridge", "review", "T0001", "--decision", "approve",
                            "--notes", "ok"], str(env))]
    assert not fakes.starts


# @pt.id T0082-process-args
# @pt.item Real process arguments
# @pt.condition run_sync and start_detached with a patched subprocess
# @pt.expected cwd, BRIDGE_PROJECT_ROOT, stdin DEVNULL, detached session
# @pt.target bridge.webactions.run_sync
# @pt.method integration
# @pt.legacy none
def test_process_arguments(monkeypatch, tmp_path):
    seen = {}
    def fake_run(argv, **kwargs):
        seen["run"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")
    def fake_popen(argv, **kwargs):
        seen["popen"] = kwargs
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    webactions.run_sync(["x"], str(tmp_path))
    webactions.start_detached(["x"], str(tmp_path))
    for kind in ("run", "popen"):
        assert seen[kind]["cwd"] == str(tmp_path)
        assert seen[kind]["env"]["BRIDGE_PROJECT_ROOT"] == str(tmp_path)
        assert seen[kind]["stdin"] == subprocess.DEVNULL
    assert seen["run"]["timeout"] == 60
    from bridge import osutil
    for key, value in osutil.GROUP_KWARGS.items():
        assert seen["popen"][key] == value
    if os.name == "posix":
        assert seen["popen"]["start_new_session"] is True


# @pt.id T0082-cancel
# @pt.item Cancel with a reason
# @pt.condition Cancel a READY task; non-JSON stdout
# @pt.expected 200 with null result
# @pt.target bridge.webactions.run_action
# @pt.method integration
# @pt.legacy none
def test_cancel(env):
    fakes = Fakes(stdout="done")
    status, body = act(env, {"action": "cancel", "reason": "no longer needed"}, fakes)
    assert status == 200 and body["result"] is None
    assert fakes.runs[0][0] == [sys.executable, "-P", "-m", "bridge", "cancel", "T0001",
                                "--reason", "no longer needed"]


# @pt.id T0099-worktree-package
# @pt.item Web actions use the installed kit
# @pt.condition A project contains a bridge package whose entry point writes a marker
# @pt.expected Cancel succeeds through the installed kit and the marker is absent
# @pt.target bridge.webactions.run_action
# @pt.method integration
# @pt.legacy none
def test_worktree_bridge_package_is_not_executed(env):
    package = env / "bridge"
    package.mkdir()
    (package / "__init__.py").write_text("")
    marker = env / "shadow-executed"
    (package / "__main__.py").write_text(
        "from pathlib import Path\nPath('shadow-executed').write_text('executed')\n")

    status, _ = webactions.run_action(
        [str(env)], progress.project_id(str(env)), "T0001",
        {"action": "cancel", "reason": "security regression"})

    assert not marker.exists()
    assert status == 200
    store = Store(env / ".git/agent-bridge/bridge.db")
    try:
        assert store.get("T0001")["state"] == "CANCELLED"
    finally:
        store.db.close()


# @pt.id T0082-start
# @pt.item Detached execute and resume
# @pt.condition Execute from READY, resume from REWORK
# @pt.expected 202 through the detached starter
# @pt.target bridge.webactions.run_action
# @pt.method integration
# @pt.legacy none
def test_execute_and_resume(env):
    set_state(env, "READY")
    fakes = Fakes()
    assert act(env, {"action": "execute"}, fakes) == (202, {"ok": True, "action": "execute", "started": True})
    set_state(env, "REWORK")
    assert act(env, {"action": "resume", "feedback": "try again"}, fakes)[0] == 202
    assert act(env, {"action": "resume"}, fakes)[0] == 202
    assert all(argv[:4] == [sys.executable, "-P", "-m", "bridge"] for argv, _ in fakes.starts)
    assert [argv[4:] for argv, _ in fakes.starts] == [
        ["execute", "T0001"], ["resume", "T0001", "--feedback", "try again"], ["resume", "T0001"]]
    for state in ("FAILED", "PAUSED"):
        set_state(env, state)
        assert act(env, {"action": "resume"}, fakes)[0] == 202
    assert not fakes.runs


# @pt.id T0082-failure
# @pt.item CLI failure
# @pt.condition Non-zero exit with a secret in stderr, and a timeout
# @pt.expected 409 with redacted stderr tail
# @pt.target bridge.webactions.run_action
# @pt.method integration
# @pt.legacy none
def test_cli_failure(env):
    set_state(env, "REVIEW")
    secret = "sk-ant-api03-" + "a" * 40
    fakes = Fakes(returncode=1, stderr="x" * 900 + f" token={secret}")
    status, body = act(env, {"action": "review", "decision": "block", "notes": "n"}, fakes)
    assert status == 409 and secret not in body["error"] and len(body["error"]) <= 600

    def timeout(argv, root):
        raise subprocess.TimeoutExpired(argv, 60)
    status, _ = webactions.run_action([str(env)], progress.project_id(str(env)), "T0001",
                                      {"action": "cancel", "reason": "r"}, runner=timeout)
    assert status == 409


# @pt.id T0082-validation
# @pt.item Request validation
# @pt.condition Unknown ids, bad fields and extra fields
# @pt.expected 404 or 400 without running anything
# @pt.target bridge.webactions.run_action
# @pt.method integration
# @pt.legacy none
def test_validation(env):
    set_state(env, "REVIEW")
    fakes = Fakes()
    pid = progress.project_id(str(env))
    assert act(env, {"action": "cancel", "reason": "r"}, fakes, pid="000000000000") == (404, {"error": "not found"})
    assert act(env, {"action": "cancel", "reason": "r"}, fakes, tid="T9999")[0] == 404
    assert act(env, {"action": "cancel", "reason": "r"}, fakes, tid="bad")[0] == 404
    assert act(env, {"action": "cancel", "reason": "r"}, fakes, pid="../x")[0] == 404
    good = {"action": "review", "decision": "approve", "notes": "ok"}
    for body in ({}, {"action": "approve"}, {"action": "reassign"}, {"action": "account"}, {"action": []}, {**good, "decision": "maybe"},
                 {**good, "notes": ""}, {**good, "notes": "x" * 4001}, {**good, "notes": 5},
                 {"action": "cancel"}, {"action": "cancel", "reason": "x" * 501},
                 {**good, "extra": 1}, {"action": "execute", "notes": "x"},
                 {"action": "resume", "feedback": "x" * 4001}, {"action": "resume", "feedback": 3}, []):
        assert act(env, body, fakes)[0] == 400, body
    assert act(env, {**good, "notes": "x" * 4000}, fakes)[0] == 200
    assert len(fakes.runs) == 1 and not fakes.starts
    del pid


# @pt.id T0082-state
# @pt.item State gate
# @pt.condition Actions in the wrong state
# @pt.expected 409 with the state and no runner call
# @pt.target bridge.webactions.run_action
# @pt.method integration
# @pt.legacy none
def test_wrong_state(env):
    fakes = Fakes()
    set_state(env, "READY")
    status, body = act(env, {"action": "review", "decision": "approve", "notes": "n"}, fakes)
    assert (status, body["state"]) == (409, "READY")
    assert act(env, {"action": "resume"}, fakes)[0] == 409
    set_state(env, "REVIEW")
    assert act(env, {"action": "execute"}, fakes)[0] == 409
    for state in ("DONE", "CANCELLED", "BLOCKED"):
        set_state(env, state)
        assert act(env, {"action": "cancel", "reason": "r"}, fakes)[0] == 409
    assert not fakes.runs and not fakes.starts


# ---- HTTP -----------------------------------------------------------------------------------

def start(repo, **kwargs):
    value = webui.make_server(0, roots=[str(repo)], **kwargs)
    thread = threading.Thread(target=value.serve_forever, daemon=True)
    thread.start()
    if kwargs.get("launch_code"):
        conn = HTTPConnection(*value.server_address, timeout=5)
        conn.request("GET", "/?code=" + kwargs["launch_code"])
        response = conn.getresponse()
        value.test_cookie = dict(response.getheaders())["Set-Cookie"].split(";")[0]
        response.read()
        conn.close()
    return value, thread


def stop(value, thread):
    value.shutdown()
    value.server_close()
    thread.join()


@pytest.fixture
def web(env):
    value, thread = start(env, launch_code="t", read_only=False)
    try:
        yield value
    finally:
        stop(value, thread)


def post(server, path, body=b"{}", token="t", headers=None, length=True, ctype="application/json"):
    port = server.server_address[1]
    conn = HTTPConnection(*server.server_address, timeout=5)
    try:
        conn.putrequest("POST", path, skip_host=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        if token is not None and hasattr(server, "test_cookie"):
            conn.putheader("Cookie", server.test_cookie if token == "t" else "bridge_session=" + token)
        if ctype is not None:
            conn.putheader("Content-Type", ctype)
        for key, value in ({"Sec-Fetch-Site": "same-origin"} if headers is None else headers).items():
            conn.putheader(key, value)
        if length:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def action_path(repo):
    return f"/api/projects/{progress.project_id(str(repo))}/tasks/T0001/actions"


# @pt.id T0082-http
# @pt.item POST action endpoint checks
# @pt.condition Bad token, content type, origin, fetch site, length and body
# @pt.expected 401/415/403/411/413/400 and a valid action reaches run_action
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_http_checks(web, env, monkeypatch):
    path = action_path(env)
    calls = []
    def fake(roots, pid, tid, body):
        calls.append((pid, tid, body))
        return 200, {"ok": True}
    monkeypatch.setattr(webactions, "run_action", fake)
    body = json.dumps({"action": "execute"}).encode()
    assert post(web, path, body, token=None)[0] == 401
    assert post(web, path, body, token="wrong")[0] == 401
    assert post(web, path, body, ctype="text/plain")[0] == 415
    assert post(web, path, body, ctype=None)[0] == 415
    assert post(web, path, body, headers={"Origin": "http://evil.test"})[0] == 403
    assert post(web, path, body, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert post(web, path, body, headers={})[0] == 403
    assert post(web, path, body, headers={"Origin": f"http://127.0.0.1:{web.server_address[1] + 1}", "Sec-Fetch-Site": "same-site"})[0] == 403
    assert post(web, path, body, length=False)[0] == 411
    assert post(web, path, b"[" + b" " * 16384 + b"]")[0] == 413
    for bad in (b"[]", b"3", b"not json", b""):
        assert post(web, path, bad)[0] == 400
    assert not calls
    port = web.server_address[1]
    status, _, answer = post(web, path, body, ctype="application/json; charset=utf-8",
                             headers={"Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
    assert (status, json.loads(answer)) == (200, {"ok": True})
    assert post(web, path, body, headers={"Sec-Fetch-Site": "none"})[0] == 200
    assert calls[0] == (progress.project_id(str(env)), "T0001", {"action": "execute"})


# @pt.id T0082-http-other
# @pt.item Other paths and read-only mode
# @pt.condition POST elsewhere, read_only, no token; overview flag
# @pt.expected 405 with Allow GET and the actions flag
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_http_405_and_flag(web, env):
    def overview(server, token):
        conn = HTTPConnection(*server.server_address, timeout=5)
        headers = {"Cookie": server.test_cookie} if token else {}
        conn.request("GET", "/api/overview", headers=headers)
        return json.loads(conn.getresponse().read())["actions"]
    for other in ("/api/overview", "/", action_path(env) + "/x",
                  action_path(env).replace("T0001", "bad")):
        status, headers, _ = post(web, other)
        assert status == 405 and headers["Allow"] == "GET"
    assert overview(web, "t") is True
    for kwargs in ({"launch_code": "t"}, {"launch_code": "t", "read_only": True},
                   {"read_only": False}):
        server, thread = start(env, **kwargs)
        try:
            status, headers, _ = post(server, action_path(env), token=kwargs.get("launch_code"))
            assert status == 405 and headers["Allow"] == "GET"
            assert overview(server, kwargs.get("launch_code")) is False
        finally:
            stop(server, thread)


# @pt.id T0082-cli-flag
# @pt.item web --read-only flag
# @pt.condition Parse web with and without the flag
# @pt.expected Flag listed in help and passed to serve
# @pt.target bridge.cli.main
# @pt.method integration
# @pt.legacy none
def test_read_only_flag(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_BRIDGE_AGENT", raising=False)
    calls = []
    monkeypatch.setattr(webui, "serve", lambda *a, **k: calls.append((a, k)))
    cli.main(["web", "--read-only"])
    cli.main(["web"])
    assert calls[0][1] == {"read_only": True} and calls[1][1] == {"read_only": False}
    with pytest.raises(SystemExit):
        cli.main(["web", "--help"])
    assert "--read-only" in capsys.readouterr().out


@pytest.mark.parametrize("decision", ["rework", "block"])
def test_review_decisions(env, decision):
    set_state(env, "REVIEW")
    fakes = Fakes(stdout='{"ok": true}')
    status, result = act(env, {"action": "review", "decision": decision, "notes": "Reviewed"}, fakes)
    assert status == 200 and result["result"] == {"ok": True}
    assert fakes.runs[0][0][6:] == ["--decision", decision, "--notes", "Reviewed"]


def test_server_environment_does_not_gate_caller(env, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "claude")
    fakes = Fakes()
    assert act(env, {"action": "cancel", "reason": "Stop"}, fakes)[0] == 200
    assert len(fakes.runs) == 1
