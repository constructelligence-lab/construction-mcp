import argparse
import os

from . import __version__
from .prompts import PROMPTS, get_prompt
from .protocol import Server
from .catalog import HANDLERS, RESOURCES, TOOLS


def main(argv: "list[str] | None" = None) -> None:
    parser = argparse.ArgumentParser(
        prog="construction-mcp",
        description="Offline construction reference MCP server. Speaks stdio by default; --http hosts it at a URL.",
    )
    env = os.environ.get
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--http", action="store_true", default=env("CONSTRUCTION_MCP_HTTP") == "1", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default=env("HOST", "127.0.0.1"), help="address to listen on (default 127.0.0.1; use 0.0.0.0 in a container)")
    parser.add_argument("--port", type=int, default=int(env("PORT", "8000")), help="port (default 8000, or $PORT)")
    parser.add_argument("--path", default=env("CONSTRUCTION_MCP_PATH", "/mcp"), help="MCP endpoint path (default /mcp)")
    parser.add_argument("--token", default=env("CONSTRUCTION_MCP_TOKEN"), help="require 'Authorization: Bearer <token>' (or $CONSTRUCTION_MCP_TOKEN)")
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[o for o in env("CONSTRUCTION_MCP_ALLOWED_ORIGINS", "").split(",") if o],
        help="browser Origin to accept; repeat for more. Requests without an Origin header are not affected",
    )
    parser.add_argument("--rate-limit", type=int, default=int(env("CONSTRUCTION_MCP_RATE_LIMIT", "120")), help="requests per minute per client IP, 0 for none (default 120)")
    parser.add_argument("--trust-proxy", action="store_true", default=env("CONSTRUCTION_MCP_TRUST_PROXY") == "1", help="take the client IP from X-Forwarded-For (only behind a proxy you run)")
    args = parser.parse_args(argv)

    server = Server(TOOLS, HANDLERS, RESOURCES, PROMPTS, get_prompt)
    if not args.http:
        server.serve()
        return
    from .http_transport import serve_http

    serve_http(
        server,
        host=args.host,
        port=args.port,
        path=args.path,
        token=args.token,
        allowed_origins=args.allowed_origin,
        rate_limit_per_minute=args.rate_limit,
        trust_proxy=args.trust_proxy,
    )


if __name__ == "__main__":
    main()
