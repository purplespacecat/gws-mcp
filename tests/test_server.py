"""Server smoke test: a real MCP client session against the in-process server."""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session as client_session

from gws_mcp.server import mcp


async def test_lists_exactly_the_phase1_tools():
    async with client_session(mcp._mcp_server) as client:
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == ["auth_status"]
        # The read/write convention is machine-checkable from day one.
        assert tools[0].annotations.readOnlyHint is True


async def test_auth_status_roundtrip_unauthenticated():
    async with client_session(mcp._mcp_server) as client:
        result = await client.call_tool("auth_status", {})
        assert result.isError is False
        state = json.loads(result.content[0].text)
        assert state["authenticated"] is False
        assert "gws-mcp auth" in state["hint"]
