"""Drive tools against a fake service — query building, export-as-text,
truncation, the native-Google-file refusal, and trash-never-delete.
"""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session as client_session
from mcp.types import ElicitResult

import gws_mcp.drive as drive_mod
from gws_mcp.server import mcp

TXT_META = {
    "id": "f1",
    "name": "notes.txt",
    "mimeType": "text/plain",
    "parents": ["folderA"],
}
DOC_META = {
    "id": "d1",
    "name": "Design doc",
    "mimeType": "application/vnd.google-apps.document",
    "parents": ["folderA"],
}
FOLDER_META = {
    "id": "folderB",
    "name": "Archive",
    "mimeType": "application/vnd.google-apps.folder",
}


class _Req:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.metas = {m["id"]: m for m in (TXT_META, DOC_META, FOLDER_META)}
        self.content: bytes = b"hello world"

    def files(self):
        return self

    def list(self, **kw):
        self.calls.append(("list", kw))
        return _Req({"files": [TXT_META, DOC_META]})

    def get(self, **kw):
        self.calls.append(("get", kw))
        return _Req(self.metas[kw["fileId"]])

    def get_media(self, **kw):
        self.calls.append(("get_media", kw))
        return _Req(self.content)

    def export(self, **kw):
        self.calls.append(("export", kw))
        return _Req(b"# Exported markdown")

    def create(self, **kw):
        self.calls.append(("create", kw))
        return _Req({"id": "new1", "name": kw["body"]["name"]})

    def update(self, **kw):
        self.calls.append(("update", kw))
        return _Req({**self.metas.get(kw.get("fileId"), {}), **kw.get("body", {})})

    def named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]


def install_fake(monkeypatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(drive_mod, "_service", lambda: fake)
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
    async def test_search_builds_escaped_query(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:
            await call(client, "search_files", {"query": "bob's plan"})
        [kw] = fake.named("list")
        assert kw["q"] == "(name contains 'bob\\'s plan' or fullText contains 'bob\\'s plan')" \
            " and trashed=false"

    async def test_read_google_doc_exports_markdown(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:
            out = await call(client, "read_file_content", {"file_id": "d1"})
        [export] = fake.named("export")
        assert export["mimeType"] == "text/markdown"
        assert out["content"] == "# Exported markdown"
        assert out["exported_as"] == "text/markdown"
        assert fake.named("get_media") == []

    async def test_read_plain_file_downloads_media(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:
            out = await call(client, "read_file_content", {"file_id": "f1"})
        assert out["content"] == "hello world"
        assert out["truncated"] is False
        assert fake.named("export") == []

    async def test_read_truncates_at_max_bytes(self, monkeypatch):
        fake = install_fake(monkeypatch)
        fake.content = b"x" * 500
        async with client_session(mcp) as client:
            out = await call(client, "read_file_content", {"file_id": "f1", "max_bytes": 100})
        assert len(out["content"]) == 100
        assert out["truncated"] is True


class TestWrites:
    async def test_create_uploads_exact_content(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            out = await call(
                client,
                "create_file",
                {"name": "note.md", "content": "hello", "folder_id": "folderA"},
            )
        assert out["status"] == "executed"
        [create] = fake.named("create")
        assert create["body"] == {"name": "note.md", "parents": ["folderA"]}
        media = create["media_body"]
        assert media.getbytes(0, media.size()) == b"hello"

    async def test_update_refuses_native_google_files_before_approval(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            out = await call(
                client, "update_file_content", {"file_id": "d1", "content": "new"}
            )
        assert out["status"] == "error"
        assert "native Google file" in out["detail"]
        assert fake.named("update") == []  # refused before any write

    async def test_move_updates_parents(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            out = await call(
                client, "move_file", {"file_id": "f1", "new_folder_id": "folderB"}
            )
        assert out["status"] == "executed"
        [update] = fake.named("update")
        assert update["addParents"] == "folderB"
        assert update["removeParents"] == "folderA"
        # Both metadata reads happened (file + destination folder, for the preview).
        assert {kw["fileId"] for kw in fake.named("get")} == {"f1", "folderB"}

    async def test_trash_unapproved_never_calls_api(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp) as client:  # fallback mode, never confirmed
            out = await call(client, "trash_file", {"file_id": "f1"})
        assert out["status"] == "pending_confirmation"
        assert "notes.txt" in out["preview"] and "restorable" in out["preview"]
        assert fake.named("update") == []

    async def test_trash_approved_sets_trashed_flag(self, monkeypatch):
        fake = install_fake(monkeypatch)
        async with client_session(mcp, elicitation_callback=approve_all()) as client:
            out = await call(client, "trash_file", {"file_id": "f1"})
        assert out["status"] == "executed"
        [update] = fake.named("update")
        assert update["body"] == {"trashed": True}
