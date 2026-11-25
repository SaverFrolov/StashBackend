from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..deps import AppDb, CurrentUser, parse_uuid
from ..dto import tag_dto

router = APIRouter(prefix="/tags", tags=["tags"])


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class MergeIn(BaseModel):
    sourceIds: list[str] = Field(min_length=1, max_length=100)
    targetId: str


async def _get(conn, tid: UUID):
    row = await conn.fetchrow("SELECT * FROM tags WHERE id = $1", tid)
    if row is None:
        raise HTTPException(404, "Tag not found")
    return row


@router.get("")
async def list_tags(conn: AppDb):
    rows = await conn.fetch("SELECT * FROM tags ORDER BY name")
    return [tag_dto(r) for r in rows]


@router.patch("/{tid}")
async def rename_tag(tid: str, body: RenameIn, conn: AppDb):
    uuid = parse_uuid(tid)
    tag = await _get(conn, uuid)
    name = body.name.strip()
    try:
        await conn.execute("UPDATE tags SET name = $2 WHERE id = $1", uuid, name)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "Tag already exists")
    await conn.execute(
        "UPDATE items SET ai_tags = array_replace(ai_tags, $1, $2), updated_at = now() "
        "WHERE $1 = ANY(ai_tags)",
        tag["name"],
        name,
    )
    return tag_dto(await _get(conn, uuid))


@router.post("/merge")
async def merge_tags(body: MergeIn, conn: AppDb, user: CurrentUser):
    target_id = parse_uuid(body.targetId)
    target = await _get(conn, target_id)
    try:
        source_ids = [UUID(i) for i in body.sourceIds if i != body.targetId]
    except ValueError:
        raise HTTPException(422, "Invalid tag id")
    sources = await conn.fetch(
        "SELECT * FROM tags WHERE id = ANY($1::uuid[])", source_ids
    )
    for src in sources:
        await conn.execute(
            "UPDATE items SET ai_tags = array_replace(ai_tags, $1, $2), updated_at = now() "
            "WHERE $1 = ANY(ai_tags)",
            src["name"],
            target["name"],
        )
    await conn.execute(
        "INSERT INTO item_tags (item_id, tag_id, user_id) "
        "SELECT item_id, $2, user_id FROM item_tags WHERE tag_id = ANY($1::uuid[]) "
        "ON CONFLICT DO NOTHING",
        source_ids,
        target_id,
    )
    await conn.execute("DELETE FROM tags WHERE id = ANY($1::uuid[])", source_ids)
    return tag_dto(target)


@router.delete("/{tid}", status_code=204)
async def delete_tag(tid: str, conn: AppDb):
    uuid = parse_uuid(tid)
    tag = await _get(conn, uuid)
    await conn.execute(
        "UPDATE items SET ai_tags = array_remove(ai_tags, $1), updated_at = now() "
        "WHERE $1 = ANY(ai_tags)",
        tag["name"],
    )
    await conn.execute("DELETE FROM tags WHERE id = $1", uuid)
    return None
