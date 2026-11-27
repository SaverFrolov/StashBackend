"""Два независимых лейна обработки: лёгкий и тяжёлый.

Тяжёлые задачи (видео, аудио, крупные файлы) идут строго по одной и стоят
в своей очереди, поэтому не могут заблокировать заметки и ссылки.
"""
import asyncio
import json
import logging
import os
from uuid import UUID

from ..config import cfg

logger = logging.getLogger("stash.pipeline")

LIGHT = "light"
HEAVY = "heavy"

_queues: dict[str, asyncio.Queue] = {LIGHT: asyncio.Queue(), HEAVY: asyncio.Queue()}
_running: dict[str, set] = {LIGHT: set(), HEAVY: set()}
_pending: dict[str, set] = {LIGHT: set(), HEAVY: set()}

HEAVY_TYPES = {"video", "audio"}
HEAVY_BYTES = int(os.getenv("HEAVY_FILE_BYTES", str(5 * 1024 * 1024)))
HEAVY_CONCURRENCY = max(1, int(os.getenv("HEAVY_CONCURRENCY", "1")))
VIDEO_HOSTS = ("vk.com/video", "vkvideo.ru", "rutube.ru", "youtube.com", "youtu.be", "vimeo.com")


def _pick(row: dict, *names, default=None):
    for n in names:
        v = row.get(n)
        if v is not None:
            return v
    return default


def classify_row(row) -> str:
    """Чистая функция: решает лейн по типу, mime, размеру и ссылке."""
    if not row:
        return LIGHT
    kind = str(_pick(row, "type", default="") or "").lower()
    if kind in HEAVY_TYPES:
        return HEAVY
    mime = str(_pick(row, "mime", "mime_type", "content_type", default="") or "").lower()
    if mime.startswith(("video/", "audio/")):
        return HEAVY
    size = _pick(row, "size_bytes", "file_size", "bytes", "size", default=0) or 0
    try:
        if int(size) >= HEAVY_BYTES:
            return HEAVY
    except (TypeError, ValueError):
        pass
    src = _pick(row, "source", default=None)
    if isinstance(src, str):
        try:
            src = json.loads(src)
        except json.JSONDecodeError:
            src = {"url": src}
    url = str((src or {}).get("url") or "").lower() if isinstance(src, dict) else ""
    if any(h in url for h in VIDEO_HOSTS):
        return HEAVY
    return LIGHT


def enqueue(item_id: UUID, user_id: UUID, steps=("extract", "annotate"), lane: str = LIGHT) -> None:
    lane = HEAVY if lane == HEAVY else LIGHT
    key = str(item_id)
    for other in (LIGHT, HEAVY):
        if key in _running[other] or key in _pending[other]:
            logger.info("skip duplicate %s (already in %s)", item_id, other)
            return
    _pending[lane].add(key)
    _queues[lane].put_nowait((item_id, user_id, tuple(steps)))
    logger.info("queued %s → %s (depth %d)", item_id, lane, _queues[lane].qsize())


async def enqueue_auto(item_id: UUID, user_id: UUID, steps=("extract", "annotate")) -> str:
    """Определяет лейн по материалу и ставит задачу в нужную очередь."""
    from ..db import app_conn

    lane = LIGHT
    try:
        async with app_conn(user_id) as conn:
            row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", item_id)
        lane = classify_row(dict(row) if row is not None else None)
    except Exception:
        logger.warning("Lane classification failed for %s, falling back to light", item_id, exc_info=True)
    enqueue(item_id, user_id, steps, lane)
    return lane


async def _worker(lane: str) -> None:
    from .worker import process_job

    q = _queues[lane]
    while True:
        item_id, user_id, steps = await q.get()
        _pending[lane].discard(str(item_id))
        _running[lane].add(str(item_id))
        try:
            await process_job(item_id, user_id, steps)
        except Exception:
            logger.exception("Processing job failed for item %s (%s lane)", item_id, lane)
        finally:
            _running[lane].discard(str(item_id))
            q.task_done()


def status() -> dict:
    return {
        "light": {
            "queued": _queues[LIGHT].qsize(),
            "running": len(_running[LIGHT]),
            "workers": max(1, cfg.worker_concurrency),
        },
        "heavy": {
            "queued": _queues[HEAVY].qsize(),
            "running": len(_running[HEAVY]),
            "workers": HEAVY_CONCURRENCY,
        },
    }


async def start_workers() -> list[asyncio.Task]:
    from .worker import recover

    await recover()
    light_n = max(1, cfg.worker_concurrency)
    tasks = [asyncio.create_task(_worker(LIGHT), name=f"worker-light-{i}") for i in range(light_n)]
    tasks += [asyncio.create_task(_worker(HEAVY), name=f"worker-heavy-{i}") for i in range(HEAVY_CONCURRENCY)]
    logger.info("workers started: light=%d heavy=%d", light_n, HEAVY_CONCURRENCY)
    return tasks
