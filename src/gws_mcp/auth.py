"""Google OAuth: installed-app flow, keyring persistence, lazy refresh.

Invariants:
- Tokens are stored in the OS keyring only — never plaintext on disk.
- Nothing in this module logs, prints, or returns secret material;
  ``auth_state()`` is safe to hand to an LLM verbatim.
- The interactive flow (browser) runs only from the CLI (`gws-mcp auth`),
  never from inside an MCP session.
"""

from __future__ import annotations

import json
from datetime import UTC

import keyring
import keyring.errors
from google.oauth2.credentials import Credentials

from .config import KEYRING_SERVICE, KEYRING_USERNAME, SCOPES, credentials_path


class NotAuthenticatedError(RuntimeError):
    """A Google client was requested before `gws-mcp auth` was run."""


def save_credentials(creds: Credentials) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, creds.to_json())


def clear_credentials() -> bool:
    """Remove the stored grant. Returns False if nothing was stored."""
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        return False
    return True


def _stored_credentials() -> Credentials | None:
    raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    if raw is None:
        return None
    try:
        return Credentials.from_authorized_user_info(json.loads(raw), scopes=SCOPES)
    except (ValueError, KeyError):
        return None  # unreadable/legacy blob — treat as unauthenticated


def load_credentials() -> Credentials | None:
    """Usable credentials from the keyring, or None.

    No eager refresh here (that would mean network I/O on every load): the
    Google API client transport refreshes automatically at request time using
    the stored refresh token. "Usable" = currently valid OR refreshable.
    """
    creds = _stored_credentials()
    if creds is None:
        return None
    if creds.valid or creds.refresh_token:
        return creds
    return None


def require_credentials() -> Credentials:
    creds = load_credentials()
    if creds is None:
        raise NotAuthenticatedError(
            "Not authenticated with Google. Run `gws-mcp auth` in a terminal first."
        )
    return creds


def run_auth_flow() -> Credentials:
    """Interactive installed-app OAuth flow (opens a browser). CLI-only."""
    # Heavy import, only needed for the one-off interactive flow.
    from google_auth_oauthlib.flow import InstalledAppFlow

    path = credentials_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"OAuth client file not found: {path}\n"
            "Create a Google Cloud OAuth 'Desktop app' client (Tasks, Calendar and "
            "Drive APIs enabled), download its JSON there (chmod 600), or point "
            "GWS_MCP_CREDENTIALS at it."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(path), scopes=SCOPES)
    creds = flow.run_local_server(port=0)
    save_credentials(creds)
    return creds


def auth_state() -> dict:
    """Status report for humans/LLMs. Contains no secret material."""
    state: dict = {
        "authenticated": False,
        "credentials_file": str(credentials_path()),
        "credentials_file_present": credentials_path().is_file(),
        "token_storage": f"OS keyring (service '{KEYRING_SERVICE}')",
        "scopes": SCOPES,
    }
    creds = _stored_credentials()
    if creds is None:
        state["hint"] = "Run `gws-mcp auth` in a terminal to authenticate."
        return state
    refreshable = bool(creds.refresh_token)
    state["authenticated"] = creds.valid or refreshable
    state["token_expired"] = bool(creds.expired)
    state["can_refresh"] = refreshable
    if creds.expiry is not None:
        state["token_expiry"] = creds.expiry.replace(tzinfo=UTC).isoformat()
    if not state["authenticated"]:
        state["hint"] = "Stored token is unusable. Re-run `gws-mcp auth`."
    return state
