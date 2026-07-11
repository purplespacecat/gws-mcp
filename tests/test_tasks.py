"""Tasks tools against a fake Google service — full path through the real
server, including approval gating and response slimming.
"""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session as client_session
from mcp.types import ElicitResult

import gws_mcp.tasks as tasks_mod
from gws_mcp.server import mcp

TASK = {
    "id": "t1",
    "title": "Buy milk",
    "status": "needsAction",
    "etag": "noise-etag",
    "selfLink": "https://noise",
    "position": "0001",
}


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeService:
    """Minimal stand-in for googleapiclient's Tasks service. Records calls."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def tasklists(self):
        return self

    def tasks(self):
        return self

    def list(self, **kw):
        self.calls.append(("list", kw))
        if "tasklist" in kw:
            return _Req({"items": [TASK]})
        return _Req({"items": [{"id": "l1", "title": "Home 🏡", "updated": "2026-07-11"}]})

    def get(self, **kw):
        self.calls.append(("get", kw))
        return _Req(TASK)

    def insert(self, **kw):
        self.calls.append(("insert", kw))
        return _Req({"id": "new1", **kw["body"], "status": "needsAction"})

    def patch(self, **kw):
        self.calls.append(("patch", kw))
        return _Req({**TASK, **kw["body"]})

    def move(self, **kw):
        self.calls.append(("move", kw))
        return _Req(TASK)

    def delete(self, **kw):
        self.calls.append(("delete", kw))
        return _Req("")

    def named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]


def install_fake(monkeypatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(tasks_mod, "_service", lambda: fake)
    return fake


async def call(client, tool: str, args: dict) -> dict | list:
    result = await client.call_tool(tool, args)
    assert result.isError is False, result.content
    # dict-returning tools: structuredContent IS the payload; list-returning
    # tools: the SDK wraps the payload as {"result": [...]}.
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


def approve_all():
    async def cb(context, params):
        return ElicitResult(action="accept", content={"confirm": True})

    return cb


def decline_all():
    async def cb(context, params):
        return ElicitResult(action="decline")

    return cb


class TestReads:
    async def test_list_task_lists(self, monkeypatch):
        install_fake(monkeypatch)
        async with client_session(mcp) as client:
            out = await call(client, "list_task_lists", {})
        assert out == [{"id": "l1", "title": "Home 🏡", "updated": "2026-07-11"}]

    async def test_list_tasks_slims_noise(self, monkeypatch):
        install_fake(monkeypatch)
        async with client_session(mcp) as client:
            out = await call(client, "list_tasks", {"task_list_id": "l1"})
        assert out[0]["title"] == "Buy milk"
        assert "etag" not in out[0] and "selfLink" not in out[0]


class TestWritesElicitation:
    async def test_create_task_approved(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            out = await call(
                client,
                "create_task",
                {"task_list_id": "l1", "title": "New task", "due": "2026-07-12"},
            )
        assert out["status"] == "executed"
        [insert] = fake.named("insert")
        assert insert["body"] == {"title": "New task", "due": "2026-07-12T00:00:00.000Z"}

    async def test_create_task_declined_never_hits_api(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=decline_all()) as client:
            out = await call(client, "create_task", {"task_list_id": "l1", "title": "New task"})
        assert out["status"] == "declined"
        assert fake.named("insert") == []

    async def test_delete_preview_names_the_task_and_warns_permanent(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=decline_all()) as client:
            out = await call(client, "delete_task", {"task_list_id": "l1", "task_id": "t1"})
        # The read-before-write happened (for the preview), the delete did not.
        assert "Buy milk" in out["preview"] and "PERMANENT" in out["preview"]
        assert fake.named("get") and fake.named("delete") == []


class TestWritesFallback:
    async def test_delete_via_confirm_token_end_to_end(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:  # no elicitation capability
            out = await call(client, "delete_task", {"task_list_id": "l1", "task_id": "t1"})
            assert out["status"] == "pending_confirmation"
            assert fake.named("delete") == []
            confirmed = await call(
                client, "confirm_write", {"confirm_token": out["confirm_token"]}
            )
        assert confirmed["status"] == "executed"
        [deleted] = fake.named("delete")
        assert deleted == {"tasklist": "l1", "task": "t1"}
