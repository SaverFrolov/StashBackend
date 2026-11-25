import asyncio
import base64
import binascii
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import cfg
from ..deps import AppDb, CurrentUser, parse_uuid
from ..dto import file_base, items_dto, one_item_dto
from ..pipeline import queue

router = APIRouter(prefix="/items", tags=["items"])

ATTENTION = ("partial", "extraction_failed", "ai_failed")
URL_RE = re.compile(r"https?://[^\s]+")


class SaveTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    comment: str | None = None
    collectionIds: list[str] = []


class SaveLinkIn(BaseModel):
    url: str = Field(max_length=4000)
    comment: str | None = None
    collectionIds: list[str] = []


class FilePayload(BaseModel):
    uri: str
    name: str = Field(min_length=1, max_length=500)
    mimeType: str = ""
    size: int | None = None


class SaveFileIn(BaseModel):
    file: FilePayload
    comment: str | None = None
    collectionIds: list[str] = []


class SaveAudioIn(BaseModel):
    blob: FilePayload
    durationMs: int = 0
    comment: str | None = None


class ItemPatch(BaseModel):
    title: str | None = None
    userComment: str | None = None
    aiAnnotation: str | None = None
    aiEdited: bool | None = None
    restoreAi: bool | None = None
    userTags: list[str] | None = None
    acceptSuggestedTag: str | None = None
    removeSuggestedTag: str | None = None
    collectionIds: list[str] | None = None
    lang: str | None = None


class DeleteIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


def type_from_mime(mime: str, name: str) -> str:
    n = name.lower()
    if mime == "application/pdf" or (not mime and n.endswith(".pdf")):
        return "pdf"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ) or (not mime and (n.endswith(".xlsx") or n.endswith(".xls"))):
        return "xlsx"
    if not mime:
        if re.search(r"\.(mp3|wav|ogg|m4a|flac|aac)$", n):
            return "audio"
        if re.search(r"\.(mp4|webm|mov|m4v|mkv)$", n):
            return "video"
    return "docx"


def guess_lang(text: str) -> str | None:
    if re.search(r"[а-яА-ЯёЁ]", text):
        return "ru"
    if re.search(r"[a-zA-Z]", text):
        return "en"
    return None


def title_from_text(text: str) -> str:
    first = text.split("\n")[0].strip()
    return first[:80].rstrip() + "…" if len(first) > 80 else first


async def _owned_collections(conn, ids: list[str]) -> list[UUID]:
    if not ids:
        return []
    try:
        uuids = list(dict.fromkeys(UUID(i) for i in ids))
    except ValueError:
        raise HTTPException(422, "Invalid collection id")
    rows = await conn.fetch(
        "SELECT id FROM collections WHERE id = ANY($1::uuid[])", uuids
    )
    if len(rows) != len(uuids):
        raise HTTPException(404, "Collection not found")
    return uuids


async def _attach_collections(conn, item_id, user_id, ids: list[UUID]):
    for cid in ids:
        await conn.execute(
            "INSERT INTO item_collections (item_id, collection_id, user_id) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            item_id,
            cid,
            user_id,
        )


async def _set_user_tags(conn, user_id, item_id, names: list[str]):
    names = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    await conn.execute("DELETE FROM item_tags WHERE item_id = $1", item_id)
    for name in names:
        row = await conn.fetchrow(
            "INSERT INTO tags (user_id, name, origin) VALUES ($1, $2, 'user') "
            "ON CONFLICT (user_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            user_id,
            name,
        )
        await conn.execute(
            "INSERT INTO item_tags (item_id, tag_id, user_id) VALUES ($1, $2, $3) "
            "ON CONFLICT DO NOTHING",
            item_id,
            row["id"],
            user_id,
        )


async def _start_processing(conn, user_id, item_id, steps=("extract", "annotate")):
    for name in steps:
        await conn.execute(
            "INSERT INTO processing_steps (item_id, user_id, name, status) "
            "VALUES ($1, $2, $3, 'pending')",
            item_id,
            user_id,
            name,
        )
    queue.enqueue(item_id, user_id, steps)


async def _auto_process(conn, user_id, item_id):
    row = await conn.fetchrow(
        "SELECT auto_process FROM ai_settings WHERE user_id = $1", user_id
    )
    if row is None or row["auto_process"]:
        await _start_processing(conn, user_id, item_id)


async def _insert(conn, user_id, type_, title, **kw):
    return await conn.fetchrow(
        "INSERT INTO items (user_id, type, title, lang, source, original_text, "
        "file_name, file_size, file_mime, file_path, user_comment) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *",
        user_id,
        type_,
        title,
        kw.get("lang"),
        kw.get("source") or {},
        kw.get("original_text"),
        kw.get("file_name"),
        kw.get("file_size"),
        kw.get("file_mime"),
        kw.get("file_path"),
        kw.get("user_comment"),
    )


async def _save_upload(conn, user, payload: FilePayload, type_: str, comment, collection_ids):
    if not payload.uri.startswith("data:"):
        raise HTTPException(422, "Only data-URL uploads are supported")
    _, _, b64 = payload.uri.partition(",")
    try:
        # shortcut: whole file lives in RAM (frontend sends base64 JSON); add a multipart endpoint when multi-GB uploads become real
        data = await asyncio.to_thread(base64.b64decode, b64, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Invalid file data")
    name = Path(payload.name).name
    if name in ("", ".", ".."):
        raise HTTPException(422, "Invalid file name")
    cols = await _owned_collections(conn, collection_ids)
    row = await _insert(
        conn,
        user["id"],
        type_,
        payload.name,
        source={"name": payload.name} if type_ != "audio" else None,
        file_name=name,
        file_size=len(data),
        file_mime=payload.mimeType or None,
        user_comment=comment,
    )
    dest = cfg.files_dir / str(user["id"]) / str(row["id"]) / name
    try:
        await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, data)
    except OSError:
        await conn.execute("DELETE FROM items WHERE id = $1", row["id"])
        raise HTTPException(507, "Could not store the file")
    await conn.execute(
        "UPDATE items SET file_path = $1 WHERE id = $2", str(dest), row["id"]
    )
    row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", row["id"])
    await _attach_collections(conn, row["id"], user["id"], cols)
    await _auto_process(conn, user["id"], row["id"])
    return row


@router.get("")
async def list_items(
    request: Request,
    conn: AppDb,
    cursor: str | None = None,
    limit: int = 20,
    filter: str = "all",
):
    limit = max(1, min(limit, 100))
    where: list[str] = []
    params: list = []
    if filter == "attention":
        where.append(f"status = ANY(${len(params) + 1}::text[])")
        params.append(list(ATTENTION))
    elif filter == "file":
        where.append(f"type = ANY(${len(params) + 1}::text[])")
        params.append(["pdf", "docx", "xlsx", "video"])
    elif filter in ("pdf", "docx"):
        where.append(f"type = ANY(${len(params) + 1}::text[])")
        params.append(["pdf", "docx"])
    elif filter in ("link", "text", "audio", "video", "xlsx"):
        where.append(f"type = ${len(params) + 1}")
        params.append(filter)
    elif filter != "all":
        raise HTTPException(422, "Unknown filter")
    filter_sql = (" WHERE " + " AND ".join(where)) if where else ""
    total = await conn.fetchval(f"SELECT count(*) FROM items{filter_sql}", *params)
    if cursor:
        try:
            ts = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "Invalid cursor")
        params.append(ts)
        where.append(f"captured_at < ${len(params)}")
    page_sql = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(limit + 1)
    rows = await conn.fetch(
        f"SELECT * FROM items{page_sql} ORDER BY captured_at DESC, id DESC LIMIT ${len(params)}",
        *params,
    )
    has_more = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    return {
        "items": await items_dto(conn, rows, file_base(request)),
        "hasMore": has_more,
        "total": total,
    }


@router.get("/attention-count")
async def attention_count(conn: AppDb):
    count = await conn.fetchval(
        "SELECT count(*) FROM items WHERE status = ANY($1::text[])", list(ATTENTION)
    )
    return {"count": count}


@router.post("/text")
async def save_text(request: Request, body: SaveTextIn, conn: AppDb, user: CurrentUser):
    text = body.text.strip()
    urls = URL_RE.findall(text)
    if len(urls) == 1:
        rest = text.replace(urls[0], "").strip()
        comment = " ".join(x for x in (rest, body.comment) if x) or None
        return await _save_link(request, conn, user, urls[0], comment, body.collectionIds)
    cols = await _owned_collections(conn, body.collectionIds)
    row = await _insert(
        conn,
        user["id"],
        "text",
        title_from_text(text),
        lang=guess_lang(text),
        original_text=text,
        user_comment=body.comment,
    )
    await _attach_collections(conn, row["id"], user["id"], cols)
    await _auto_process(conn, user["id"], row["id"])
    return await one_item_dto(conn, row, file_base(request))


async def _save_link(request, conn, user, url: str, comment, collection_ids):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(422, "Invalid URL")
    cols = await _owned_collections(conn, collection_ids)
    row = await _insert(
        conn,
        user["id"],
        "link",
        url,
        source={"url": url, "name": parsed.netloc},
        user_comment=comment,
    )
    await _attach_collections(conn, row["id"], user["id"], cols)
    await _auto_process(conn, user["id"], row["id"])
    return await one_item_dto(conn, row, file_base(request))


@router.post("/link")
async def save_link(request: Request, body: SaveLinkIn, conn: AppDb, user: CurrentUser):
    return await _save_link(request, conn, user, body.url.strip(), body.comment, body.collectionIds)


@router.post("/file")
async def save_file(request: Request, body: SaveFileIn, conn: AppDb, user: CurrentUser):
    type_ = type_from_mime(body.file.mimeType, body.file.name)
    row = await _save_upload(conn, user, body.file, type_, body.comment, body.collectionIds)
    return await one_item_dto(conn, row, file_base(request))


@router.post("/audio")
async def save_audio(request: Request, body: SaveAudioIn, conn: AppDb, user: CurrentUser):
    row = await _save_upload(conn, user, body.blob, "audio", body.comment, [])
    return await one_item_dto(conn, row, file_base(request))


@router.get("/{item_id}")
async def get_item(request: Request, item_id: str, conn: AppDb):
    row = await conn.fetchrow(
        "SELECT * FROM items WHERE id = $1", parse_uuid(item_id)
    )
    if row is None:
        raise HTTPException(404, "Item not found")
    return await one_item_dto(conn, row, file_base(request))


@router.patch("/{item_id}")
async def patch_item(
    request: Request, item_id: str, body: ItemPatch, conn: AppDb, user: CurrentUser
):
    iid = parse_uuid(item_id)
    row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", iid)
    if row is None:
        raise HTTPException(404, "Item not found")
    fields = body.model_fields_set
    updates: dict = {}
    if "title" in fields and body.title is not None:
        updates["title"] = body.title
    if "userComment" in fields:
        updates["user_comment"] = body.userComment
    if "lang" in fields:
        updates["lang"] = body.lang
    if "aiAnnotation" in fields and body.aiAnnotation is not None:
        updates["ai_annotation"] = body.aiAnnotation
        updates["ai_edited"] = body.aiEdited if body.aiEdited is not None else True
    if body.restoreAi and row["ai_original_annotation"] is not None:
        updates["ai_annotation"] = row["ai_original_annotation"]
        updates["ai_edited"] = False
    if body.removeSuggestedTag:
        updates["ai_tags"] = [
            t for t in row["ai_tags"] if t != body.removeSuggestedTag
        ]
    if updates:
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
        await conn.execute(
            f"UPDATE items SET {sets}, updated_at = now() WHERE id = $1",
            iid,
            *updates.values(),
        )
    if body.acceptSuggestedTag:
        tag = body.acceptSuggestedTag.strip()
        await conn.execute(
            "UPDATE items SET ai_tags = array_remove(ai_tags, $2), updated_at = now() WHERE id = $1",
            iid,
            tag,
        )
        current = await conn.fetch(
            "SELECT t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id WHERE it.item_id = $1",
            iid,
        )
        await _set_user_tags(
            conn, user["id"], iid, [r["name"] for r in current] + [tag]
        )
    if body.userTags is not None:
        await _set_user_tags(conn, user["id"], iid, body.userTags)
    if body.collectionIds is not None:
        cols = await _owned_collections(conn, body.collectionIds)
        await conn.execute("DELETE FROM item_collections WHERE item_id = $1", iid)
        await _attach_collections(conn, iid, user["id"], cols)
    row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", iid)
    return await one_item_dto(conn, row, file_base(request))


@router.delete("", status_code=204)
async def delete_items(body: DeleteIn, conn: AppDb, user: CurrentUser):
    try:
        ids = [UUID(i) for i in body.ids]
    except ValueError:
        raise HTTPException(422, "Invalid item id")
    rows = await conn.fetch(
        "SELECT id, file_path FROM items WHERE id = ANY($1::uuid[])", ids
    )
    await conn.execute("DELETE FROM items WHERE id = ANY($1::uuid[])", ids)
    for r in rows:
        if r["file_path"]:
            shutil.rmtree(Path(r["file_path"]).parent, ignore_errors=True)
    return None


@router.post("/{item_id}/reprocess")
async def reprocess(request: Request, item_id: str, conn: AppDb, user: CurrentUser):
    iid = parse_uuid(item_id)
    row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", iid)
    if row is None:
        raise HTTPException(404, "Item not found")
    await conn.execute(
        "UPDATE items SET ai_annotation = NULL, ai_original_annotation = NULL, "
        "ai_tags = '{}', ai_model = NULL, ai_generated_at = NULL, ai_edited = false, "
        "status = 'annotating', updated_at = now() WHERE id = $1",
        iid,
    )
    await conn.execute(
        "DELETE FROM processing_steps WHERE item_id = $1 AND name = 'annotate'", iid
    )
    await _start_processing(conn, user["id"], iid, steps=("annotate",))
    row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", iid)
    return await one_item_dto(conn, row, file_base(request))


@router.post("/{item_id}/retry-extraction")
async def retry_extraction(request: Request, item_id: str, conn: AppDb, user: CurrentUser):
    iid = parse_uuid(item_id)
    row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", iid)
    if row is None:
        raise HTTPException(404, "Item not found")
    await conn.execute(
        "UPDATE items SET status = 'extracting', extracted_error = NULL, updated_at = now() WHERE id = $1",
        iid,
    )
    await conn.execute("DELETE FROM processing_steps WHERE item_id = $1", iid)
    await _start_processing(conn, user["id"], iid)
    row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", iid)
    return await one_item_dto(conn, row, file_base(request))


@router.get("/{item_id}/related")
async def related(request: Request, item_id: str, conn: AppDb, limit: int = 5):
    iid = parse_uuid(item_id)
    limit = max(1, min(limit, 20))
    src = await conn.fetchval(
        "SELECT avg(embedding)::text FROM item_chunks WHERE item_id = $1", iid
    )
    if src is None:
        return []
    rows = await conn.fetch(
        "SELECT item_id FROM (SELECT DISTINCT ON (c.item_id) c.item_id, "
        "c.embedding <=> $2::vector AS dist FROM item_chunks c "
        "WHERE c.item_id <> $1 ORDER BY c.item_id, dist) t ORDER BY dist LIMIT $3",
        iid,
        src,
        limit,
    )
    if not rows:
        return []
    items = await conn.fetch(
        "SELECT * FROM items WHERE id = ANY($1::uuid[])", [r["item_id"] for r in rows]
    )
    order = {r["item_id"]: i for i, r in enumerate(rows)}
    items.sort(key=lambda r: order[r["id"]])
    return await items_dto(conn, items, file_base(request))
