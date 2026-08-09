"""Static configuration: OAuth scopes, credential paths, keyring coordinates.

The scope set is a deliberate ceiling (see CONTRIBUTING.md) — do not add
scopes here without a discussion issue first.
"""

from __future__ import annotations

import os
from pathlib import Path

# Read-write for all three services; Calendar is limited to *events*
# (no calendar ACLs/settings). Ordering is stable — it is user-visible in
# `auth_status` output and in the OAuth consent screen.
SCOPES_BY_SERVICE = {
    "tasks": "https://www.googleapis.com/auth/tasks",
    "calendar": "https://www.googleapis.com/auth/calendar.events",
    "drive": "https://www.googleapis.com/auth/drive",
}
SCOPES = list(SCOPES_BY_SERVICE.values())


def scopes() -> list[str]:
    """Scopes actually requested/held: the ceiling narrowed to enabled services.

    A tasks-only install (GWS_MCP_SERVICES=tasks) never requests the
    restricted `drive` scope. Widening services later requires re-running
    `gws-mcp auth` — the stored grant only covers what was consented to.
    """
    return [SCOPES_BY_SERVICE[s] for s in enabled_services()]

# Services the server can expose, in registration order.
KNOWN_SERVICES = ("tasks", "calendar", "drive")


def enabled_services() -> tuple[str, ...]:
    """Services to expose, from GWS_MCP_SERVICES (comma-separated subset of
    KNOWN_SERVICES); defaults to all. Lets one install serve e.g. only Tasks
    alongside Google's official remote MCP servers for the rest."""
    env = os.environ.get("GWS_MCP_SERVICES")
    if not env:
        return KNOWN_SERVICES
    requested = tuple(s.strip() for s in env.split(",") if s.strip())
    unknown = [s for s in requested if s not in KNOWN_SERVICES]
    if unknown:
        raise ValueError(
            f"GWS_MCP_SERVICES contains unknown service(s) {unknown}; "
            f"known: {', '.join(KNOWN_SERVICES)}"
        )
    return requested


# OS keyring coordinates for the OAuth token (never stored on disk).
KEYRING_SERVICE = "gws-mcp"
KEYRING_USERNAME = "oauth-token"


def credentials_path() -> Path:
    """Path to the user's own Google OAuth client file (BYO credentials).

    Overridable via GWS_MCP_CREDENTIALS; defaults to
    ``$XDG_CONFIG_HOME/gws-mcp/credentials.json`` (``~/.config`` fallback).
    This file identifies the OAuth *client*, not the user grant — the grant
    (refresh token) lives in the OS keyring only.
    """
    if env := os.environ.get("GWS_MCP_CREDENTIALS"):
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "~/.config")
    return Path(xdg).expanduser() / "gws-mcp" / "credentials.json"
