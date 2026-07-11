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
SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
]

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
