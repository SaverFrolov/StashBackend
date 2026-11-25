from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import pools
from ..deps import CurrentUser, parse_uuid
from ..dto import iso
from ..security import new_token, token_hash

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    expiresInDays: int | None = Field(default=None, ge=1, le=3650)


def _dto(row) -> dict:
    out = {
        "id": str(row["id"]),
        "name": row["name"],
        "createdAt": iso(row["created_at"]),
    }
    if row["expires_at"]:
        out["expiresAt"] = iso(row["expires_at"])
    if row["last_used_at"]:
        out["lastUsedAt"] = iso(row["last_used_at"])
    return out


@router.get("")
async def list_tokens(user: CurrentUser):
    async with pools["auth"].acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM api_tokens WHERE user_id = $1 ORDER BY created_at", user["id"]
        )
    return [_dto(r) for r in rows]


@router.post("")
async def create_token(body: TokenIn, user: CurrentUser):
    secret = "stash_" + new_token()
    expires = (
        datetime.now(timezone.utc) + timedelta(days=body.expiresInDays)
        if body.expiresInDays
        else None
    )
    async with pools["auth"].acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO api_tokens (user_id, name, token_hash, expires_at) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            user["id"],
            body.name.strip(),
            token_hash(secret),
            expires,
        )
    return {"token": _dto(row), "secret": secret}


@router.delete("/{tid}", status_code=204)
async def revoke_token(tid: str, user: CurrentUser):
    async with pools["auth"].acquire() as conn:
        await conn.execute(
            "DELETE FROM api_tokens WHERE id = $1 AND user_id = $2",
            parse_uuid(tid),
            user["id"],
        )
    return None
