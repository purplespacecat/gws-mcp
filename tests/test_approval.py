"""The write-approval layer — the acceptance criterion is that a write
without human approval is IMPOSSIBLE in both modes. These tests are the
mechanical form of that sentence; treat any weakening as a red flag.
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as client_session
from mcp.types import ElicitResult

from gws_mcp import approval


def make_app(counter: dict) -> FastMCP:
    """Tiny server with one gated write tool and the confirm_write fallback."""
    app = FastMCP("approval-test")

    @app.tool()
    async def create_thing(name: str, ctx: Context) -> dict:
        def go() -> dict:
            counter["runs"] += 1
            return {"created": name}

        return await approval.approve_and_execute(ctx, f"CREATE thing '{name}'", go)

    @app.tool()
    async def confirm_write(confirm_token: str) -> dict:
        return await approval.confirm_pending(confirm_token)

    return app


def elicit_responder(action: str, confirm: bool | None = None):
    async def cb(context, params):
        if action == "accept":
            return ElicitResult(action="accept", content={"confirm": confirm})
        return ElicitResult(action=action)

    return cb


async def call(client, tool: str, args: dict) -> dict:
    result = await client.call_tool(tool, args)
    return json.loads(result.content[0].text)


class TestElicitationMode:
    async def test_accept_executes_once(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(
            app, elicitation_callback=elicit_responder("accept", confirm=True)
        ) as client:
            out = await call(client, "create_thing", {"name": "x"})
        assert out["status"] == "executed"
        assert out["approved_via"] == "elicitation"
        assert out["result"] == {"created": "x"}
        assert counter["runs"] == 1

    async def test_decline_never_executes(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(
            app, elicitation_callback=elicit_responder("decline")
        ) as client:
            out = await call(client, "create_thing", {"name": "x"})
        assert out["status"] == "declined"
        assert counter["runs"] == 0

    async def test_accept_with_confirm_false_never_executes(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(
            app, elicitation_callback=elicit_responder("accept", confirm=False)
        ) as client:
            out = await call(client, "create_thing", {"name": "x"})
        assert out["status"] == "declined"
        assert counter["runs"] == 0

    async def test_cancel_never_executes(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(
            app, elicitation_callback=elicit_responder("cancel")
        ) as client:
            out = await call(client, "create_thing", {"name": "x"})
        assert out["status"] == "declined"
        assert counter["runs"] == 0


class TestFallbackMode:
    """No elicitation callback → the client has no elicitation capability."""

    async def test_first_call_mutates_nothing_and_issues_token(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(app) as client:
            out = await call(client, "create_thing", {"name": "x"})
        assert out["status"] == "pending_confirmation"
        assert out["preview"] == "CREATE thing 'x'"
        assert out["confirm_token"]
        assert counter["runs"] == 0  # THE invariant

    async def test_confirm_token_executes_exactly_once(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(app) as client:
            out = await call(client, "create_thing", {"name": "x"})
            token = out["confirm_token"]
            confirmed = await call(client, "confirm_write", {"confirm_token": token})
            assert confirmed["status"] == "executed"
            assert confirmed["approved_via"] == "confirm_token"
            assert counter["runs"] == 1
            # Reuse must fail without executing again.
            reused = await call(client, "confirm_write", {"confirm_token": token})
        assert reused["status"] == "error"
        assert counter["runs"] == 1

    async def test_unknown_token_is_an_error(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(app) as client:
            out = await call(client, "confirm_write", {"confirm_token": "nope"})
        assert out["status"] == "error"
        assert counter["runs"] == 0

    async def test_expired_token_is_an_error(self, monkeypatch):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(app) as client:
            out = await call(client, "create_thing", {"name": "x"})
            token = out["confirm_token"]
            # Everything is now expired.
            monkeypatch.setattr(approval, "CONFIRM_TOKEN_TTL_SECONDS", -1.0)
            confirmed = await call(client, "confirm_write", {"confirm_token": token})
        assert confirmed["status"] == "error"
        assert counter["runs"] == 0

    async def test_stash_is_bounded(self):
        counter = {"runs": 0}
        app = make_app(counter)
        async with client_session(app) as client:
            for i in range(approval._MAX_PENDING + 4):
                await call(client, "create_thing", {"name": f"n{i}"})
        assert len(approval._pending) <= approval._MAX_PENDING
        assert counter["runs"] == 0


@pytest.mark.parametrize("mode", ["elicitation-decline", "fallback-no-confirm"])
async def test_acceptance_write_without_approval_is_impossible(mode):
    """Phase 2 acceptance criterion, verbatim."""
    counter = {"runs": 0}
    app = make_app(counter)
    if mode == "elicitation-decline":
        async with client_session(
            app, elicitation_callback=elicit_responder("decline")
        ) as client:
            await call(client, "create_thing", {"name": "x"})
    else:
        async with client_session(app) as client:
            await call(client, "create_thing", {"name": "x"})
            # ... and the model "forgetting" to confirm means nothing ever runs.
    assert counter["runs"] == 0
