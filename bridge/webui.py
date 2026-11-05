"""Read-only localhost HTTP view of bridge progress."""

import errno
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sys
import webbrowser

from . import progress

STATIC = Path(__file__).parent / "static"


def make_server(port: int = 8765, roots: list[str] | None = None,
                token: str | None = None) -> ThreadingHTTPServer:
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
            port = self.server.server_address[1]
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0] not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                self.respond(421, {"error": "bad host"})
                return
            path = self.path.split("?", 1)[0]
            if token is not None and path.startswith("/api/"):
                supplied = self.headers.get_all("X-Bridge-Token", [])
                if len(supplied) != 1 or not hmac.compare_digest(
                        supplied[0].encode("utf-8"), token.encode("utf-8")):
                    self.respond(401, {"error": "token required"})
                    return
            if self.command not in ("GET", "HEAD"):
                self.respond(405, {"error": "method not allowed"}, allow=True)
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
    token = secrets.token_urlsafe(32)
    try:
        server = make_server(port, token=token)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        print(f"error: port {port} is in use; try --port 0", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/?token={token}"
        print(f"agent-bridge web: {url}", flush=True)
        print("This link is the key to the dashboard; do not share it.", flush=True)
        if open_browser:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
