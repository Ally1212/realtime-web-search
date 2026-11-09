"""Standalone loopback gateway for the versioned Google research namespace.

This gateway intentionally does not touch the legacy web handler; it binds a
small HTTPServer on 127.0.0.1 only and delegates every /v1/google request to
``google_http.handle``.  Startup initializes only the independent ``sf_google_*``
schema (idempotent CREATE TABLE IF NOT EXISTS); no adapter, scheduler or
collector is started by this process.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlparse

from .google_api import GoogleDomainService
from .google_http import handle
from .google_runtime import configure_google_service


def config_from_env():
    return SimpleNamespace(database_url=os.environ["DATABASE_URL"])


def build(service=None):
    if service is None:
        service = GoogleDomainService(os.environ["DATABASE_URL"])
        service.initialize()
        configure_google_service(service)
    return service


class Handler(BaseHTTPRequestHandler):
    config = config_from_env()

    def send_body(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status=200) -> None:
        self.send_body(
            json.dumps(payload, ensure_ascii=False, default=str).encode(),
            "application/json; charset=utf-8", status,
        )

    def _google(self, method: str) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self.send_body(b"ok", "text/plain")
            return
        if path == "/v1/google" or path.startswith("/v1/google/"):
            handle(self, method)
            return
        self.send_json({"code": "unsupported", "error": "Method not supported"}, 405)

    def do_GET(self) -> None:  # noqa: N802
        self._google("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._google("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._google("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._google("DELETE")

    def log_message(self, format, *args) -> None:  # noqa: A002
        pass


def serve() -> None:
    build()
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "18096"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Google research gateway listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
