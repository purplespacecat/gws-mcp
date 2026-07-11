"""The write-approval layer — the security core of this server.

Every mutating tool routes execution through ``approve_and_execute()``. The
operation closure runs ONLY after a human said yes, via one of two modes:

1. **Elicitation (primary)** — the client supports MCP elicitation: a preview
   is sent through the client UI and the human accepts or declines there. The
   approval bypasses the model entirely — it cannot approve its own writes.
2. **Two-step fallback** — no elicitation capability: the first call stashes
   the operation and returns the preview plus a one-time confirm token;
   nothing mutates. ``confirm_pending(token)`` (exposed as the
   ``confirm_write`` tool) executes it. Tokens are single-use, expire after
   ``CONFIRM_TOKEN_TTL_SECONDS``, and the stash is bounded.

Every executed or declined write is logged to stderr (audit trail — stdout is
the MCP protocol channel).
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import anyio
from mcp.server.fastmcp import Context
from mcp.types import ClientCapabilities, ElicitationCapability
from pydantic import BaseModel, Field

audit = logging.getLogger("gws_mcp.audit")

CONFIRM_TOKEN_TTL_SECONDS = 300.0
_MAX_PENDING = 16


class _PendingWrite:
    __slots__ = ("description", "execute", "created")

    def __init__(self, description: str, execute: Callable[[], Any]) -> None:
        self.description = description
        self.execute = execute
        self.created = time.monotonic()


_pending: OrderedDict[str, _PendingWrite] = OrderedDict()


class ConfirmWrite(BaseModel):
    """Elicitation form shown to the human."""

    confirm: bool = Field(description="Approve this write to your Google account?")


def _client_supports_elicitation(ctx: Context) -> bool:
    return ctx.session.check_client_capability(
        ClientCapabilities(elicitation=ElicitationCapability())
    )


def _prune_expired() -> None:
    now = time.monotonic()
    for token in [
        t for t, p in _pending.items() if now - p.created > CONFIRM_TOKEN_TTL_SECONDS
    ]:
        del _pending[token]


async def _run(execute: Callable[[], Any]) -> Any:
    """Run the (blocking, Google-API-calling) closure off the event loop."""
    return await anyio.to_thread.run_sync(execute)


async def approve_and_execute(
    ctx: Context, description: str, execute: Callable[[], Any]
) -> dict:
    """Gate ``execute`` behind human approval. The ONLY path to a mutation.

    ``description`` must be a complete, human-readable preview of exactly what
    will change — it is what the human approves.
    """
    if _client_supports_elicitation(ctx):
        result = await ctx.elicit(
            message=f"Google write approval requested:\n\n{description}", schema=ConfirmWrite
        )
        if result.action == "accept" and result.data is not None and result.data.confirm:
            outcome = await _run(execute)
            audit.info("EXECUTED (elicitation-approved): %s", description)
            return {"status": "executed", "approved_via": "elicitation", "result": outcome}
        audit.info("NOT EXECUTED (user %s): %s", result.action, description)
        return {
            "status": "declined",
            "preview": description,
            "detail": "The user did not approve this write. It was not executed. "
            "Do not retry unless the user explicitly asks again.",
        }

    # Fallback: two-step confirm token. Nothing mutates on this call.
    _prune_expired()
    while len(_pending) >= _MAX_PENDING:
        _pending.popitem(last=False)
    token = secrets.token_urlsafe(12)
    _pending[token] = _PendingWrite(description, execute)
    audit.info("PENDING (token issued): %s", description)
    return {
        "status": "pending_confirmation",
        "preview": description,
        "confirm_token": token,
        "instructions": "Nothing was written. Show the preview to the user verbatim and ask "
        "for approval. Call confirm_write with this token ONLY after the user has explicitly "
        "said yes. The token is single-use and expires in 5 minutes. If the user declines, "
        "simply do not call confirm_write.",
    }


async def confirm_pending(confirm_token: str) -> dict:
    """Execute a stashed write. Backs the ``confirm_write`` tool."""
    _prune_expired()
    pending = _pending.pop(confirm_token, None)
    if pending is None:
        return {
            "status": "error",
            "detail": "Unknown, already-used, or expired confirm token. Nothing was "
            "executed. Re-run the write tool to get a fresh preview and token.",
        }
    outcome = await _run(pending.execute)
    audit.info("EXECUTED (token-confirmed): %s", pending.description)
    return {"status": "executed", "approved_via": "confirm_token", "result": outcome}
