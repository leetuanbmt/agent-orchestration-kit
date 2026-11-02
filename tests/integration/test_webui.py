import hashlib
from http.client import HTTPConnection
import json
import threading

import pytest

from bridge import progress, webui
from bridge.models import TaskSpec
from bridge.state import Store


@pytest.fixture
def server(repo):
    store = Store(repo / ".git/agent-bridge/bridge.db")
    for n in range(2):
        store.create(TaskSpec(objective=f"Change {n}", allowed_paths=["src/**"],
                              acceptance_criteria=["Works"]), str(n))
    store.db.close()
    value = webui.make_server(0, roots=[str(repo)])
    thread = threading.Thread(target=value.serve_forever, daemon=True)
    thread.start()
    try:
        yield value
    finally:
        value.shutdown()
        value.server_close()
        thread.join()


def request(server, path="/api/overview", method="GET", host=True, token=None):
    conn = HTTPConnection(*server.server_address, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        if token is not None:
            conn.putheader("X-Bridge-Token", token)
        if host is not None:
            conn.putheader("Host", f"127.0.0.1:{server.server_address[1]}" if host is True else host)
        conn.endheaders()
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


# @pt.id T0024-overview
# @pt.item Overview and detail HTTP routes
# @pt.condition Two tasks in a fixture repository
# @pt.expected JSON overview and known details succeed; view errors are hidden
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_overview_shape(server, repo, monkeypatch):
    assert server.server_address[0] == "127.0.0.1"
    status, headers, body = request(server)
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert set(json.loads(body)) == {"generated_at", "projects", "attention", "running", "activity", "requests"}
    pid = progress.project_id(str(repo))
    for kind in ("tasks", "requests"):
        assert request(server, f"/api/projects/{pid}/{kind}/T0001")[0] == 200
    assert request(server, host=f"localhost:{server.server_address[1]}")[0] == 200
    def broken(roots):
        raise RuntimeError(str(repo))
    monkeypatch.setattr(progress, "snapshot", broken)
    assert request(server)[::2] == (500, b'{"error": "internal"}')


# @pt.id T0024-host
# @pt.item Host validation
# @pt.condition Foreign, wrong-port, duplicate or missing Host
# @pt.expected 421 before routing
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_rejects_foreign_or_missing_host(server):
    for host in ("evil.test:8765", "127.0.0.1:0", None):
        assert request(server, "/unknown", host=host)[::2] == (421, b'{"error": "bad host"}')


# @pt.id T0024-methods
# @pt.item Read-only methods
# @pt.condition Mutating and arbitrary methods plus HEAD
# @pt.expected 405 with Allow GET; HEAD has GET headers and no body
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_only_get(server):
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CUSTOM"):
        status, headers, _ = request(server, method=method)
        assert status == 405 and headers["Allow"] == "GET"
    status, headers, body = request(server, method="HEAD")
    assert status == 200 and body == b"" and int(headers["Content-Length"]) > 0


# @pt.id T0024-ids
# @pt.item Invalid and unknown identifiers
# @pt.condition Malformed IDs, unknown project/task and traversal
# @pt.expected 404 JSON without filesystem paths
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_bad_ids_are_404_without_paths(server, repo):
    pid = progress.project_id(str(repo))
    for path in (f"/api/projects/{pid}/tasks/..%2Fx", "/api/projects/000000000000/tasks/T0001",
                 f"/api/projects/{pid}/tasks/T9999", f"/api/projects/{pid}/tasks/W0001",
                 f"/api/projects/{pid}/requests/W9999", "/api/projects/INVALID/tasks/T0001",
                 f"/api/projects/{pid}/requests/bad", "/unknown"):
        status, _, body = request(server, path)
        assert status == 404 and json.loads(body) == {"error": "not found"}
        assert str(repo).encode() not in body


# @pt.id T0024-headers
# @pt.item Response security and silent logging
# @pt.condition Successful and rejected requests
# @pt.expected Four security headers, no CORS headers or request logs
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_security_headers(server, capsys):
    for path, method, host in (("/", "GET", True), ("/api/overview", "GET", True),
                               ("/missing", "GET", True), ("/", "POST", True),
                               ("/", "GET", None)):
        _, headers, _ = request(server, path, method, host)
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Cache-Control"] == "no-store"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Content-Security-Policy"] == "default-src 'self'; frame-ancestors 'none'"
        assert not any(k.lower().startswith("access-control-") for k in headers)
    assert capsys.readouterr().err == ""


# @pt.id T0024-static
# @pt.item Static allowlist
# @pt.condition Missing index, allowed files and traversal attempts
# @pt.expected 503 before UI build, correct MIME types, 404 for all other files
# @pt.target bridge.webui.STATIC, bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_static_allowlist(server, tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "STATIC", tmp_path)
    assert request(server, "/")[::2] == (503, b'{"error": "ui not built"}')
    for name, mime in (("index.html", "text/html"), ("app.css", "text/css"),
                       ("app.js", "application/javascript")):
        (tmp_path / name).write_text("content", encoding="utf-8")
        path = "/" if name == "index.html" else f"/static/{name}"
        status, headers, body = request(server, path)
        assert status == 200 and body == b"content"
        assert headers["Content-Type"] == f"{mime}; charset=utf-8"
    for path in ("/static/../progress.py", "/static/%2e%2e/app.js", "/static/index.html",
                 "/static/app.js/extra", "/static/unknown", "/static/..%2Fapp.js"):
        assert request(server, path)[::2] == (404, b'{"error": "not found"}')


# @pt.id T0024-read-only
# @pt.item Database remains unchanged
# @pt.condition Ten overview and detail requests
# @pt.expected Database hash and mtime are identical
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_requests_leave_db_unchanged(server, repo):
    db = repo / ".git/agent-bridge/bridge.db"
    def fingerprint():
        return hashlib.sha256(db.read_bytes()).digest(), db.stat().st_mtime_ns
    before = fingerprint()
    pid = progress.project_id(str(repo))
    for n in range(10):
        path = "/api/overview" if n % 2 else f"/api/projects/{pid}/tasks/T0001"
        assert request(server, path)[0] == 200
    assert fingerprint() == before


# @pt.id T0024-port
# @pt.item Occupied port diagnostic
# @pt.condition Another server holds the requested port
# @pt.expected Exit 1 and actionable stderr
# @pt.target bridge.webui.serve
# @pt.method integration
# @pt.legacy none
def test_port_in_use(server, capsys):
    port = server.server_address[1]
    with pytest.raises(SystemExit) as failed:
        webui.serve(port)
    assert failed.value.code == 1
    assert capsys.readouterr().err == f"error: port {port} is in use; try --port 0\n"


# @pt.id T0039-token
# @pt.item Per-launch API authentication
# @pt.condition Server configured with a token
# @pt.expected API routes require token; static routes remain public; Host checked first
# @pt.target bridge.webui.make_server
# @pt.method integration
# @pt.legacy none
def test_api_token(server, repo, monkeypatch):
    protected = webui.make_server(0, roots=[str(repo)], token="t")
    thread = threading.Thread(target=protected.serve_forever, daemon=True)
    thread.start()
    try:
        pid = progress.project_id(str(repo))
        for path in ("/api/overview", f"/api/projects/{pid}/tasks/T0001",
                     f"/api/projects/{pid}/requests/T0001", "/api/unknown"):
            for token in (None, "wrong"):
                assert request(protected, path, token=token)[::2] == (
                    401, b'{"error": "token required"}')
            assert request(protected, path, token="t")[0] == (404 if path == "/api/unknown" else 200)
        for path in ("/", "/static/app.css", "/static/app.js"):
            assert request(protected, path)[0] == 200
        assert request(protected, host="evil.test")[0] == 421
        assert request(protected, method="HEAD")[::2] == (401, b"")
        def forbidden(roots):
            pytest.fail("unauthenticated request accessed dashboard data")
        monkeypatch.setattr(progress, "snapshot", forbidden)
        assert request(protected)[0] == 401
    finally:
        protected.shutdown()
        protected.server_close()
        thread.join()
