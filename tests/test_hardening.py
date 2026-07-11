"""Phase 5 edge cases: unauthenticated UX, failing operations, and the
token-consumed-before-execution guarantee.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as client_session
from mcp.types import ElicitResult

from gws_mcp import approval
from gws_mcp.server import mcp


async def test_unauthenticated_tool_error_is_actionable():
    """Without credentials, a real tool must fail with the fix in the message
    (the model relays it to the user), not a bare traceback."""
    async with client_session(mcp) as client:
        result = await client.call_tool("list_task_lists", {})
    assert result.isError is True
    text = " ".join(c.text for c in result.content if hasattr(c, "text"))
    assert "gws-mcp auth" in text


def make_failing_app(counter: dict) -> FastMCP:
    app = FastMCP("failing-test")

    @app.tool()
    async def create_boom(ctx: Context) -> dict:
        def go() -> dict:
            counter["runs"] += 1
            raise RuntimeError("google api exploded")

        return await approval.approve_and_execute(ctx, "CREATE boom", go)

    @app.tool()
    async def confirm_write(confirm_token: str) -> dict:
        return await approval.confirm_pending(confirm_token)

    return app


async def call(client, tool: str, args: dict) -> dict:
    result = await client.call_tool(tool, args)
    assert result.isError is False, result.content
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


async def test_failing_operation_elicitation_mode_surfaces_error_once():
    counter = {"runs": 0}
    app = make_failing_app(counter)

    async def approve(context, params):
        return ElicitResult(action="accept", content={"confirm": True})

    async with client_session(app, elicitation_callback=approve) as client:
        result = await client.call_tool("create_boom", {})
    assert result.isError is True  # surfaced, not swallowed
    assert counter["runs"] == 1  # attempted exactly once


async def test_failing_operation_fallback_consumes_token_no_retry():
    """The token is consumed BEFORE execution: a failed operation cannot be
    re-run from the same approval — a fresh preview+approval is required."""
    counter = {"runs": 0}
    app = make_failing_app(counter)
    async with client_session(app) as client:  # fallback mode
        out = await call(client, "create_boom", {})
        token = out["confirm_token"]
        first = await client.call_tool("confirm_write", {"confirm_token": token})
        assert first.isError is True  # the failure surfaced
        assert counter["runs"] == 1
        # Same token again: rejected, NOT re-executed.
        second = await call(client, "confirm_write", {"confirm_token": token})
    assert second["status"] == "error"
    assert counter["runs"] == 1


async def test_rejected_confirm_attempts_are_audited(caplog):
    counter = {"runs": 0}
    app = make_failing_app(counter)
    with caplog.at_level("WARNING", logger="gws_mcp.audit"):
        async with client_session(app) as client:
            await call(client, "confirm_write", {"confirm_token": "guess-1"})
            await call(client, "confirm_write", {"confirm_token": "guess-2"})
    rejected = [r for r in caplog.records if "REJECTED confirm attempt" in r.message]
    assert len(rejected) == 2
    assert counter["runs"] == 0
