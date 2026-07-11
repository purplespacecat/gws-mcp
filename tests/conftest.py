"""Shared fixtures. The OS keyring is always mocked — tests must never touch
the real Secret Service, and must pass on headless CI with no backend at all.
"""

from __future__ import annotations

import keyring
import keyring.errors
import pytest


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch) -> dict:
    """Replace the keyring module's API with an in-memory dict."""
    store: dict[tuple[str, str], str] = {}

    def _get(service: str, username: str) -> str | None:
        return store.get((service, username))

    def _set(service: str, username: str, value: str) -> None:
        store[(service, username)] = value

    def _delete(service: str, username: str) -> None:
        if (service, username) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, username)]

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "set_password", _set)
    monkeypatch.setattr(keyring, "delete_password", _delete)
    return store
