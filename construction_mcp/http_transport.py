"""Streamable HTTP transport, so the server can be hosted and reached by URL.

Standard library only. One endpoint (``/mcp`` by default) takes JSON-RPC messages by POST and answers with JSON;
the server is stateless, so there is no session id, no server-to-client stream (GET returns 405) and nothing to
delete. It only listens: like the stdio transport it makes no outbound connections.

Protections, because a hosted server is reachable by anyone who can reach the port:

- binds to 127.0.0.1 unless told otherwise;
- rejects browser requests whose ``Origin`` is not allowed (DNS rebinding);
- optional bearer token (``--token`` or ``CONSTRUCTION_MCP_TOKEN``);
- request bodies over 1 MB are refused, slow clients time out, and each client IP gets a request budget per minute.
"""

from __future__ import annotations

import hmac
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable
from urllib.parse import urlsplit

from . import __version__
from .protocol import INVALID_REQUEST, PARSE_ERROR, SERVER_NAME, SUPPORTED_VERSIONS, Server

MAX_BODY = 1_000_000
MAX_DRAIN = 16_000_000  # read and discard an oversized body up to this size so the client sees the 413
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class RateLimiter:
    """A fixed budget of requests per client per minute, refilled continuously."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.buckets: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def allow(self, client: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            tokens, last = self.buckets.get(client, (float(self.per_minute), now))
            tokens = min(float(self.per_minute), tokens + (now - last) * self.per_minute / 60)
            if len(self.buckets) > 10_000:  # forget idle clients rather than grow without bound
                self.buckets = {k: v for k, v in self.buckets.items() if now - v[1] < 60}
            if tokens < 1:
                self.buckets[client] = (tokens, now)
                return False
            self.buckets[client] = (tokens - 1, now)
            return True


def _origin_allowed(origin: str, allowed: set[str], loopback_bind: bool) -> bool:
    if origin in allowed or "*" in allowed:
        return True
    host = urlsplit(origin).hostname or ""
    return loopback_bind and host in LOOPBACK


def make_handler(
    server: Server,
    path: str,
    token: str | None,
    allowed_origins: set[str],
    loopback_bind: bool,
    limiter: RateLimiter,
    trust_proxy: bool,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"{SERVER_NAME}/{__version__}"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 30  # drop clients that open a connection and stall

        # ------------------------------------------------------------ plumbing

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature from the base class
            print(f"{SERVER_NAME}: {self._client()} {format % args}", file=sys.stderr)

        def _client(self) -> str:
            if trust_proxy:
                forwarded = self.headers.get("X-Forwarded-For", "")
                if forwarded:
                    return forwarded.split(",")[0].strip()
            return self.client_address[0]

        def _cors(self) -> list[tuple[str, str]]:
            origin = self.headers.get("Origin")
            if not origin:
                return []
            return [
                ("Access-Control-Allow-Origin", origin),
                ("Vary", "Origin"),
                ("Access-Control-Allow-Methods", "POST, GET, OPTIONS"),
                ("Access-Control-Allow-Headers", "Content-Type, Accept, Authorization, MCP-Protocol-Version, Mcp-Session-Id, Last-Event-ID"),
                ("Access-Control-Max-Age", "600"),
            ]

        def _send(self, status: int, body: Any = None, headers: Iterable[tuple[str, str]] = ()) -> None:
            data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            if self.command == "POST" and not getattr(self, "body_read", False):
                # unread body bytes would be parsed as the next request on this connection
                self.close_connection = True
                self.send_header("Connection", "close")
            for name, value in [*self._cors(), *headers]:
                self.send_header(name, value)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _rpc_error(self, status: int, code: int, message: str, headers: Iterable[tuple[str, str]] = ()) -> None:
            self._send(status, {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}, headers)

        def _guard(self) -> bool:
            """Origin, rate limit and token checks shared by every MCP request. False means a reply was sent."""
            origin = self.headers.get("Origin")
            if origin and not _origin_allowed(origin, allowed_origins, loopback_bind):
                self._send(403, {"error": "origin not allowed"})
                return False
            if not limiter.allow(self._client()):
                self._send(429, {"error": "too many requests"}, [("Retry-After", "10")])
                return False
            if token:
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                    self._rpc_error(401, INVALID_REQUEST, "missing or wrong bearer token", [("WWW-Authenticate", 'Bearer realm="construction-mcp"')])
                    return False
            return True

        def _route(self) -> str:
            return urlsplit(self.path).path.rstrip("/") or "/"

        # ------------------------------------------------------------ methods

        def do_OPTIONS(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
            origin = self.headers.get("Origin")
            if origin and not _origin_allowed(origin, allowed_origins, loopback_bind):
                self._send(403, {"error": "origin not allowed"})
                return
            self._send(204)

        def do_GET(self) -> None:  # noqa: N802
            route = self._route()
            if route == "/health":
                self._send(200, {"status": "ok", "name": SERVER_NAME, "version": __version__})
            elif route == path:
                # stateless server: no server-to-client stream to offer
                if self._guard():
                    self._send(405, {"error": "this server does not open an event stream; POST JSON-RPC messages"}, [("Allow", "POST, OPTIONS")])
            elif route == "/":
                self._send(200, {"name": SERVER_NAME, "version": __version__, "mcp_endpoint": path, "transport": "streamable-http"})
            else:
                self._send(404, {"error": "not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            if self._route() != path:
                self._send(404, {"error": "not found"})
            elif self._guard():
                self._send(405, {"error": "no sessions to end"}, [("Allow", "POST, OPTIONS")])

        def do_POST(self) -> None:  # noqa: N802
            self.body_read = False  # one handler serves every request on a kept-alive connection
            if self._route() != path:
                self._send(404, {"error": "not found"})
                return
            if not self._guard():
                return
            version = self.headers.get("MCP-Protocol-Version")
            if version and version not in SUPPORTED_VERSIONS:
                self._rpc_error(400, INVALID_REQUEST, f"unsupported MCP-Protocol-Version {version!r}; supported: {', '.join(SUPPORTED_VERSIONS)}")
                return
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self._send(415, {"error": "Content-Type must be application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._send(411, {"error": "Content-Length required"})
                return
            if length < 0 or length > MAX_BODY:
                if 0 < length <= MAX_DRAIN:
                    while length > 0:
                        chunk = self.rfile.read(min(length, 65536))
                        if not chunk:
                            break
                        length -= len(chunk)
                self._send(413, {"error": f"body larger than {MAX_BODY} bytes"})
                return
            raw = self.rfile.read(length)
            self.body_read = True
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._rpc_error(400, PARSE_ERROR, "parse error")
                return
            reply = server.handle(message)
            if reply is None:
                self._send(202)  # notifications and responses need no body
            else:
                self._send(200, reply)

    return Handler


def serve_http(
    server: Server,
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
    token: str | None = None,
    allowed_origins: Iterable[str] = (),
    rate_limit_per_minute: int = 120,
    trust_proxy: bool = False,
    ready: threading.Event | None = None,
    holder: dict[str, Any] | None = None,
) -> None:
    path = "/" + path.strip("/")
    handler = make_handler(server, path, token or None, set(allowed_origins), host in LOOPBACK, RateLimiter(rate_limit_per_minute), trust_proxy)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    bound_host, bound_port = httpd.server_address[:2]
    if holder is not None:
        holder["httpd"], holder["port"] = httpd, bound_port
    if host not in LOOPBACK and not token:
        print(f"{SERVER_NAME}: warning: listening on {host} with no token; anyone who can reach this port can call the tools", file=sys.stderr)
    print(f"{SERVER_NAME}: listening on http://{bound_host}:{bound_port}{path}", file=sys.stderr, flush=True)
    if ready is not None:
        ready.set()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
