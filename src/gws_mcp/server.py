"""The MCP server (stdio).

Phase 4: auth + Google Tasks + Calendar + Drive — the full designed surface.

Conventions that MUST hold as tools are added (see CONTRIBUTING.md):
- read tools:  list_* / get_* / search_* / read_*   → ToolAnnotations(readOnlyHint=True)
- write tools: create_* / update_* / complete_* / move_* / respond_* /
  trash_* / delete_* → never execute without human approval (elicitation, or
  two-step confirm fallback via `confirm_write`), destructive ones
  additionally ToolAnnotations(destructiveHint=True).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import approval, auth, config, drive, gcal, tasks

INSTRUCTIONS = """\
Google {services} for this user's personal account.

Reads are free. Every write (create_*/update_*/complete_*/move_*/trash_*/
delete_*) requires the user's explicit approval before it executes — do not
try to bypass this; a declined approval is final for that call.

If tools fail with an authentication error, tell the user to run
`gws-mcp auth` in a terminal; check `auth_status` for details.
"""

_SERVICE_NAMES = {"tasks": "Tasks", "calendar": "Calendar", "drive": "Drive"}
_REGISTRARS = {"tasks": tasks.register, "calendar": gcal.register, "drive": drive.register}


def create_server(services: tuple[str, ...] | None = None) -> FastMCP:
    """Build the server exposing `services` (default: config.enabled_services())."""
    if services is None:
        services = config.enabled_services()
    unknown = [s for s in services if s not in _REGISTRARS]
    if unknown:
        raise ValueError(f"unknown service(s): {unknown}")

    instructions = INSTRUCTIONS.format(
        services=", ".join(_SERVICE_NAMES[s] for s in services)
    )
    server = FastMCP("gws-mcp", instructions=instructions)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def auth_status() -> dict:
        """Report Google authentication status (never contains secret material)."""
        return auth.auth_state()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def confirm_write(confirm_token: str) -> dict:
        """Execute a previously previewed write (fallback approval mode).

        Call ONLY after the user has explicitly approved the preview returned by a
        write tool. Tokens are single-use and expire after 5 minutes.
        """
        return await approval.confirm_pending(confirm_token)

    for s in services:
        _REGISTRARS[s](server)
    return server


mcp = create_server()


def run() -> None:
    """Run the server on stdio (stdout is the protocol channel — never print)."""
    mcp.run()
