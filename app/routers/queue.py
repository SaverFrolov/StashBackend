"""Наблюдаемость очередей обработки."""
from fastapi import APIRouter

from ..deps import CurrentUser
from ..pipeline import queue

router = APIRouter(tags=["queue"])


@router.get("/queue/status")
async def queue_status(user: CurrentUser):
    return queue.status()
