import re
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import cfg
from ..db import app_conn, pools
from ..security import hash_password, new_token, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignUpIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=200)


class SignInIn(BaseModel):
    email: str
    password: str


class ResetIn(BaseModel):
    email: str


async def _create_session(conn, user_id) -> dict:
    token = new_token()
    expires = datetime.now(timezone.utc) + timedelta(days=cfg.session_days)
    await conn.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
        user_id,
        token_hash(token),
        expires,
    )
    return token


def _session(token: str, user) -> dict:
    return {
        "token": token,
        "user": {"id": str(user["id"]), "name": user["name"], "email": user["email"]},
    }


@router.post("/signup")
async def signup(body: SignUpIn):
    if not EMAIL_RE.match(body.email):
        raise HTTPException(422, "Invalid email")
    async with pools["auth"].acquire() as conn:
        try:
            user = await conn.fetchrow(
                "INSERT INTO users (email, password_hash, name) VALUES ($1, $2, $3) "
                "RETURNING id, name, email",
                body.email.strip(),
                hash_password(body.password),
                body.name.strip(),
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "Email already registered")
        token = await _create_session(conn, user["id"])
    async with app_conn(user["id"]) as conn:
        await conn.execute(
            "INSERT INTO ai_settings (user_id, provider, base_url, model) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (user_id) DO NOTHING",
            user["id"],
            cfg.default_ai["provider"],
            cfg.default_ai["base_url"],
            cfg.default_ai["model"],
        )
    return _session(token, user)


@router.post("/signin")
async def signin(body: SignInIn):
    async with pools["auth"].acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, name, email, password_hash FROM users WHERE email = $1",
            body.email.strip(),
        )
        if user is None or not verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Invalid email or password")
        token = await _create_session(conn, user["id"])
    return _session(token, user)


@router.post("/reset-password", status_code=204)
async def reset_password(body: ResetIn):
    # Stub until SMTP is set up (user decision 2026-11-23); never leak existence.
    return None
