"""Server-level checks: tool inventory and the naming/annotation contract."""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session as client_session

from gws_mcp.server import mcp

READ_PREFIXES = ("list_", "get_", "search_", "read_", "auth_")
WRITE_PREFIXES = ("create_", "update_", "complete_", "move_", "trash_", "delete_", "confirm_")

PHASE2_TOOLS = {
    "auth_status",
    "confirm_write",
    "list_task_lists",
    "list_tasks",
    "get_task",
    "create_task",
    "update_task",
    "complete_task",
    "move_task",
    "delete_task",
}


async def test_phase2_tool_inventory():
    async with client_session(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == PHASE2_TOOLS


async def test_naming_convention_matches_annotations():
    """CONTRIBUTING.md's naming contract, enforced mechanically: every tool is
    classified by prefix, reads carry readOnlyHint, writes must not."""
    async with client_session(mcp) as client:
        tools = (await client.list_tools()).tools
    for t in tools:
        is_read = t.name.startswith(READ_PREFIXES)
        is_write = t.name.startswith(WRITE_PREFIXES)
        assert is_read != is_write, f"unclassifiable tool name: {t.name}"
        if is_read:
            assert t.annotations and t.annotations.readOnlyHint is True, t.name
        else:
            assert t.annotations and not t.annotations.readOnlyHint, t.name


async def test_delete_task_is_flagged_destructive():
    async with client_session(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["delete_task"].annotations.destructiveHint is True


async def test_auth_status_roundtrip_unauthenticated():
    async with client_session(mcp) as client:
        result = await client.call_tool("auth_status", {})
        assert result.isError is False
        state = json.loads(result.content[0].text)
        assert state["authenticated"] is False
        assert "gws-mcp auth" in state["hint"]
