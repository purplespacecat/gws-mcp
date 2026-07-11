"""Google Tasks tools (Phase 2).

Reads are free; every write goes through ``approval.approve_and_execute`` with
a preview that names the exact task/list it will touch. Google Tasks has no
trash — ``delete_task`` is permanent, hence ``destructiveHint`` and a preview
that says so.
"""

from __future__ import annotations

from typing import Any

import anyio
from googleapiclient.discovery import build
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from . import approval, auth

_RO = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


def _service() -> Any:
    """A Tasks API client (auto-refreshing credentials from the keyring)."""
    return build("tasks", "v1", credentials=auth.require_credentials(), cache_discovery=False)


def _slim(task: dict) -> dict:
    """Compact task representation — drop etag/selfLink/link noise."""
    keep = ("id", "title", "status", "due", "notes", "parent", "position", "updated", "completed")
    return {k: task[k] for k in keep if task.get(k) is not None}


def _due_rfc3339(due: str) -> str:
    """Accept YYYY-MM-DD; Tasks stores date-only significance anyway."""
    return due if "T" in due else f"{due}T00:00:00.000Z"


def _get(task_list_id: str, task_id: str) -> dict:
    return _service().tasks().get(tasklist=task_list_id, task=task_id).execute()


def register(mcp: FastMCP) -> None:  # noqa: SIM117 — tool defs read better flat
    # ---------------------------------------------------------------- reads
    @mcp.tool(annotations=_RO)
    async def list_task_lists() -> list[dict]:
        """List all Google Tasks lists (id, title)."""

        def go() -> list[dict]:
            svc, items, page = _service(), [], None
            while True:
                resp = svc.tasklists().list(maxResults=100, pageToken=page).execute()
                items += resp.get("items", [])
                if not (page := resp.get("nextPageToken")):
                    return [
                        {"id": i["id"], "title": i["title"], "updated": i.get("updated")}
                        for i in items
                    ]

        return await anyio.to_thread.run_sync(go)

    @mcp.tool(annotations=_RO)
    async def list_tasks(task_list_id: str, show_completed: bool = False) -> list[dict]:
        """List tasks in a list. Set show_completed=True to include done+hidden tasks."""

        def go() -> list[dict]:
            svc, items, page = _service(), [], None
            while True:
                resp = (
                    svc.tasks()
                    .list(
                        tasklist=task_list_id,
                        maxResults=100,
                        pageToken=page,
                        showCompleted=show_completed,
                        showHidden=show_completed,
                    )
                    .execute()
                )
                items += resp.get("items", [])
                if not (page := resp.get("nextPageToken")):
                    return [_slim(t) for t in items]

        return await anyio.to_thread.run_sync(go)

    @mcp.tool(annotations=_RO)
    async def get_task(task_list_id: str, task_id: str) -> dict:
        """Fetch a single task by id."""
        return _slim(await anyio.to_thread.run_sync(_get, task_list_id, task_id))

    # --------------------------------------------------------------- writes
    @mcp.tool(annotations=_WRITE)
    async def create_task(
        task_list_id: str,
        title: str,
        ctx: Context,
        notes: str | None = None,
        due: str | None = None,
        parent: str | None = None,
    ) -> dict:
        """Create a task (due: YYYY-MM-DD; parent: id to nest under). Needs user approval."""
        body: dict = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = _due_rfc3339(due)
        preview = f"CREATE task '{title}' in list {task_list_id}"
        maybe = (f"due {due}" if due else None, f"nested under {parent}" if parent else None)
        extras = [d for d in maybe if d]
        if extras:
            preview += f" ({', '.join(extras)})"
        if notes:
            preview += f"\nnotes: {notes}"

        def go() -> dict:
            return _slim(
                _service()
                .tasks()
                .insert(tasklist=task_list_id, body=body, parent=parent)
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_WRITE)
    async def update_task(
        task_list_id: str,
        task_id: str,
        ctx: Context,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
    ) -> dict:
        """Update a task's title/notes/due (only provided fields change). Requires user approval."""
        body: dict = {}
        if title is not None:
            body["title"] = title
        if notes is not None:
            body["notes"] = notes
        if due is not None:
            body["due"] = _due_rfc3339(due)
        if not body:
            return {"status": "error", "detail": "Nothing to update — no fields provided."}
        current = await anyio.to_thread.run_sync(_get, task_list_id, task_id)
        changes = ", ".join(f"{k}: {current.get(k)!r} -> {v!r}" for k, v in body.items())
        preview = f"UPDATE task '{current.get('title')}' ({task_id}): {changes}"

        def go() -> dict:
            return _slim(
                _service().tasks().patch(tasklist=task_list_id, task=task_id, body=body).execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_WRITE)
    async def complete_task(task_list_id: str, task_id: str, ctx: Context) -> dict:
        """Mark a task completed. Requires user approval."""
        current = await anyio.to_thread.run_sync(_get, task_list_id, task_id)
        preview = f"COMPLETE task '{current.get('title')}' ({task_id})"

        def go() -> dict:
            return _slim(
                _service()
                .tasks()
                .patch(tasklist=task_list_id, task=task_id, body={"status": "completed"})
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_WRITE)
    async def move_task(
        task_list_id: str,
        task_id: str,
        ctx: Context,
        parent: str | None = None,
        previous: str | None = None,
    ) -> dict:
        """Reposition a task (parent: nest under it; previous: place after it). Needs approval."""
        current = await anyio.to_thread.run_sync(_get, task_list_id, task_id)
        where = f"under parent {parent}" if parent else "to top level"
        if previous:
            where += f", after {previous}"
        preview = f"MOVE task '{current.get('title')}' ({task_id}) {where}"

        def go() -> dict:
            return _slim(
                _service()
                .tasks()
                .move(tasklist=task_list_id, task=task_id, parent=parent, previous=previous)
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def delete_task(task_list_id: str, task_id: str, ctx: Context) -> dict:
        """PERMANENTLY delete a task (Google Tasks has no trash). Requires user approval."""
        current = await anyio.to_thread.run_sync(_get, task_list_id, task_id)
        preview = (
            f"DELETE task '{current.get('title')}' ({task_id}) from list {task_list_id} — "
            "PERMANENT, Google Tasks has no trash."
        )

        def go() -> dict:
            _service().tasks().delete(tasklist=task_list_id, task=task_id).execute()
            return {"deleted": task_id}

        return await approval.approve_and_execute(ctx, preview, go)
