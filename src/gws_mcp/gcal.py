"""Google Calendar tools (Phase 3).

Scope ceiling is ``calendar.events`` — events read/write only, no calendar
ACLs or settings (see config.py). Reads are free; every write goes through
``approval.approve_and_execute`` with a preview naming the exact event and
time. Deleting an event is recoverable from Calendar's trash for ~30 days,
but it still carries ``destructiveHint``.

File is ``gcal.py`` (not ``calendar.py``) to avoid shadowing the stdlib.
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

_RESPONSES = ("accepted", "declined", "tentative")


def _service() -> Any:
    return build("calendar", "v3", credentials=auth.require_credentials(), cache_discovery=False)


def _slim_attendee(a: dict) -> dict:
    keep = ("email", "displayName", "responseStatus", "self", "organizer", "optional")
    return {k: a[k] for k in keep if a.get(k) is not None}


def _slim_event(e: dict) -> dict:
    keep = (
        "id",
        "summary",
        "status",
        "start",
        "end",
        "location",
        "description",
        "organizer",
        "recurringEventId",
        "htmlLink",
    )
    out = {k: e[k] for k in keep if e.get(k) is not None}
    if e.get("attendees"):
        out["attendees"] = [_slim_attendee(a) for a in e["attendees"]]
    return out


def _when(value: str, time_zone: str | None) -> dict:
    """'YYYY-MM-DD' → all-day {date}; anything else → {dateTime[, timeZone]}."""
    if "T" not in value:
        return {"date": value}
    body: dict = {"dateTime": value}
    if time_zone:
        body["timeZone"] = time_zone
    return body


def _fmt_when(when: dict | None) -> str:
    if not when:
        return "?"
    return when.get("dateTime") or when.get("date") or "?"


def _get(calendar_id: str, event_id: str) -> dict:
    return _service().events().get(calendarId=calendar_id, eventId=event_id).execute()


def _list_events(
    calendar_id: str,
    time_min: str | None,
    time_max: str | None,
    query: str | None,
    max_results: int,
) -> list[dict]:
    svc, items, page = _service(), [], None
    kwargs: dict = {
        "calendarId": calendar_id,
        "singleEvents": True,  # expand recurring events into instances
        "orderBy": "startTime",
        "maxResults": min(max_results, 250),
    }
    if time_min:
        kwargs["timeMin"] = time_min if "T" in time_min else f"{time_min}T00:00:00Z"
    if time_max:
        kwargs["timeMax"] = time_max if "T" in time_max else f"{time_max}T23:59:59Z"
    if query:
        kwargs["q"] = query
    while True:
        resp = svc.events().list(pageToken=page, **kwargs).execute()
        items += resp.get("items", [])
        page = resp.get("nextPageToken")
        if not page or len(items) >= max_results:
            return [_slim_event(e) for e in items[:max_results]]


def register(mcp: FastMCP) -> None:
    # ---------------------------------------------------------------- reads
    @mcp.tool(annotations=_RO)
    async def list_calendars() -> list[dict]:
        """List the user's calendars (id, summary; 'primary' flag)."""

        def go() -> list[dict]:
            resp = _service().calendarList().list(maxResults=100).execute()
            keep = ("id", "summary", "primary", "accessRole", "timeZone")
            return [
                {k: c[k] for k in keep if c.get(k) is not None} for c in resp.get("items", [])
            ]

        return await anyio.to_thread.run_sync(go)

    @mcp.tool(annotations=_RO)
    async def list_events(
        time_min: str,
        time_max: str,
        calendar_id: str = "primary",
        max_results: int = 50,
    ) -> list[dict]:
        """List events in a time range (RFC3339 or YYYY-MM-DD). Recurring events are expanded."""
        return await anyio.to_thread.run_sync(
            _list_events, calendar_id, time_min, time_max, None, max_results
        )

    @mcp.tool(annotations=_RO)
    async def search_events(
        query: str,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 50,
    ) -> list[dict]:
        """Free-text search over events (summary, description, location, attendees)."""
        return await anyio.to_thread.run_sync(
            _list_events, calendar_id, time_min, time_max, query, max_results
        )

    @mcp.tool(annotations=_RO)
    async def get_event(event_id: str, calendar_id: str = "primary") -> dict:
        """Fetch a single event by id."""
        return _slim_event(await anyio.to_thread.run_sync(_get, calendar_id, event_id))

    # --------------------------------------------------------------- writes
    @mcp.tool(annotations=_WRITE)
    async def create_event(
        summary: str,
        start: str,
        end: str,
        ctx: Context,
        calendar_id: str = "primary",
        description: str | None = None,
        location: str | None = None,
        time_zone: str | None = None,
    ) -> dict:
        """Create an event. start/end: YYYY-MM-DD for all-day, else RFC3339;
        pass time_zone (IANA) when the dateTime has no UTC offset. Needs user approval."""
        body: dict = {
            "summary": summary,
            "start": _when(start, time_zone),
            "end": _when(end, time_zone),
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        preview = f"CREATE event '{summary}' {start} -> {end} in calendar {calendar_id}"
        if location:
            preview += f" @ {location}"

        def go() -> dict:
            return _slim_event(
                _service().events().insert(calendarId=calendar_id, body=body).execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_WRITE)
    async def update_event(
        event_id: str,
        ctx: Context,
        calendar_id: str = "primary",
        summary: str | None = None,
        description: str | None = None,
        location: str | None = None,
        start: str | None = None,
        end: str | None = None,
        time_zone: str | None = None,
    ) -> dict:
        """Update an event's fields (only provided ones change). Needs user approval."""
        body: dict = {}
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description
        if location is not None:
            body["location"] = location
        if start is not None:
            body["start"] = _when(start, time_zone)
        if end is not None:
            body["end"] = _when(end, time_zone)
        if not body:
            return {"status": "error", "detail": "Nothing to update — no fields provided."}
        current = await anyio.to_thread.run_sync(_get, calendar_id, event_id)
        old = {
            "summary": current.get("summary"),
            "description": current.get("description"),
            "location": current.get("location"),
            "start": _fmt_when(current.get("start")),
            "end": _fmt_when(current.get("end")),
        }
        changes = ", ".join(
            f"{k}: {old.get(k)!r} -> {(_fmt_when(v) if k in ('start', 'end') else v)!r}"
            for k, v in body.items()
        )
        preview = f"UPDATE event '{current.get('summary')}' ({event_id}): {changes}"

        def go() -> dict:
            return _slim_event(
                _service()
                .events()
                .patch(calendarId=calendar_id, eventId=event_id, body=body)
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_WRITE)
    async def respond_to_event(
        event_id: str,
        response: str,
        ctx: Context,
        calendar_id: str = "primary",
    ) -> dict:
        """RSVP to an invite: response = accepted | declined | tentative. Needs user approval."""
        if response not in _RESPONSES:
            return {"status": "error", "detail": f"response must be one of {_RESPONSES}"}
        current = await anyio.to_thread.run_sync(_get, calendar_id, event_id)
        attendees = current.get("attendees", [])
        if not any(a.get("self") for a in attendees):
            return {
                "status": "error",
                "detail": "You are not an attendee of this event — nothing to respond to.",
            }
        new_attendees = [
            {**a, "responseStatus": response} if a.get("self") else a for a in attendees
        ]
        preview = (
            f"RESPOND '{response}' to event '{current.get('summary')}' "
            f"({_fmt_when(current.get('start'))})"
        )

        def go() -> dict:
            return _slim_event(
                _service()
                .events()
                .patch(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body={"attendees": new_attendees},
                )
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def delete_event(event_id: str, ctx: Context, calendar_id: str = "primary") -> dict:
        """Delete an event (recoverable from Calendar trash ~30 days). Needs user approval."""
        current = await anyio.to_thread.run_sync(_get, calendar_id, event_id)
        preview = (
            f"DELETE event '{current.get('summary')}' ({_fmt_when(current.get('start'))}) "
            f"from calendar {calendar_id} — recoverable from Calendar trash for ~30 days."
        )

        def go() -> dict:
            _service().events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return {"deleted": event_id}

        return await approval.approve_and_execute(ctx, preview, go)
