import json
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
import asyncpg
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

from ..config import cfg
from ..deps import AppDb, CurrentUser
from ..obsidian import build_vault
from ..dto import file_base, items_dto

router = APIRouter(tags=["export"])


class ExportOptions(BaseModel):
    scope: Literal["all", "collection", "selected"] = "all"
    collectionId: str | None = None
    itemIds: list[str] | None = None
    format: Literal["json", "markdown", "obsidian"] = "json"
    includeOriginals: bool = True
    includeExtracted: bool = True
    includeAi: bool = True
    includeComments: bool = True
    includeFiles: bool = False


def _strip(item: dict, opts: ExportOptions) -> dict:
    copy = dict(item)
    if not opts.includeOriginals:
        copy["original"] = {}
    if not opts.includeExtracted:
        copy["extracted"] = {}
    if not opts.includeAi:
        copy["ai"] = {"tags": [], "model": copy["ai"]["model"], "edited": False}
        copy["suggestedTags"] = []
    if not opts.includeComments:
        copy.pop("userComment", None)
    return copy


def _markdown(items: list[dict]) -> str:
    docs = []
    for i in items:
        lines = [
            f"# {i['title']}",
            "",
            f"- Type: {i['type']}",
            f"- Captured: {i['capturedAt']}",
            f"- Status: {i['status']}",
        ]
        if i["source"].get("url"):
            lines.append(f"- Source: {i['source']['url']}")
        if i["original"].get("text"):
            lines += ["", "## Original", "", i["original"]["text"]]
        if i["extracted"].get("text"):
            lines += ["", "## Extracted text", "", i["extracted"]["text"]]
        if i["ai"].get("annotation"):
            lines += ["", "## AI annotation", "", i["ai"]["annotation"]]
        if i.get("userComment"):
            lines += ["", "## My comment", "", i["userComment"]]
        docs.append("\n".join(lines))
    return "\n\n---\n\n".join(docs)


@router.post("/export")
async def _select_rows(conn, opts: ExportOptions):
    if opts.scope == "collection":
        if not opts.collectionId:
            raise HTTPException(422, "collectionId required")
        try:
            cid = UUID(opts.collectionId)
        except ValueError:
            raise HTTPException(422, "Invalid collection id")
        rows = await conn.fetch(
            "SELECT i.* FROM items i JOIN item_collections ic ON ic.item_id = i.id "
            "WHERE ic.collection_id = $1 ORDER BY i.captured_at",
            cid,
        )
    elif opts.scope == "selected":
        try:
            ids = [UUID(i) for i in (opts.itemIds or [])]
        except ValueError:
            raise HTTPException(422, "Invalid item id")
        rows = await conn.fetch(
            "SELECT * FROM items WHERE id = ANY($1::uuid[]) ORDER BY captured_at", ids
        )
    else:
        rows = await conn.fetch("SELECT * FROM items ORDER BY captured_at")
    return rows


@router.post("/export")
async def export_data(request: Request, opts: ExportOptions, conn: AppDb):
    if opts.format == "obsidian":
        raise HTTPException(422, "Use POST /export/obsidian")
    rows = await _select_rows(conn, opts)
    items = [
        _strip(d, opts) for d in await items_dto(conn, rows, file_base(request))
    ]
    if opts.format == "markdown":
        return PlainTextResponse(_markdown(items), media_type="text/markdown")
    payload = {
        "exportedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "items": items,
    }
    return PlainTextResponse(
        json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json"
    )


@router.get("/usage")
async def usage(conn: AppDb):
    total = await conn.fetchval("SELECT coalesce(sum(file_size), 0) FROM items")
    return {"bytesUsed": total}


def _row_for_vault(r, opts: ExportOptions) -> dict:
    d = dict(r)
    if not opts.includeAi:
        d["ai_annotation"] = None
        d["ai_tags"] = []
    if not opts.includeComments:
        d["user_comment"] = None
    if not opts.includeOriginals:
        d["original_text"] = None
    if not opts.includeExtracted:
        d["extracted_text"] = None
    return d


@router.post("/export/obsidian")
async def export_obsidian(opts: ExportOptions, conn: AppDb):
    rows = await _select_rows(conn, opts)
    if not rows:
        raise HTTPException(422, "Nothing to export")
    ids = [r["id"] for r in rows]

    cols: dict = {}
    for r in await conn.fetch(
        "SELECT ic.item_id, c.name FROM item_collections ic "
        "JOIN collections c ON c.id = ic.collection_id WHERE ic.item_id = ANY($1::uuid[])", ids
    ):
        cols.setdefault(r["item_id"], []).append(r["name"])

    tags: dict = {}
    for r in await conn.fetch(
        "SELECT it.item_id, t.name FROM item_tags it "
        "JOIN tags t ON t.id = it.tag_id WHERE it.item_id = ANY($1::uuid[])", ids
    ):
        tags.setdefault(r["item_id"], []).append(r["name"])
    if opts.includeAi:
        for r in rows:
            tags.setdefault(r["id"], []).extend(r["ai_tags"] or [])

    transcripts: dict = {}
    if opts.includeExtracted:
        try:
            for r in await conn.fetch(
                "SELECT item_id, segments FROM video_transcripts "
                "WHERE item_id = ANY($1::uuid[]) ORDER BY item_id, chunk_no", ids
            ):
                segs = r["segments"]
                if isinstance(segs, str):
                    segs = json.loads(segs)
                transcripts.setdefault(r["item_id"], []).extend(segs or [])
        except asyncpg.UndefinedTableError:
            pass  # миграция 003 не применена — vault без транскриптов

    files_dir = Path(getattr(cfg, "files_dir", Path.home() / "stash-files"))
    if opts.includeFiles:
        need = sum(r["file_size"] or 0 for r in rows)
        free = shutil.disk_usage(tempfile.gettempdir()).free
        if need * 1.2 > free:
            raise HTTPException(507, "Not enough disk space for vault export")

    tmp = Path(tempfile.mkstemp(suffix=".zip", prefix="stash-vault-")[1])
    build_vault(
        tmp, [_row_for_vault(r, opts) for r in rows],
        collections=cols, tags=tags, transcripts=transcripts, files_dir=files_dir,
        include_files=opts.includeFiles, include_extracted=opts.includeExtracted,
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return FileResponse(
        tmp, media_type="application/zip", filename=f"stash-vault-{stamp}.zip",
        background=BackgroundTask(tmp.unlink, missing_ok=True),
    )
