"""Server-level checks: tool inventory and the naming/annotation contract."""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session as client_session

from gws_mcp.server import mcp

READ_PREFIXES = ("list_", "get_", "search_", "read_", "auth_")
WRITE_PREFIXES = (
    "create_",
    "update_",
    "complete_",
    "move_",
    "respond_",
    "trash_",
    "delete_",
    "confirm_",
)

ALL_TOOLS = {
    "auth_status",
    "confirm_write",
    # Tasks
    "list_task_lists",
    "list_tasks",
    "get_task",
    "create_task",
    "update_task",
    "complete_task",
    "move_task",
    "delete_task",
    # Calendar
    "list_calendars",
    "list_events",
    "search_events",
    "get_event",
    "create_event",
    "update_event",
    "respond_to_event",
    "delete_event",
    # Drive
    "search_files",
    "get_file_metadata",
    "read_file_content",
    "create_file",
    "update_file_content",
    "move_file",
    "trash_file",
}


async def test_tool_inventory_is_the_full_designed_surface():
    async with client_session(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == ALL_TOOLS


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


async def test_deletes_are_flagged_destructive():
    async with client_session(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert tools["delete_task"].annotations.destructiveHint is True
    assert tools["delete_event"].annotations.destructiveHint is True
    assert tools["trash_file"].annotations.destructiveHint is True


async def test_drive_has_no_hard_delete():
    """Design rule: Drive removal is trash-only."""
    async with client_session(mcp) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "delete_file" not in names and "trash_file" in names


CORE_TOOLS = {"auth_status", "confirm_write"}
TASKS_TOOLS = {
    "list_task_lists",
    "list_tasks",
    "get_task",
    "create_task",
    "update_task",
    "complete_task",
    "move_task",
    "delete_task",
}


async def test_service_filter_tasks_only():
    """create_server(("tasks",)) exposes core tools + Tasks tools, nothing else."""
    from gws_mcp.server import create_server

    async with client_session(create_server(("tasks",))) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == CORE_TOOLS | TASKS_TOOLS


async def test_service_filter_defaults_to_full_surface():
    from gws_mcp.server import create_server

    async with client_session(create_server()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == ALL_TOOLS


async def test_service_filter_rejects_unknown_service():
    import pytest

    from gws_mcp.server import create_server

    with pytest.raises(ValueError, match="gmail"):
        create_server(("tasks", "gmail"))


def test_enabled_services_env_parsing(monkeypatch):
    """GWS_MCP_SERVICES is a comma-separated, whitespace-tolerant subset."""
    from gws_mcp import config

    monkeypatch.delenv("GWS_MCP_SERVICES", raising=False)
    assert config.enabled_services() == ("tasks", "calendar", "drive")

    monkeypatch.setenv("GWS_MCP_SERVICES", "tasks, calendar")
    assert config.enabled_services() == ("tasks", "calendar")

    monkeypatch.setenv("GWS_MCP_SERVICES", "tasks")
    assert config.enabled_services() == ("tasks",)


async def test_auth_status_roundtrip_unauthenticated():
    async with client_session(mcp) as client:
        result = await client.call_tool("auth_status", {})
        assert result.isError is False
        state = json.loads(result.content[0].text)
        assert state["authenticated"] is False
        assert "gws-mcp auth" in state["hint"]
