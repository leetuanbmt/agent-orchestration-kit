"""E2E: the web dashboard as a person opens it, through a real browser (Playwright) and HTTP."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bridge.progress import project_id
from tests.qa.conftest import BIN, Sandbox

DRIVER = Path(__file__).parent / "web" / "dashboard.cjs"


def _browsers_path() -> str:
    """Where Playwright keeps its browsers for the real user. Read at import, before the tests
    swap HOME for a throwaway one (Playwright would then look in that empty home)."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    home = Path.home()
    if sys.platform == "darwin":
        return str(home / "Library" / "Caches" / "ms-playwright")
    if sys.platform == "win32":
        return str(Path(os.environ.get("LOCALAPPDATA", home)) / "ms-playwright")
    return str(Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / "ms-playwright")


BROWSERS = _browsers_path()
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
                             timeout=120,
                             env={**self.box.env, "PLAYWRIGHT_BROWSERS_PATH": BROWSERS})
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


SCENARIOS = ["load", "drawer", "no_token", "xss", "live", "language", "search", "offline", "mobile",
             "loading", "theme", "drawer_layout", "mobile_cards", "attention", "board", "live_region", "shell",
             "overview", "sidebar", "table_fit", "target_size"]


# @pt.id QA-E2E-073-web-browser
# @pt.item The dashboard in a real browser
# @pt.condition one scenario each: load, drawer, no_token, xss, live, language, search, mobile, offline, loading, theme, drawer_layout, mobile_cards, attention, board, live_region, shell, overview, sidebar, table_fit, target_size (see tests/qa/web/dashboard.cjs)
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


# @pt.id QA-E2E-075-web-capacity
# @pt.item Account capacity and quota failover on the dashboard
# @pt.condition two claude-cli accounts cl1/cl2 with quota fallback on; a task pauses on QUOTA on cl1 and resume moves it to claude2 (account_auto_switched); a second task on cl2 reports 40 % five-hour usage; browser scenario capacity
# @pt.expected Accounts shows a card per account with a state badge (cl1 out of quota or lifted), cl2 quota meters with role=meter and aria values, the fallback policy on, one switch row naming claude1 and claude2 that opens the task drawer; the Overview summary lists both accounts and the fallback line; no horizontal scroll at 375 px; Vietnamese heading and fallback line
# @pt.target bridge/static/app.js, bridge.progress.snapshot accounts/switches
# @pt.method e2e (Playwright)
# @pt.legacy none
@needs_browser
def test_web_capacity(sandbox: Sandbox) -> None:
    from tests.qa.test_e2e_accounts import WRITE, claude_accounts
    sandbox.orchestrator(claude_accounts())
    failover = sandbox.create()
    out = sandbox.ok("execute", failover, env={"FAKE_CLAUDE_SCENARIO": "quota,ok"})
    assert out["state"] == "PAUSED" and out["failure"].startswith("QUOTA"), out
    out = sandbox.ok("resume", failover, env={"FAKE_CLAUDE_SCENARIO": "quota,ok"})
    assert out["agent"] == "claude2", out
    switched, = sandbox.events(failover, "account_auto_switched")
    assert switched["detail"]["to"] == "claude2", switched
    second = sandbox.create("Second task", agent="claude2")
    out = sandbox.ok("execute", second, env={"FAKE_CLAUDE_UTIL": "0.4", "FAKE_CLAUDE_WRITE": WRITE})
    assert out["agent"] == "claude2", out
    proc, url = start_web(sandbox)
    base, _, token = url.partition("/?token=")
    board = Dashboard(sandbox, proc, url, base, int(base.rsplit(":", 1)[1]), token,
                      project_id(str(sandbox.root)), {"failover": failover, "second": second})
    status, _, body = board.request("GET", "/api/overview")
    data = json.loads(body)
    assert status == 200 and {a["name"] for a in data["accounts"]} >= {"cl1", "cl2"}, data["accounts"]
    assert len(data["switches"]) == 1, data["switches"]
    result = board.browser("capacity")
    failed = [c for c in result["checks"] if not c["ok"]]
    assert result["ok"] and not result.get("error"), json.dumps(
        {"error": result.get("error"), "failed": failed}, indent=1)[:3000]


# @pt.id QA-E2E-096-web-many-projects
# @pt.item Project identity and bounded Home lists
# @pt.condition three projects with T0001 in REVIEW, nine attention tasks, and grouped changed files including cache paths
# @pt.expected project tags disambiguate tasks; queue expands from six; Accounts has one shared fallback panel after cards; cache files start collapsed; no overflow or JS errors
# @pt.target bridge/static/app.js, bridge/static/index.html, bridge/static/app.css
# @pt.method e2e (Playwright)
# @pt.legacy none
@needs_browser
def test_web_many_projects(sandbox: Sandbox) -> None:
    from tests.qa.conftest import make_project

    roots = [sandbox.root]
    for name in ("project-two", "project-three"):
        root = make_project(sandbox.base / name)
        assert sandbox.cli("init", cwd=root).code == 0
        (root / ".ai" / "config.json").write_text(json.dumps({
            "retry_backoff_s": 0, "lock_dir": str(sandbox.base / "locks")}))
        roots.append(root)
    paths = ["src/app.py", *[f"src/file{i}.py" for i in range(8)], ".cache/result.txt"]
    for root in roots:
        for i in range(3):
            tid = sandbox.ok("create", str(sandbox.spec(
                f"Review work {i}", allowed_paths=["src/**", ".cache/**"])), cwd=root)["task_id"]
            out = sandbox.ok("execute", tid, cwd=root,
                             env={"FAKE_CODEX_WRITE": ";".join(f"{path}=x = 2\n" for path in paths)})
            assert out["state"] == "REVIEW", out
            if i == 0:
                assert tid == "T0001"
    proc, url = start_web(sandbox)
    base, _, token = url.partition("/?token=")
    board = Dashboard(sandbox, proc, url, base, int(base.rsplit(":", 1)[1]), token,
                      project_id(str(sandbox.root)), {"review": "T0001"})
    for root in roots:
        status, _, body = board.request("GET", f"/api/projects/{project_id(str(root))}/tasks/T0001")
        task = json.loads(body)
        assert status == 200 and task["state"] == "REVIEW", task
        assert set(task["files"]) == set(paths), task["files"]
    result = board.browser("projects")
    assert result["ok"] and not result.get("error"), json.dumps(result, indent=1)[:5000]


# @pt.id QA-E2E-097-web-workflow
# @pt.item Planner workflow context in the dashboard
# @pt.condition a two-step planner workflow stops WAITING with Step one in REVIEW and Step two in BACKLOG
# @pt.expected drawer shows workflow objective, progress and sibling navigation; task rows show workflow id and search finds both steps; no JavaScript errors
# @pt.target bridge/static/app.js, bridge/static/app.css
# @pt.method e2e (Playwright)
# @pt.legacy none
@needs_browser
def test_web_workflow(sandbox: Sandbox) -> None:
    from tests.qa.test_e2e_workflows import AGENTS, TWO_STEPS, workflow

    sandbox.orchestrator(AGENTS.format(reviewer="claude"))
    env = {"FAKE_CODEX_PLAN": TWO_STEPS}
    planned = sandbox.ok("plan", "Do two steps", env=env)
    wid = planned["workflow_id"]
    assert wid == "W0001", planned
    planned = workflow(sandbox, wid, {"READY", "FAILED"})
    assert planned["state"] == "READY", planned
    assert [t["state"] for t in planned["tasks"]] == ["READY", "BACKLOG"], planned
    sandbox.ok("run-workflow", wid, env=env)
    waiting = workflow(sandbox, wid, {"WAITING", "DONE", "INCOMPLETE"})
    assert waiting["state"] == "WAITING", waiting
    assert [t["state"] for t in waiting["tasks"]] == ["REVIEW", "BACKLOG"], waiting
    first, second = (t["task_id"] for t in waiting["tasks"])
    proc, url = start_web(sandbox)
    base, _, token = url.partition("/?token=")
    board = Dashboard(sandbox, proc, url, base, int(base.rsplit(":", 1)[1]), token,
                      project_id(str(sandbox.root)), {"first": first, "second": second})
    result = board.browser("workflow")
    assert result["ok"] and not result.get("error"), json.dumps(result, indent=1)[:5000]


# @pt.id QA-E2E-098-web-visual
# @pt.item Dashboard visual regression
# @pt.condition deterministic main views and review drawer in light/dark, plus phone Home
# @pt.expected 13 screenshots match platform baselines within 0.1 percent, or update baselines when requested
# @pt.target bridge/static/app.js, bridge/static/app.css
# @pt.method e2e (Playwright)
# @pt.legacy none
def test_web_visual(dashboard: Dashboard) -> None:
    update = os.environ.get("QA_VISUAL_UPDATE") == "1"
    platform = "win32" if sys.platform == "win32" else sys.platform
    baselines = DRIVER.parent / "visual" / platform
    if not update and not any(baselines.glob("*.png")):
        pytest.skip(
            "No visual baselines for " + platform + "; update with "
            "QA_E2E=1 QA_VISUAL_UPDATE=1 uv run --frozen pytest -q "
            "tests/qa/test_e2e_web.py::test_web_visual")
    if not _playwright():
        pytest.skip("node with playwright not installed")
    result = dashboard.browser("visual", update=update)
    assert result["ok"] and not result.get("error"), json.dumps(result, indent=1)[:5000]
