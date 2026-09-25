"""The local HTTP server behind the desk UI.

Standard library only - ``http.server`` and ``json``. The application has to
open on a machine with the network unplugged, and a dependency that needs
installing is a dependency that will be missing on the morning it matters.

**This server is not on the network and must not be treated as if it were.** It
binds to the loopback interface, and three checks keep a web page the user
happens to have open in another tab from driving it:

1. **A per-run token.** Generated at startup, handed to the browser in the URL
   the launcher opens, and required on every ``/api/`` request. It is not a
   password - it is a capability that only the process that printed it can know,
   which is enough to stop a blind cross-origin POST from rewriting the risk
   configuration.
2. **Origin and Host pinning.** A cross-site request carries an ``Origin`` the
   loopback server did not issue; those are refused before the handler runs.
3. **Loopback binding.** ``127.0.0.1`` by default, so nothing off the machine can
   reach it at all. ``--host`` can widen it, and the launcher prints a warning
   when it does, because a risk tool that writes files should never be quietly
   listening on a shared network.

Static files come from :meth:`ProjectPaths.assets_dir` - the project folder's own
``assets/`` when present, the bundled copy otherwise - and every path is resolved
and checked to be inside that directory before it is opened.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import socket
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

from . import api
from .paths import ProjectPaths, paths

__all__ = ["DeskServer", "build_server"]

#: Requests larger than this are refused unread. The largest legitimate body is a
#: full configuration document, which is a few kilobytes.
MAX_BODY_BYTES = 512 * 1024

_SAFE_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


class DeskServer:
    """Owns the socket, the token and the resolved project paths."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0,
                 project: Optional[ProjectPaths] = None,
                 token: Optional[str] = None,
                 open_access: bool = False):
        self.paths = (project or paths()).ensure()
        self.host = host
        self.token = token or secrets.token_urlsafe(24)
        #: Set only by an explicit opt-in; the API token check is skipped, which
        #: is useful for scripted probing and never appropriate for real use.
        self.open_access = open_access
        self._httpd = ThreadingHTTPServer((host, port), _make_handler(self))
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    # ---- lifecycle ----------------------------------------------------
    @property
    def base_url(self) -> str:
        shown = "127.0.0.1" if self.host in ("", "0.0.0.0") else self.host
        return f"http://{shown}:{self.port}"

    @property
    def app_url(self) -> str:
        return f"{self.base_url}/?token={self.token}"

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def start_background(self) -> "DeskServer":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True,
                                        name="futures-desk")
        self._thread.start()
        return self

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "DeskServer":
        return self.start_background()

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()

    # ---- request checks ----------------------------------------------
    def token_ok(self, supplied: Optional[str]) -> bool:
        if self.open_access:
            return True
        if not supplied:
            return False
        return secrets.compare_digest(supplied, self.token)

    def origin_ok(self, origin: Optional[str]) -> bool:
        """Reject a cross-site caller; allow a same-origin or origin-less one.

        A browser sends no ``Origin`` on a same-origin ``GET``, and ``curl``
        sends none at all, so absence cannot be treated as hostile. What can be
        rejected is an ``Origin`` naming a host this server did not serve.
        """
        if not origin:
            return True
        try:
            parsed = urlparse(origin)
        except ValueError:
            return False
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1", self.host):
            return False
        return parsed.port in (self.port, None)


def _make_handler(server: DeskServer) -> type:
    """Build a handler class bound to one :class:`DeskServer`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "FuturesDesk/1.0"
        protocol_version = "HTTP/1.1"

        # ---- plumbing -------------------------------------------------
        def log_message(self, fmt: str, *args: Any) -> None:
            """Quiet by default; ``FA_DESK_VERBOSE`` turns the access log on.

            The default ``BaseHTTPRequestHandler`` log writes a line per request
            to stderr. The UI polls, so that is a wall of text in the console the
            user launched from, and it buries the one line that matters - the URL.
            """
            if os.environ.get("FA_DESK_VERBOSE"):
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str,
                  extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # No caching: the whole design reads the project folder fresh so a
            # hand-edited file shows up on the next click.
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, status: int, message: str, field: str = "") -> None:
            self._json(status, {"error": message, "field": field, "status": status})

        # ---- routing --------------------------------------------------
        def do_GET(self) -> None:      # noqa: N802
            self._handle("GET")

        def do_HEAD(self) -> None:     # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:     # noqa: N802
            self._handle("POST")

        def _handle(self, method: str) -> None:
            try:
                parsed = urlparse(self.path)
                route = parsed.path
                if route.startswith("/api/"):
                    self._handle_api(method, route, parsed.query)
                elif method == "GET":
                    self._handle_static(route)
                else:
                    self._error(HTTPStatus.METHOD_NOT_ALLOWED, f"{method} {route}")
            except BrokenPipeError:
                # The browser navigated away mid-response. Nothing to report.
                pass
            except Exception as exc:                      # pragma: no cover
                detail = traceback.format_exc(limit=6)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                            f"{type(exc).__name__}: {exc}\n{detail}")

        def _handle_api(self, method: str, route: str, query: str) -> None:
            if not server.origin_ok(self.headers.get("Origin")):
                self._error(HTTPStatus.FORBIDDEN,
                            "cross-origin request refused - this server only "
                            "answers the page it served itself")
                return
            supplied = (self.headers.get("X-Desk-Token")
                        or (parse_qs(query).get("token") or [None])[0])
            if not server.token_ok(supplied):
                self._error(HTTPStatus.UNAUTHORIZED,
                            "missing or invalid session token - reopen the URL the "
                            "application printed at startup")
                return

            handler: Optional[Callable[..., dict]] = api.ROUTES.get(method, {}).get(route)
            if handler is None:
                self._error(HTTPStatus.NOT_FOUND, f"no route for {method} {route}")
                return

            payload: Dict[str, Any] = {}
            if method == "POST":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    self._error(HTTPStatus.BAD_REQUEST, "malformed Content-Length")
                    return
                if length > MAX_BODY_BYTES:
                    self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                f"body of {length} bytes exceeds the "
                                f"{MAX_BODY_BYTES} byte limit")
                    return
                raw = self.rfile.read(length) if length else b""
                if raw:
                    try:
                        payload = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        self._error(HTTPStatus.BAD_REQUEST,
                                    f"request body is not valid JSON: {exc}")
                        return
                    if not isinstance(payload, dict):
                        self._error(HTTPStatus.BAD_REQUEST,
                                    "request body must be a JSON object")
                        return

            try:
                result = handler(payload, p=server.paths)
            except api.ApiError as exc:
                self._error(exc.status, str(exc), exc.field)
                return
            self._json(HTTPStatus.OK, result)

        # ---- static ---------------------------------------------------
        def _handle_static(self, route: str) -> None:
            assets = server.paths.assets_dir
            if assets is None:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                            "no assets directory found. Expected "
                            f"{server.paths.external_assets} or a bundled copy.")
                return
            rel = "app.html" if route in ("/", "") else route.lstrip("/")
            try:
                target = (assets / rel).resolve()
            except (OSError, RuntimeError):
                self._error(HTTPStatus.BAD_REQUEST, "unresolvable path")
                return
            # Containment check: a resolved path must still sit under the assets
            # directory, which stops `../../etc/passwd` and symlink escapes alike.
            try:
                target.relative_to(assets.resolve())
            except ValueError:
                self._error(HTTPStatus.FORBIDDEN, "path escapes the assets directory")
                return
            if not target.is_file():
                self._error(HTTPStatus.NOT_FOUND, f"{rel} not found in {assets}")
                return
            suffix = target.suffix.lower()
            ctype = _SAFE_TYPES.get(suffix)
            if ctype is None:
                guessed, _ = mimetypes.guess_type(target.name)
                ctype = guessed or "application/octet-stream"
            self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    return Handler


def build_server(*, host: str = "127.0.0.1", port: int = 0,
                 project: Optional[ProjectPaths] = None,
                 open_access: bool = False) -> DeskServer:
    """Create a server, reporting a busy port in terms the user can act on."""
    try:
        return DeskServer(host=host, port=port, project=project,
                          open_access=open_access)
    except OSError as exc:
        raise SystemExit(
            f"Could not listen on {host}:{port} ({exc}).\n"
            f"Another copy of the desk may already be running. Use --port 0 to let "
            f"the operating system pick a free one."
        ) from None
