from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException

from .db import app_conn, pools
from .security import token_hash


async def current_user(authorization: Annotated[str | None, Header()] = None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    h = token_hash(authorization[7:].strip())
    async with pools["auth"].acquire() as conn:
        row = await conn.fetchrow(
            "SELECT u.id, u.name, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = $1 AND s.expires_at > now()",
            h,
        )
        table = "sessions"
        if row is None:
            row = await conn.fetchrow(
                "SELECT u.id, u.name, u.email FROM api_tokens t JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash = $1 AND (t.expires_at IS NULL OR t.expires_at > now())",
                h,
            )
            table = "api_tokens"
        if row is None:
            raise HTTPException(401, "Invalid or expired token")
        await conn.execute(
            f"UPDATE {table} SET last_used_at = now() WHERE token_hash = $1", h
        )
    return dict(row)


CurrentUser = Annotated[dict, Depends(current_user)]


async def app_db(user: CurrentUser):
    async with app_conn(user["id"]) as conn:
        yield conn


AppDb = Annotated[object, Depends(app_db)]


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(404, "Not found")
