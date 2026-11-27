import logging
from uuid import UUID

import asyncpg

from ..config import cfg
from ..db import app_conn, pools
from . import ai
from .extract import extract_item
from .queue import enqueue_auto

logger = logging.getLogger("stash.pipeline")


async def _step(conn, item_id, name, status, *, error=None, started=False, finished=False):
    sets = ["status = $3"]
    params: list = [item_id, name, status]
    if started:
        sets.append("started_at = now()")
    if finished:
        sets.append("finished_at = now()")
    if error is not None:
        params.append(error[:2000])
        sets.append(f"error = ${len(params)}")
    await conn.execute(
        f"UPDATE processing_steps SET {', '.join(sets)} WHERE item_id = $1 AND name = $2",
        *params,
    )


async def _embed(conn, item_id, user_id, text: str) -> None:
    chunks = ai.chunk_text(text)
    if not chunks:
        await conn.execute("DELETE FROM item_chunks WHERE item_id = $1", item_id)
        return

    existing = {
        r["chunk_no"]: r["content"]
        for r in await conn.fetch(
            "SELECT chunk_no, content FROM item_chunks "
            "WHERE item_id = $1 AND embedding IS NOT NULL",
            item_id,
        )
    }
    await conn.execute(
        "DELETE FROM item_chunks WHERE item_id = $1 AND chunk_no >= $2",
        item_id, len(chunks),
    )
    todo = [i for i, c in enumerate(chunks) if existing.get(i) != c]
    if not todo:
        return  # текст не изменился — эмбеддинги остаются как есть

    try:
        vecs = await ai.embed_texts([chunks[i] for i in todo])
    except Exception:
        # Extraction stays ok; only semantic search degrades for this item.
        logger.warning("Embedding failed for item %s", item_id, exc_info=True)
        return
    try:
        await conn.executemany(
            "INSERT INTO item_chunks (item_id, user_id, chunk_no, content, embedding) "
            "VALUES ($1, $2, $3, $4, $5::vector) "
            "ON CONFLICT (item_id, chunk_no) DO UPDATE "
            "SET content = EXCLUDED.content, embedding = EXCLUDED.embedding",
            [(item_id, user_id, i, chunks[i], str(v)) for i, v in zip(todo, vecs)],
        )
    except asyncpg.ForeignKeyViolationError:
        pass  # item deleted while embedding


async def _annotate(conn, item_id, user_id, text: str) -> None:
    if not text.strip():
        await _step(conn, item_id, "annotate", "skipped", finished=True)
        await conn.execute(
            "UPDATE items SET status = 'done', updated_at = now() WHERE id = $1", item_id
        )
        return
    settings = await conn.fetchrow(
        "SELECT * FROM ai_settings WHERE user_id = $1", user_id
    )
    key_override = None
    if settings is not None and settings["provider"] == "openai" and not settings["api_key_enc"]:
        if not cfg.default_ai_key:
            await _step(conn, item_id, "annotate", "skipped", finished=True)
            await conn.execute(
                "UPDATE items SET status = 'partial', updated_at = now() WHERE id = $1",
                item_id,
            )
            return
        key_override = ai.resolve_key(settings)
    if settings is None:
        await _step(conn, item_id, "annotate", "skipped", finished=True)
        await conn.execute(
            "UPDATE items SET status = 'partial', updated_at = now() WHERE id = $1",
            item_id,
        )
        return
    await conn.execute(
        "UPDATE items SET status = 'annotating', updated_at = now() WHERE id = $1",
        item_id,
    )
    await _step(conn, item_id, "annotate", "running", started=True)
    try:
        result = await ai.annotate(settings, text, key=key_override)
    except Exception as e:
        msg = (str(e) or type(e).__name__)[:2000]
        logger.warning("Annotation failed for item %s: %s", item_id, msg)
        await _step(conn, item_id, "annotate", "failed", error=msg, finished=True)
        await conn.execute(
            "UPDATE items SET status = 'ai_failed', updated_at = now() WHERE id = $1",
            item_id,
        )
        return
    await conn.execute(
        "UPDATE items SET ai_annotation = $2, ai_original_annotation = $2, ai_model = $3, "
        "ai_generated_at = now(), ai_tags = $4::text[], ai_edited = false, "
        "status = 'done', updated_at = now() WHERE id = $1",
        item_id,
        result["annotation"],
        settings["model"],
        result["tags"],
    )
    for tag in result["tags"]:
        await conn.execute(
            "INSERT INTO tags (user_id, name, origin) VALUES ($1, $2, 'ai') "
            "ON CONFLICT (user_id, name) DO NOTHING",
            user_id,
            tag,
        )
    await _step(conn, item_id, "annotate", "ok", finished=True)


async def process_job(item_id: UUID, user_id: UUID, steps: tuple) -> None:
    async with app_conn(user_id) as conn:
        row = await conn.fetchrow("SELECT * FROM items WHERE id = $1", item_id)
        if row is None:
            return
        text = row["extracted_text"] or row["original_text"] or ""
        if "extract" in steps:
            await conn.execute(
                "UPDATE items SET status = 'extracting', updated_at = now() WHERE id = $1",
                item_id,
            )
            await _step(conn, item_id, "extract", "running", started=True)
            try:
                result = await extract_item(row, conn)
                if result is not None and not result[0].strip():
                    raise RuntimeError("No text could be extracted")
            except Exception as e:
                msg = (str(e) or type(e).__name__)[:2000]
                logger.warning("Extraction failed for item %s: %s", item_id, msg)
                await conn.execute(
                    "UPDATE items SET extracted_error = $2, status = 'extraction_failed', "
                    "updated_at = now() WHERE id = $1",
                    item_id,
                    msg,
                )
                await _step(conn, item_id, "extract", "failed", error=msg, finished=True)
                await _step(conn, item_id, "annotate", "skipped", finished=True)
                return
            if result is None:
                await _step(conn, item_id, "extract", "skipped", finished=True)
                await _embed(conn, item_id, user_id, text)
            else:
                new_text, lang, title = result
                await conn.execute(
                    "UPDATE items SET extracted_text = $2, extracted_error = NULL, "
                    "lang = COALESCE($3, lang), "
                    "title = CASE WHEN $4::text IS NOT NULL AND $4 <> '' THEN $4 ELSE title END, "
                    "updated_at = now() WHERE id = $1",
                    item_id,
                    new_text,
                    lang,
                    title,
                )
                await _step(conn, item_id, "extract", "ok", finished=True)
                text = new_text
                await _embed(conn, item_id, user_id, text)
        if "annotate" in steps:
            if await conn.fetchval("SELECT 1 FROM items WHERE id = $1", item_id) is None:
                return  # item deleted while processing
            await _annotate(conn, item_id, user_id, text)


async def recover() -> None:
    """Re-enqueue jobs interrupted by a restart (per-user RLS scan)."""
    async with pools["auth"].acquire() as conn:
        users = await conn.fetch("SELECT id FROM users")
    count = 0
    for user in users:
        async with app_conn(user["id"]) as conn:
            rows = await conn.fetch(
                "SELECT id FROM items WHERE status IN ('extracting', 'annotating') "
                "OR (status = 'saved' AND EXISTS (SELECT 1 FROM processing_steps ps "
                "WHERE ps.item_id = items.id AND ps.status IN ('pending', 'running')))"
            )
            for r in rows:
                await conn.execute(
                    "UPDATE processing_steps SET status = 'pending', started_at = NULL, "
                    "finished_at = NULL, error = NULL "
                    "WHERE item_id = $1 AND status IN ('pending', 'running')",
                    r["id"],
                )
                st = await conn.fetchval(
                    "SELECT status FROM processing_steps WHERE item_id = $1 AND name = 'extract'",
                    r["id"],
                )
                steps = ("annotate",) if st in ("ok", "skipped") else ("extract", "annotate")
                await enqueue_auto(r["id"], user["id"], steps)
                count += 1
    if count:
        logger.info("Recovered %s interrupted processing jobs", count)
