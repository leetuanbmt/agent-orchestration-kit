"""E2E: the web dashboard as a person opens it, through a real browser (Playwright) and HTTP."""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bridge.progress import project_id
from tests.qa.conftest import BIN, Sandbox

DRIVER = Path(__file__).parent / "web" / "dashboard.cjs"
XSS = '<img src=x onerror="window.__xss=1">'


def _playwright() -> bool:
    if not shutil.which("node"):
        return False
    probe = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True)
    return probe.returncode == 0


needs_browser = pytest.mark.skipif(not _playwright(), reason="node with playwright not installed")


@dataclass
class Dashboard:
    box: Sandbox
    proc: subprocess.Popen
    url: str
    base: str
    port: int
    token: str
    pid: str
    tasks: dict[str, str]

    def request(self, method: str, path: str, token: str | None = "", host: str | None = None
                ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token is not None:
            headers["X-Bridge-Token"] = self.token if token == "" else token
        conn.request(method, path, headers=headers)
        res = conn.getresponse()
        return res.status, {k.lower(): v for k, v in res.getheaders()}, res.read()

    def browser(self, scenario: str, **extra: Any) -> dict[str, Any]:
        shots = self.box.base / "shots"
        shots.mkdir(exist_ok=True)
        config = {"url": self.url, "base": self.base, "scenario": scenario, "root": str(self.box.root),
                  "cli": str(BIN / "agent-bridge"), "env": self.box.env, "shots": str(shots),
                  "tasks": self.tasks, "pid": self.pid, "total": len(self.tasks),
                  "serverPid": self.proc.pid, "newSpec": str(self.box.spec("Created while watching")),
                  **extra}
        path = self.box.base / f"web-{scenario}.json"
        path.write_text(json.dumps(config))
        out = subprocess.run(["node", str(DRIVER), str(path)], capture_output=True, text=True,
                             timeout=120, env=self.box.env)
        lines = [line for line in out.stdout.splitlines() if line.startswith("{")]
        assert lines, out.stderr[-2000:]
        return json.loads(lines[-1])


def start_web(box: Sandbox, port: str = "0") -> tuple[subprocess.Popen, str]:
    proc = box.spawn("web", "--port", port)
    line = proc.stdout.readline()
    assert "agent-bridge web: http://127.0.0.1:" in line, (line, proc.stderr.read() if proc.poll() else "")
    return proc, line.split("web: ", 1)[1].strip()


@pytest.fixture
def dashboard(sandbox: Sandbox) -> Iterator[Dashboard]:
    sandbox.env["FAKE_CODEX_WRITE"] = "src/app.py=x = 2\n"
    review = sandbox.create()
    sandbox.ok("execute", review)
    done = sandbox.create("Finished work")
    sandbox.ok("execute", done)
    sandbox.ok("review", done, "--decision", "approve", "--notes", "ok")
    cancelled = sandbox.create("Not needed")
    sandbox.ok("cancel", cancelled, "--reason", "dropped")
    xss = sandbox.create(XSS)
    vietnamese = sandbox.create("Sửa lỗi hiển thị tiếng Việt")
    proc, url = start_web(sandbox)
    base, _, token = url.partition("/?token=")
    yield Dashboard(sandbox, proc, url, base, int(base.rsplit(":", 1)[1]), token,
                    project_id(str(sandbox.root)),
                    {"review": review, "done": done, "cancelled": cancelled, "xss": xss,
                     "vi": vietnamese})


# @pt.id QA-E2E-070-web-http-guards
# @pt.item The dashboard's HTTP guards
# @pt.condition requests without a token, with a wrong token, with a foreign Host header, with POST/DELETE, and with traversal or malformed ids
# @pt.expected 401, 401, 421, 405, 404; the real token gets 200 JSON; security headers on every response; the token is long and random
# @pt.target bridge.webui.make_server
# @pt.method e2e
# @pt.legacy none
def test_web_http_guards(dashboard: Dashboard) -> None:
    status, headers, body = dashboard.request("GET", "/api/overview")
    assert status == 200 and json.loads(body)["projects"]
    assert dashboard.request("GET", "/api/overview", token=None)[0] == 401
    assert dashboard.request("GET", "/api/overview", token="guess")[0] == 401
    assert dashboard.request("GET", "/api/overview", host="evil.example:80")[0] == 421
    assert dashboard.request("GET", "/", host=f"attacker.test:{dashboard.port}")[0] == 421
    for method in ("POST", "DELETE", "PUT"):
        assert dashboard.request(method, "/api/overview")[0] == 405
    review = dashboard.tasks["review"]
    assert dashboard.request("GET", f"/api/projects/{dashboard.pid}/tasks/{review}")[0] == 200
    for path in (f"/api/projects/{dashboard.pid}/tasks/../../../etc/passwd",
                 f"/api/projects/{dashboard.pid}/tasks/T1;DROP",
                 "/api/projects/zzzz/tasks/T0001", "/static/../bridge.db", "/.env"):
        assert dashboard.request("GET", path)[0] == 404, path
    for header, value in (("content-security-policy", "default-src 'self'"),
                          ("x-content-type-options", "nosniff"), ("cache-control", "no-store"),
                          ("referrer-policy", "no-referrer")):
        assert value in headers.get(header, ""), (header, headers)
    assert len(dashboard.token) >= 40


# @pt.id QA-E2E-071-web-port-busy
# @pt.item Starting a second dashboard on a port in use
# @pt.condition `agent-bridge web --port <port of the running one>`
# @pt.expected exit 1 with a hint to use --port 0, no traceback
# @pt.target bridge.webui.serve
# @pt.method e2e
# @pt.legacy none
def test_web_port_busy(dashboard: Dashboard) -> None:
    res = dashboard.box.cli("web", "--port", str(dashboard.port), timeout=20)
    assert res.code == 1 and "--port 0" in res.err and "Traceback" not in res.err, res.err


# @pt.id QA-E2E-072-web-log
# @pt.item Reading an attempt's log from the dashboard API
# @pt.condition the REVIEW task's first attempt log, paged
# @pt.expected 200 with log lines; a bad query is 404
# @pt.target bridge.webui log route
# @pt.method e2e
# @pt.legacy none
def test_web_log(dashboard: Dashboard) -> None:
    review = dashboard.tasks["review"]
    status, _, body = dashboard.request("GET", f"/api/projects/{dashboard.pid}/tasks/{review}/log?n=1")
    assert status == 200, body
    assert dashboard.request("GET", f"/api/projects/{dashboard.pid}/tasks/{review}/log?n=x")[0] == 404


SCENARIOS = ["load", "drawer", "no_token", "xss", "live", "language", "search", "offline", "mobile"]


# @pt.id QA-E2E-073-web-browser
# @pt.item The dashboard in a real browser
# @pt.condition one scenario each: load, drawer, no_token, xss, live, language, search, mobile, offline (see tests/qa/web/dashboard.cjs)
# @pt.expected every check of the scenario passes and the page raises no JavaScript error
# @pt.target bridge/static/app.js, bridge.webui
# @pt.method e2e (Playwright)
# @pt.legacy none
@needs_browser
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_web_browser(dashboard: Dashboard, scenario: str) -> None:
    result = dashboard.browser(scenario)
    failed = [c for c in result["checks"] if not c["ok"]]
    assert result["ok"] and not result.get("error"), json.dumps(
        {"error": result.get("error"), "failed": failed}, indent=1)[:3000]


# @pt.id QA-E2E-074-web-performance
# @pt.item Overview at volume
# @pt.condition 300 tasks in the project
# @pt.expected /api/overview answers in under 1 s; the browser table with every task is ready in under 3 s
# @pt.target bridge.progress.snapshot, bridge/static/app.js
# @pt.method e2e (Playwright)
# @pt.legacy none
@needs_browser
def test_web_performance(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from bridge.service import Bridge
    monkeypatch.setenv("HOME", str(sandbox.home))
    seeder = Bridge(sandbox.root)  # bulk data setup only; the measurement goes through HTTP
    ids = [asyncio.run(seeder.create_task({"objective": f"Bulk task {i}", "allowed_paths": ["src/**"],
                                           "acceptance_criteria": ["a"]}))["task_id"]
           for i in range(300)]
    proc, url = start_web(sandbox)
    base, _, token = url.partition("/?token=")
    board = Dashboard(sandbox, proc, url, base, int(base.rsplit(":", 1)[1]), token,
                      project_id(str(sandbox.root)), {i: i for i in ids})
    started = time.monotonic()
    status, _, body = board.request("GET", "/api/overview")
    elapsed = time.monotonic() - started
    assert status == 200 and elapsed < 1.0, elapsed
    result = board.browser("performance")
    assert result["ok"], result
