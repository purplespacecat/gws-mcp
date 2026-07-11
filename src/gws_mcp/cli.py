"""CLI entry point: `gws-mcp` serves (stdio); `gws-mcp auth` authenticates."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gws-mcp",
        description="MCP server for Google Tasks/Calendar/Drive "
        "(run with no arguments to serve on stdio).",
    )
    sub = parser.add_subparsers(dest="command")
    auth_p = sub.add_parser(
        "auth", help="Interactive Google OAuth flow (opens a browser); token → OS keyring."
    )
    group = auth_p.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="print auth status and exit")
    group.add_argument("--clear", action="store_true", help="remove the stored token")
    args = parser.parse_args(argv)

    if args.command == "auth":
        from . import auth

        if args.status:
            print(json.dumps(auth.auth_state(), indent=2))
            return 0
        if args.clear:
            removed = auth.clear_credentials()
            print("Token removed from keyring." if removed else "No token was stored.")
            return 0
        try:
            auth.run_auth_flow()
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        print("Authenticated. Token stored in the OS keyring (nothing written to disk).")
        return 0

    # Default: run the MCP server on stdio.
    from .server import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
