from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..deps import AppDb, CurrentUser, parse_uuid
from ..dto import collection_dto

router = APIRouter(prefix="/collections", tags=["collections"])


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ItemsIn(BaseModel):
    itemIds: list[str] = Field(min_length=1, max_length=500)


async def _get(conn, cid: UUID):
    row = await conn.fetchrow("SELECT * FROM collections WHERE id = $1", cid)
    if row is None:
        raise HTTPException(404, "Collection not found")
    return row


@router.get("")
async def list_collections(conn: AppDb):
    rows = await conn.fetch("SELECT * FROM collections ORDER BY created_at")
    return [collection_dto(r) for r in rows]


@router.post("")
async def create_collection(body: CollectionIn, conn: AppDb, user: CurrentUser):
    try:
        row = await conn.fetchrow(
            "INSERT INTO collections (user_id, name, description) VALUES ($1, $2, $3) RETURNING *",
            user["id"],
            body.name.strip(),
            body.description.strip() if body.description else None,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "Collection already exists")
    return collection_dto(row)


@router.get("/{cid}")
async def get_collection(cid: str, conn: AppDb):
    return collection_dto(await _get(conn, parse_uuid(cid)))


@router.patch("/{cid}")
async def patch_collection(cid: str, body: CollectionPatch, conn: AppDb):
    uuid = parse_uuid(cid)
    await _get(conn, uuid)
    updates = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if "description" in body.model_fields_set:
        updates["description"] = body.description.strip() if body.description else None
    if updates:
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
        try:
            await conn.execute(
                f"UPDATE collections SET {sets}, updated_at = now() WHERE id = $1",
                uuid,
                *updates.values(),
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "Collection already exists")
    return collection_dto(await _get(conn, uuid))


@router.delete("/{cid}", status_code=204)
async def delete_collection(cid: str, conn: AppDb):
    await conn.execute("DELETE FROM collections WHERE id = $1", parse_uuid(cid))
    return None


async def _owned_items(conn, ids: list[str]) -> list[UUID]:
    try:
        uuids = list(dict.fromkeys(UUID(i) for i in ids))
    except ValueError:
        raise HTTPException(422, "Invalid item id")
    rows = await conn.fetch("SELECT id FROM items WHERE id = ANY($1::uuid[])", uuids)
    if len(rows) != len(uuids):
        raise HTTPException(404, "Item not found")
    return uuids


@router.post("/{cid}/items", status_code=204)
async def add_items(cid: str, body: ItemsIn, conn: AppDb, user: CurrentUser):
    uuid = parse_uuid(cid)
    await _get(conn, uuid)
    items = await _owned_items(conn, body.itemIds)
    for iid in items:
        await conn.execute(
            "INSERT INTO item_collections (item_id, collection_id, user_id) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            iid,
            uuid,
            user["id"],
        )
    return None


@router.delete("/{cid}/items", status_code=204)
async def remove_items(cid: str, body: ItemsIn, conn: AppDb):
    uuid = parse_uuid(cid)
    await _get(conn, uuid)
    items = await _owned_items(conn, body.itemIds)
    await conn.execute(
        "DELETE FROM item_collections WHERE collection_id = $1 AND item_id = ANY($2::uuid[])",
        uuid,
        items,
    )
    return None
