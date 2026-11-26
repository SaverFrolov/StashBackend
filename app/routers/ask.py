from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..deps import AppDb, CurrentUser
from ..pipeline import ai
from ..pipeline import ask as engine
from .settings import _get as load_settings

router = APIRouter(tags=["ask"])


class AskIn(BaseModel):
    q: str = Field(min_length=1, max_length=500)


@router.post("/ask")
async def ask_archive(body: AskIn, conn: AppDb, user: CurrentUser):
    settings = await load_settings(conn, user["id"])
    if not ai.ai_available(settings):
        raise HTTPException(409, "ИИ не настроен: добавь ключ или провайдера в настройках")
    try:
        return await engine.ask(conn, settings, user["id"], body.q)
    except Exception as e:
        raise HTTPException(502, f"Не удалось получить ответ: {str(e)[:200]}")
