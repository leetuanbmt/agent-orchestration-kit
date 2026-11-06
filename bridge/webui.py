"""Read-only localhost HTTP view of bridge progress."""

import errno
import hmac
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser

from . import progress

STATIC = Path(__file__).parent / "static"


def make_server(port: int = 8765, roots: list[str] | None = None,
                launch_code: str | None = None) -> ThreadingHTTPServer:
    started = time.monotonic()
    code_used = False
    sessions: set[str] = set()
    auth_lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return None

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
            super().end_headers()

        def respond(self, status, body, content_type="application/json; charset=utf-8", allow=False):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if allow:
                self.send_header("Allow", "GET")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def handle_request(self):
            nonlocal code_used
            port = self.server.server_address[1]
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0] not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.respond(421, {"error": "bad host"})
                return
            path = self.path.split("?", 1)[0]
            if launch_code is not None and path.startswith("/api/"):
                cookie = SimpleCookie()
                try:
                    cookie.load("; ".join(self.headers.get_all("Cookie", [])))
                    supplied = cookie["bridge_session"].value if "bridge_session" in cookie else ""
                except CookieError:
                    supplied = ""
                with auth_lock:
                    authenticated = any(hmac.compare_digest(
                        supplied.encode("utf-8"), session.encode("utf-8")) for session in sessions)
                if not authenticated:
                    self.respond(401, {"error": "session required"})
                    return
            if self.command not in ("GET", "HEAD"):
                self.respond(405, {"error": "method not allowed"}, allow=True)
                return
            if self.command == "GET" and path == "/" and launch_code is not None:
                codes = parse_qs(urlsplit(self.path).query).get("code", [])
                with auth_lock:
                    if (len(codes) == 1 and not code_used
                            and time.monotonic() - started < 600
                            and hmac.compare_digest(codes[0].encode("utf-8"),
                                                    launch_code.encode("utf-8"))):
                        code_used = True
                        session = secrets.token_urlsafe(32)
                        sessions.add(session)
                        self.send_response(303)
                        self.send_header("Location", "/")
                        self.send_header("Set-Cookie",
                                         f"bridge_session={session}; HttpOnly; SameSite=Strict; Path=/")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
            if path == "/" or path in ("/static/app.css", "/static/app.js"):
                name, mime = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
                    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
                }[path]
                try:
                    body = (STATIC / name).read_bytes()
                except FileNotFoundError:
                    self.respond(503 if path == "/" else 404,
                                 {"error": "ui not built" if path == "/" else "not found"})
                    return
                self.respond(200, body, mime)
                return
            try:
                if path == "/api/overview":
                    self.respond(200, progress.snapshot(roots))
                    return
                log_match = re.fullmatch(r"/api/projects/([0-9a-f]{12})/tasks/(T[0-9]{4,})/log", path)
                if log_match:
                    query = self.path.partition("?")[2]
                    if re.fullmatch(r"(?:n=[0-9]+(?:&offset=[0-9]+)?|offset=[0-9]+&n=[0-9]+)", query):
                        params = dict(part.split("=", 1) for part in query.split("&"))
                        result = progress.task_log(*log_match.groups(), int(params["n"]),
                                                   int(params.get("offset", "0")), roots)
                        if result is not None:
                            self.respond(200, result)
                            return
                match = re.fullmatch(r"/api/projects/([0-9a-f]{12})/(requests|tasks)/([^/]+)", path)
                if match:
                    pid, kind, ident = match.groups()
                    valid = progress.TASK_ID.fullmatch(ident) or (
                        kind == "requests" and progress.WORKFLOW_ID.fullmatch(ident))
                    if valid:
                        detail = progress.request_detail if kind == "requests" else progress.task_detail
                        result = detail(pid, ident, roots)
                        if result is not None:
                            self.respond(200, result)
                            return
            except Exception:
                self.respond(500, {"error": "internal"})
                return
            self.respond(404, {"error": "not found"})

        do_GET = handle_request
        do_HEAD = handle_request

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self.handle_request
            raise AttributeError(name)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port: int = 8765, open_browser: bool = False) -> None:
    launch_code = secrets.token_urlsafe(32)
    try:
        server = make_server(port, launch_code=launch_code)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        print(f"error: port {port} is in use; try --port 0", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/?code={launch_code}"
        print(f"agent-bridge web: {url}", flush=True)
        print("This link works once; do not share it.", flush=True)
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
