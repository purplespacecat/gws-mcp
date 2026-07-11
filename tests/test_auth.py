"""Auth module: keyring roundtrip, state reporting, and the no-secret-leak rule."""

from __future__ import annotations

import json

import pytest
from google.oauth2.credentials import Credentials

from gws_mcp import auth
from gws_mcp.config import SCOPES

# Distinctive markers so leak assertions can't false-positive.
ACCESS_TOKEN = "leakcheck-access-token"
REFRESH_TOKEN = "leakcheck-refresh-token"
CLIENT_SECRET = "leakcheck-client-secret"


def make_creds() -> Credentials:
    return Credentials(
        token=ACCESS_TOKEN,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id.apps.googleusercontent.com",
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )


class TestUnauthenticated:
    def test_load_returns_none(self):
        assert auth.load_credentials() is None

    def test_require_raises_with_actionable_message(self):
        with pytest.raises(auth.NotAuthenticatedError, match="gws-mcp auth"):
            auth.require_credentials()

    def test_state_reports_unauthenticated_with_hint(self):
        state = auth.auth_state()
        assert state["authenticated"] is False
        assert "gws-mcp auth" in state["hint"]
        assert state["scopes"] == SCOPES

    def test_clear_without_token_is_safe(self):
        assert auth.clear_credentials() is False


class TestStoredToken:
    def test_save_load_roundtrip(self, memory_keyring):
        auth.save_credentials(make_creds())
        assert len(memory_keyring) == 1  # in the (mocked) keyring, nowhere else
        loaded = auth.load_credentials()
        assert loaded is not None
        # The refresh token is the durable grant — that's what must survive
        # the roundtrip (access tokens are re-minted lazily at request time).
        assert loaded.refresh_token == REFRESH_TOKEN
        assert set(SCOPES) == set(loaded.scopes)

    def test_state_reports_authenticated(self):
        auth.save_credentials(make_creds())
        state = auth.auth_state()
        assert state["authenticated"] is True
        assert state["can_refresh"] is True

    def test_state_never_leaks_secret_material(self):
        auth.save_credentials(make_creds())
        serialized = json.dumps(auth.auth_state())
        assert "leakcheck-" not in serialized

    def test_clear_removes_token(self, memory_keyring):
        auth.save_credentials(make_creds())
        assert auth.clear_credentials() is True
        assert memory_keyring == {}
        assert auth.load_credentials() is None

    def test_corrupt_blob_treated_as_unauthenticated(self, memory_keyring):
        memory_keyring[("gws-mcp", "oauth-token")] = "not json {"
        assert auth.load_credentials() is None
        assert auth.auth_state()["authenticated"] is False
