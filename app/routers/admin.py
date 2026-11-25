import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import cfg
from ..db import app_conn, pools
from ..deps import AdminUser, parse_uuid
from ..dto import iso

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
async def list_users(user: AdminUser):
    async with pools["auth"].acquire() as conn:
        rows = await conn.fetch("SELECT id, name, email, created_at FROM users ORDER BY created_at")
    users = []
    total_items = 0
    total_bytes = 0
    for r in rows:
        async with app_conn(r["id"]) as conn:
            stats = await conn.fetchrow(
                "SELECT count(*) AS items, coalesce(sum(file_size), 0) AS bytes FROM items"
            )
        items, bytes_ = stats["items"], stats["bytes"]
        total_items += items
        total_bytes += bytes_
        users.append(
            {
                "id": str(r["id"]),
                "name": r["name"],
                "email": r["email"],
                "createdAt": iso(r["created_at"]),
                "items": items,
                "bytesUsed": bytes_,
            }
        )
    return {
        "users": users,
        "totals": {"users": len(users), "items": total_items, "bytesUsed": total_bytes},
    }


@router.delete("/users/{uid}", status_code=204)
async def delete_user(uid: str, user: AdminUser):
    uuid = parse_uuid(uid)
    if uuid == user["id"]:
        raise HTTPException(400, "Cannot delete yourself")
    async with pools["auth"].acquire() as conn:
        result = await conn.execute("DELETE FROM users WHERE id = $1", uuid)
    if result == "DELETE 0":
        raise HTTPException(404, "User not found")
    shutil.rmtree(cfg.files_dir / str(uuid), ignore_errors=True)
    return None
