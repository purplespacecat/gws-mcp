"""Google Drive tools (Phase 4).

Reads are free; writes are approval-gated. Two Drive-specific rules:
- **Trash, never delete**: the only removal tool is ``trash_file`` (restorable
  from Drive's trash for ~30 days). There is deliberately no hard delete.
- **Native Google files export as text**: ``read_file_content`` exports Docs
  as markdown, Sheets as CSV, Slides as plain text. Content updates to native
  Google files are refused (silent format conversion would be a lie in the
  approval preview) — edit those in their editors.
"""

from __future__ import annotations

from typing import Any

import anyio
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from . import approval, auth

_RO = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

_META_FIELDS = "id,name,mimeType,size,modifiedTime,parents,trashed,webViewLink"
_GOOGLE_APPS = "application/vnd.google-apps"
_EXPORT_AS = {
    f"{_GOOGLE_APPS}.document": "text/markdown",
    f"{_GOOGLE_APPS}.spreadsheet": "text/csv",
    f"{_GOOGLE_APPS}.presentation": "text/plain",
}


def _service() -> Any:
    return build("drive", "v3", credentials=auth.require_credentials(), cache_discovery=False)


def _get_meta(file_id: str) -> dict:
    return _service().files().get(fileId=file_id, fields=_META_FIELDS).execute()


def _escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("'", "\\'")


def register(mcp: FastMCP) -> None:
    # ---------------------------------------------------------------- reads
    @mcp.tool(annotations=_RO)
    async def search_files(
        query: str,
        folder_id: str | None = None,
        mime_type: str | None = None,
        include_trashed: bool = False,
        max_results: int = 25,
    ) -> list[dict]:
        """Search Drive by name/content text. Optional folder and MIME-type filters."""

        def go() -> list[dict]:
            text = _escape(query)
            terms = [f"(name contains '{text}' or fullText contains '{text}')"]
            if not include_trashed:
                terms.append("trashed=false")
            if folder_id:
                terms.append(f"'{_escape(folder_id)}' in parents")
            if mime_type:
                terms.append(f"mimeType='{_escape(mime_type)}'")
            resp = (
                _service()
                .files()
                .list(
                    q=" and ".join(terms),
                    pageSize=min(max_results, 100),
                    fields=f"nextPageToken, files({_META_FIELDS})",
                )
                .execute()
            )
            return resp.get("files", [])[:max_results]

        return await anyio.to_thread.run_sync(go)

    @mcp.tool(annotations=_RO)
    async def get_file_metadata(file_id: str) -> dict:
        """Fetch a file's metadata (name, MIME type, size, parents, link)."""
        return await anyio.to_thread.run_sync(_get_meta, file_id)

    @mcp.tool(annotations=_RO)
    async def read_file_content(file_id: str, max_bytes: int = 100_000) -> dict:
        """Read a file's content as text. Google Docs export as markdown,
        Sheets as CSV, Slides as plain text; other files must be text-like."""

        def go() -> dict:
            svc = _service()
            meta = svc.files().get(fileId=file_id, fields=_META_FIELDS).execute()
            mime = meta.get("mimeType", "")
            exported_as = None
            if mime.startswith(_GOOGLE_APPS):
                exported_as = _EXPORT_AS.get(mime)
                if exported_as is None:
                    return {
                        "status": "error",
                        "detail": f"Native Google file type {mime} has no text export.",
                    }
                data = svc.files().export(fileId=file_id, mimeType=exported_as).execute()
            else:
                data = svc.files().get_media(fileId=file_id).execute()
            if isinstance(data, str):
                data = data.encode()
            text = data[:max_bytes].decode("utf-8", errors="replace")
            out = {
                "name": meta.get("name"),
                "mimeType": mime,
                "content": text,
                "truncated": len(data) > max_bytes,
            }
            if exported_as:
                out["exported_as"] = exported_as
            return out

        return await anyio.to_thread.run_sync(go)

    # --------------------------------------------------------------- writes
    @mcp.tool(annotations=_WRITE)
    async def create_file(
        name: str,
        content: str,
        ctx: Context,
        folder_id: str | None = None,
        mime_type: str = "text/plain",
    ) -> dict:
        """Create a file with the given text content. Needs user approval."""
        preview = f"CREATE file '{name}' ({mime_type}, {len(content)} chars)"
        if folder_id:
            preview += f" in folder {folder_id}"

        def go() -> dict:
            body: dict = {"name": name}
            if folder_id:
                body["parents"] = [folder_id]
            media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime_type)
            return (
                _service()
                .files()
                .create(body=body, media_body=media, fields=_META_FIELDS)
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_WRITE)
    async def update_file_content(file_id: str, content: str, ctx: Context) -> dict:
        """Replace a file's content (regular files only, not native Google
        Docs/Sheets — edit those in their editors). Needs user approval."""
        meta = await anyio.to_thread.run_sync(_get_meta, file_id)
        mime = meta.get("mimeType", "")
        if mime.startswith(_GOOGLE_APPS):
            return {
                "status": "error",
                "detail": f"'{meta.get('name')}' is a native Google file ({mime}) — "
                "content updates via upload would silently convert formats. Refused.",
            }
        preview = (
            f"REPLACE content of file '{meta.get('name')}' ({file_id}, {mime}) "
            f"with {len(content)} chars of new content — the old content is overwritten."
        )

        def go() -> dict:
            media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime)
            return (
                _service()
                .files()
                .update(fileId=file_id, media_body=media, fields=_META_FIELDS)
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_WRITE)
    async def move_file(file_id: str, new_folder_id: str, ctx: Context) -> dict:
        """Move a file to another folder. Needs user approval."""
        meta = await anyio.to_thread.run_sync(_get_meta, file_id)
        dest = await anyio.to_thread.run_sync(_get_meta, new_folder_id)
        old_parents = ",".join(meta.get("parents", []))
        preview = (
            f"MOVE file '{meta.get('name')}' ({file_id}) to folder "
            f"'{dest.get('name')}' ({new_folder_id})"
        )

        def go() -> dict:
            return (
                _service()
                .files()
                .update(
                    fileId=file_id,
                    addParents=new_folder_id,
                    removeParents=old_parents,
                    fields=_META_FIELDS,
                )
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)

    @mcp.tool(annotations=_DESTRUCTIVE)
    async def trash_file(file_id: str, ctx: Context) -> dict:
        """Move a file to Drive's trash (restorable ~30 days; there is no hard
        delete in this server). Needs user approval."""
        meta = await anyio.to_thread.run_sync(_get_meta, file_id)
        preview = (
            f"TRASH file '{meta.get('name')}' ({file_id}) — restorable from Drive's "
            "trash for ~30 days."
        )

        def go() -> dict:
            return (
                _service()
                .files()
                .update(fileId=file_id, body={"trashed": True}, fields=_META_FIELDS)
                .execute()
            )

        return await approval.approve_and_execute(ctx, preview, go)
