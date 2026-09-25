"""A minimal Model Context Protocol server over stdio.

Standard library only. It speaks JSON-RPC 2.0, one message per line on stdin and stdout, and
implements what a small read-only server needs: ``initialize``, ``ping``, ``tools/list``,
``tools/call``, ``resources/list`` and ``resources/read``. It opens no network port and makes no outbound connections: the only process that
can talk to it is the one that launched it.

Logging goes to stderr, never stdout, because stdout carries the protocol.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO

from . import __version__

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_NAME = "construction-reference"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
RESOURCE_NOT_FOUND = -32002

INSTRUCTIONS = (
    "Offline construction reference data and calculators. Every tool is read-only and works from "
    "data bundled with the server; nothing is fetched from the network. Results are planning "
    "values and general reference, not project-specific advice: say so when you use them, and "
    "tell the user to verify quantities, factors and prices against their own data."
)


class ToolError(Exception):
    """A problem with the tool's input that the model can correct and retry."""


class Server:
    def __init__(
        self,
        tools: list[dict[str, Any]],
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
        resources: list[dict[str, Any]] | None = None,
    ):
        self.tools = tools
        self.handlers = handlers
        # each resource: uri, name, title, description, mimeType, and a "read" callable returning text
        self.resources = {r["uri"]: r for r in (resources or [])}

    # ------------------------------------------------------------------ dispatch

    def handle(self, message: Any) -> Any:
        """Handle one decoded message (or batch). Returns the response, or None for notifications."""
        if isinstance(message, list):
            replies = [r for r in (self.handle(m) for m in message) if r is not None]
            return replies or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
                return None  # a response to a request we never send; ignore
            return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "invalid JSON-RPC request")

        method, params = message["method"], message.get("params") or {}
        is_notification = "id" not in message
        if is_notification:
            return None  # notifications/initialized, notifications/cancelled and so on need no reply

        request_id = message["id"]
        try:
            if method == "initialize":
                return _result(request_id, self._initialize(params))
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": self.tools})
            if method == "tools/call":
                return self._call(request_id, params)
            if method == "resources/list":
                listed = [{k: v for k, v in r.items() if k != "read"} for r in self.resources.values()]
                return _result(request_id, {"resources": listed})
            if method == "resources/templates/list":
                return _result(request_id, {"resourceTemplates": []})
            if method == "resources/read":
                resource = self.resources.get(params.get("uri"))
                if resource is None:
                    return _error(request_id, RESOURCE_NOT_FOUND, f"resource not found: {params.get('uri')}")
                content = {"uri": resource["uri"], "mimeType": resource["mimeType"], "text": resource["read"]()}
                return _result(request_id, {"contents": [content]})
            return _error(request_id, METHOD_NOT_FOUND, f"method not found: {method}")
        except Exception as exc:  # never let one bad request kill the server
            print(f"{SERVER_NAME}: internal error on {method}: {exc!r}", file=sys.stderr)
            return _error(request_id, INTERNAL_ERROR, "internal error")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, **({"resources": {"listChanged": False}} if self.resources else {})},
            "serverInfo": {"name": SERVER_NAME, "title": "Construction reference", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in self.handlers:
            return _error(request_id, INVALID_PARAMS, f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            return _error(request_id, INVALID_PARAMS, "arguments must be an object")
        try:
            data = self.handlers[name](arguments)
        except ToolError as exc:
            # tool-level errors go back as a result, so the model sees them and can correct its call
            return _result(request_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(data, indent=2, ensure_ascii=False)}],
                "structuredContent": data,
                "isError": False,
            },
        )

    # ------------------------------------------------------------------ transport

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                reply: Any = _error(None, PARSE_ERROR, "parse error")
            else:
                reply = self.handle(message)
            if reply is not None:
                stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
                stdout.flush()


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
