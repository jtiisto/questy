"""CLI for Quest Diagnostics MCP server."""

import argparse
import os
import sys
from pathlib import Path

from .config import MCPConfig

DEFAULT_DB_PATH = Path.home() / ".questy" / "questy.db"


def resolve_db_path(args) -> str:
    if args.database:
        return args.database
    return os.environ.get("QUESTY_DB_PATH", str(DEFAULT_DB_PATH))


def cmd_server(args):
    """Start MCP server."""
    from .server import create_mcp_server

    db_path = Path(resolve_db_path(args)).resolve()

    if not db_path.exists():
        print(f"Error: Database not found: {db_path}", file=sys.stderr)
        print("Run 'questy-import' first to import PDF reports.", file=sys.stderr)
        sys.exit(1)

    config = MCPConfig(
        db_path=db_path,
        max_rows=args.max_rows,
        strict_validation=True,
        transport=args.transport,
    )
    config.validate()

    mcp_server = create_mcp_server(config)
    mcp_server.run()


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="questy-mcp",
        description="Quest Diagnostics MCP Server - Read-only access to lab data",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    server_parser = subparsers.add_parser("server", help="Start MCP server")
    server_parser.add_argument(
        "--database", "-d", type=str,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})",
    )
    server_parser.add_argument(
        "--max-rows", type=int, default=1000,
        help="Maximum rows per query (default: 1000)",
    )
    server_parser.add_argument(
        "--transport", choices=["stdio", "http", "sse"], default="stdio",
        help="Transport protocol (default: stdio)",
    )
    server_parser.set_defaults(func=cmd_server)

    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()
    args.func(args)
