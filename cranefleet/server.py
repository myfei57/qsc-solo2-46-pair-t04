"""HTTP front end for the control console.

The server is intentionally small: it parses the request, hands ``/api`` calls
to the console and serves the static console pages from ``web_root``.
"""

from __future__ import annotations

import json
import logging
import posixpath
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import api
from .console import Console, Request

LOGGER = logging.getLogger("cranefleet.server")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


class ControlServer(ThreadingHTTPServer):
    """Threaded server so concurrent crane reports exercise the interlocks."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], console: Console) -> None:
        super().__init__(address, ControlRequestHandler)
        self.console = console


class ControlRequestHandler(BaseHTTPRequestHandler):
    server: ControlServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802 - routed so unknown verbs get a 405
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802 - routed so unknown verbs get a 405
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802 - routed so unknown verbs get a 405
        self._handle("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        if not api.is_api_path(path):
            self._serve_static(path)
            return
        try:
            version, canonical = api.negotiate(
                path, self.headers.get(api.API_VERSION_HEADER)
            )
        except api.ApiError as exc:
            # Negotiation itself failed: answer in the latest envelope so an
            # unknown future client still gets the classified error shape.
            self._write_json(
                exc.status, api.render_error(api.LATEST_VERSION, exc), api.LATEST_VERSION
            )
            return
        body, valid, body_error = self._read_body()
        request = Request(
            method=method,
            path=canonical,
            query=parse_qs(parsed.query),
            body=body,
            body_valid=valid,
            api_version=version,
            body_error=body_error,
        )
        try:
            status, payload = self.server.console.dispatch(request)
        except api.ApiError as exc:  # defensive: dispatch renders these itself
            status, payload = exc.status, api.render_error(version, exc)
        self._write_json(status, payload, version)

    def _read_body(self) -> tuple[dict[str, Any], bool, str | None]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}, False, api.BODY_MALFORMED
        if length <= 0:
            return {}, True, None
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return {}, False, api.BODY_MALFORMED
        except json.JSONDecodeError:
            return {}, False, api.BODY_MALFORMED
        if not isinstance(parsed, dict):
            return {}, False, api.BODY_NOT_OBJECT
        return parsed, True, None

    def _write_json(
        self, status: int, payload: dict[str, Any], version: int = api.DEFAULT_VERSION
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(api.API_VERSION_HEADER, str(version))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        # Drain any request body so a keep-alive connection starts clean; only
        # API POSTs normally carry one, but PUT/PATCH/DELETE may too.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 0:
            self.rfile.read(length)
        web_root = Path(self.server.console.web_root).resolve()
        relative = posixpath.normpath(unquote(path).lstrip("/"))
        if relative in ("", "."):
            relative = "index.html"
        target = (web_root / relative).resolve()
        if web_root != target and web_root not in target.parents:
            self._write_plain(403, "forbidden")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._write_plain(404, "not found")
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_plain(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(console: Console, host: str = "127.0.0.1", port: int = 8080) -> ControlServer:
    """Create (but do not start) the HTTP server for ``console``."""

    return ControlServer((host, port), console)


def run(console: Console, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Serve until interrupted."""

    server = serve(console, host, port)
    LOGGER.info("CraneFleet listening on http://%s:%d", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
