import asyncio
import logging
from uuid import UUID

from ..config import cfg

logger = logging.getLogger("stash.pipeline")

_queue: asyncio.Queue = asyncio.Queue()


def enqueue(item_id: UUID, user_id: UUID, steps=("extract", "annotate")) -> None:
    _queue.put_nowait((item_id, user_id, tuple(steps)))


async def _worker() -> None:
    from .worker import process_job

    while True:
        item_id, user_id, steps = await _queue.get()
        try:
            await process_job(item_id, user_id, steps)
        except Exception:
            logger.exception("Processing job failed for item %s", item_id)
        finally:
            _queue.task_done()


async def start_workers() -> list[asyncio.Task]:
    from .worker import recover

    await recover()
    return [asyncio.create_task(_worker()) for _ in range(cfg.worker_concurrency)]
