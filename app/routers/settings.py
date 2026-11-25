import shutil
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import cfg
from ..deps import AppDb, CurrentUser
from ..pipeline import ai
from ..security import encrypt_api_key, hash_password, verify_password

router = APIRouter(tags=["settings"])


class AiSettingsIn(BaseModel):
    provider: Literal["openai", "ollama"] | None = None
    baseUrl: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    apiKey: str | None = Field(default=None, max_length=500)
    autoProcess: bool | None = None


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    currentPassword: str | None = Field(default=None, max_length=200)


def _dto(row) -> dict:
    return {
        "provider": row["provider"],
        "baseUrl": row["base_url"],
        "model": row["model"],
        "apiKeySet": row["api_key_enc"] is not None,
        "autoProcess": row["auto_process"],
    }


async def _get(conn, user_id):
    row = await conn.fetchrow("SELECT * FROM ai_settings WHERE user_id = $1", user_id)
    if row is None:
        row = await conn.fetchrow(
            "INSERT INTO ai_settings (user_id, provider, base_url, model) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            user_id,
            cfg.default_ai["provider"],
            cfg.default_ai["base_url"],
            cfg.default_ai["model"],
        )
    return row


@router.get("/settings/ai")
async def get_ai_settings(conn: AppDb, user: CurrentUser):
    return _dto(await _get(conn, user["id"]))


@router.put("/settings/ai")
async def save_ai_settings(body: AiSettingsIn, conn: AppDb, user: CurrentUser):
    await _get(conn, user["id"])
    updates = {}
    if body.provider is not None:
        updates["provider"] = body.provider
    if body.baseUrl is not None:
        if not body.baseUrl.strip():
            raise HTTPException(422, "Base URL is empty")
        updates["base_url"] = body.baseUrl.strip()
    if body.model is not None:
        updates["model"] = body.model.strip()
    if body.autoProcess is not None:
        updates["auto_process"] = body.autoProcess
    if body.apiKey:
        updates["api_key_enc"] = encrypt_api_key(body.apiKey)
    if updates:
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
        await conn.execute(
            f"UPDATE ai_settings SET {sets}, updated_at = now() WHERE user_id = $1",
            user["id"],
            *updates.values(),
        )
    return _dto(await _get(conn, user["id"]))


@router.post("/settings/ai/test")
async def test_ai(conn: AppDb, user: CurrentUser):
    return await ai.test_connection(await _get(conn, user["id"]))


@router.patch("/account", status_code=204)
async def update_account(body: AccountPatch, conn: AppDb, user: CurrentUser):
    updates = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.password is not None:
        row = await conn.fetchrow(
            "SELECT password_hash FROM users WHERE id = $1", user["id"]
        )
        if not body.currentPassword or not verify_password(
            body.currentPassword, row["password_hash"]
        ):
            raise HTTPException(403, "Current password is wrong")
        updates["password_hash"] = hash_password(body.password)
    if updates:
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
        await conn.execute(
            f"UPDATE users SET {sets}, updated_at = now() WHERE id = $1",
            user["id"],
            *updates.values(),
        )
    return None


@router.delete("/account", status_code=204)
async def delete_account(conn: AppDb, user: CurrentUser):
    await conn.execute("DELETE FROM users WHERE id = $1", user["id"])
    shutil.rmtree(cfg.files_dir / str(user["id"]), ignore_errors=True)
    return None