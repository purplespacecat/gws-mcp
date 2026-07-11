"""Calendar tools against a fake Google service — through the real server,
covering time conversion, RSVP self-attendee logic, and approval gating.
"""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session as client_session
from mcp.types import ElicitResult

import gws_mcp.gcal as gcal_mod
from gws_mcp.server import mcp

EVENT = {
    "id": "e1",
    "summary": "Dentist",
    "status": "confirmed",
    "start": {"dateTime": "2026-07-15T10:00:00+02:00"},
    "end": {"dateTime": "2026-07-15T11:00:00+02:00"},
    "etag": "noise",
    "iCalUID": "noise",
    "attendees": [
        {"email": "organizer@example.com", "responseStatus": "accepted", "organizer": True},
        {"email": "me@example.com", "responseStatus": "needsAction", "self": True},
    ],
}


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def calendarList(self):  # noqa: N802 — mirrors googleapiclient
        return self

    def events(self):
        return self

    def list(self, **kw):
        self.calls.append(("list", kw))
        if "calendarId" in kw:
            return _Req({"items": [EVENT]})
        return _Req({"items": [{"id": "primary", "summary": "Me", "primary": True}]})

    def get(self, **kw):
        self.calls.append(("get", kw))
        return _Req(EVENT)

    def insert(self, **kw):
        self.calls.append(("insert", kw))
        return _Req({"id": "new1", **kw["body"], "status": "confirmed"})

    def patch(self, **kw):
        self.calls.append(("patch", kw))
        return _Req({**EVENT, **kw["body"]})

    def delete(self, **kw):
        self.calls.append(("delete", kw))
        return _Req("")

    def named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]


def install_fake(monkeypatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(gcal_mod, "_service", lambda: fake)
    return fake


async def call(client, tool: str, args: dict) -> dict | list:
    result = await client.call_tool(tool, args)
    assert result.isError is False, result.content
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def approve_all():
    async def cb(context, params):
        return ElicitResult(action="accept", content={"confirm": True})

    return cb


class TestReads:
    async def test_list_events_expands_recurring_and_slims(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:
            out = await call(
                client,
                "list_events",
                {"time_min": "2026-07-15", "time_max": "2026-07-16"},
            )
        [kw] = fake.named("list")
        assert kw["singleEvents"] is True and kw["orderBy"] == "startTime"
        # Date-only bounds become full-day RFC3339 bounds.
        assert kw["timeMin"] == "2026-07-15T00:00:00Z"
        assert kw["timeMax"] == "2026-07-16T23:59:59Z"
        assert out[0]["summary"] == "Dentist"
        assert "etag" not in out[0] and "iCalUID" not in out[0]

    async def test_search_passes_query(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:
            await call(client, "search_events", {"query": "dentist"})
        [kw] = fake.named("list")
        assert kw["q"] == "dentist"


class TestWrites:
    async def test_create_all_day_vs_timed(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            await call(
                client,
                "create_event",
                {"summary": "Trip", "start": "2026-08-01", "end": "2026-08-03"},
            )
            await call(
                client,
                "create_event",
                {
                    "summary": "Standup",
                    "start": "2026-08-01T09:00:00",
                    "end": "2026-08-01T09:15:00",
                    "time_zone": "Europe/Warsaw",
                },
            )
        all_day, timed = fake.named("insert")
        assert all_day["body"]["start"] == {"date": "2026-08-01"}
        assert timed["body"]["start"] == {
            "dateTime": "2026-08-01T09:00:00",
            "timeZone": "Europe/Warsaw",
        }

    async def test_respond_patches_only_self_attendee(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            out = await call(
                client, "respond_to_event", {"event_id": "e1", "response": "accepted"}
            )
        assert out["status"] == "executed"
        [patch] = fake.named("patch")
        by_email = {a["email"]: a["responseStatus"] for a in patch["body"]["attendees"]}
        assert by_email["me@example.com"] == "accepted"
        assert by_email["organizer@example.com"] == "accepted"  # untouched value

    async def test_respond_rejects_bad_response_without_api_call(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            out = await call(
                client, "respond_to_event", {"event_id": "e1", "response": "maybe"}
            )
        assert out["status"] == "error"
        assert fake.calls == []

    async def test_delete_unapproved_never_calls_api(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:  # fallback mode, never confirmed
            out = await call(client, "delete_event", {"event_id": "e1"})
        assert out["status"] == "pending_confirmation"
        assert "Dentist" in out["preview"]
        assert fake.named("delete") == []
