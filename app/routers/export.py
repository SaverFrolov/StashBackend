import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..deps import AppDb, CurrentUser
from ..dto import file_base, items_dto

router = APIRouter(tags=["export"])


class ExportOptions(BaseModel):
    scope: Literal["all", "collection", "selected"] = "all"
    collectionId: str | None = None
    itemIds: list[str] | None = None
    format: Literal["json", "markdown"] = "json"
    includeOriginals: bool = True
    includeExtracted: bool = True
    includeAi: bool = True
    includeComments: bool = True


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
async def export_data(request: Request, opts: ExportOptions, conn: AppDb):
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
