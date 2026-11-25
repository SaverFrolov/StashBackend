import logging
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("stash.search")

from ..deps import AppDb, CurrentUser
from ..dto import file_base, items_dto
from ..pipeline import ai

router = APIRouter(prefix="/search", tags=["search"])

RRF_K = 60
LIMIT = 50


class SearchFilters(BaseModel):
    mode: Literal["smart", "keywords", "meaning"] = "smart"
    types: list[str] | None = None
    collectionIds: list[str] | None = None
    tagIds: list[str] | None = None
    dateFrom: str | None = None
    dateTo: str | None = None
    statuses: list[str] | None = None


def _parse_date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "Invalid date")


async def _filters(conn, body: SearchFilters) -> tuple[str, list]:
    where: list[str] = []
    params: list = []

    def p(v):
        params.append(v)
        return f"${len(params)}"

    if body.types:
        where.append(f"i.type = ANY({p(body.types)}::text[])")
    if body.statuses:
        where.append(f"i.status = ANY({p(body.statuses)}::text[])")
    if body.dateFrom:
        where.append(f"i.captured_at >= {p(_parse_date(body.dateFrom))}")
    if body.dateTo:
        where.append(f"i.captured_at < {p(_parse_date(body.dateTo) + timedelta(days=1))}")
    if body.collectionIds:
        try:
            ids = [UUID(c) for c in body.collectionIds]
        except ValueError:
            raise HTTPException(422, "Invalid collection id")
        where.append(
            f"EXISTS (SELECT 1 FROM item_collections ic WHERE ic.item_id = i.id "
            f"AND ic.collection_id = ANY({p(ids)}::uuid[]))"
        )
    if body.tagIds:
        try:
            ids = [UUID(t) for t in body.tagIds]
        except ValueError:
            raise HTTPException(422, "Invalid tag id")
        names = [
            r["name"]
            for r in await conn.fetch("SELECT name FROM tags WHERE id = ANY($1::uuid[])", ids)
        ]
        ph = p(names)
        where.append(
            f"(i.ai_tags && {ph}::text[] OR EXISTS (SELECT 1 FROM item_tags it "
            f"JOIN tags tg ON tg.id = it.tag_id WHERE it.item_id = i.id "
            f"AND tg.name = ANY({ph}::text[])))"
        )
    return (" WHERE " + " AND ".join(where)) if where else "", params


async def _fts(conn, q: str, filter_sql: str, params: list) -> list:
    ph = f"${len(params) + 1}"
    return await conn.fetch(
        f"SELECT i.*, ts_headline('russian', "
        f"coalesce(i.extracted_text, i.original_text, i.ai_annotation, i.user_comment, ''), "
        f"plainto_tsquery('russian', {ph}), "
        f"'MaxFragments=1,MaxWords=25,MinWords=8,StartSel=,StopSel=,FragmentDelimiter= … ') AS snippet "
        f"FROM items i WHERE i.search_tsv @@ plainto_tsquery('russian', {ph}) "
        f"{filter_sql.replace('WHERE', 'AND', 1) if filter_sql else ''} "
        f"ORDER BY ts_rank(i.search_tsv, plainto_tsquery('russian', {ph})) DESC LIMIT {LIMIT}",
        *params,
        q,
    )


async def _vector(conn, q: str, filter_sql: str, params: list) -> list:
    vec = str((await ai.embed_texts([q]))[0])
    ph = f"${len(params) + 1}"
    return await conn.fetch(
        f"SELECT i.*, t.dist, t.content AS snippet FROM ("
        f"SELECT DISTINCT ON (item_id) item_id, dist, content FROM ("
        f"SELECT item_id, content, embedding <=> {ph}::vector AS dist "
        f"FROM item_chunks ORDER BY embedding <=> {ph}::vector LIMIT 200"
        f") c ORDER BY item_id, dist) t JOIN items i ON i.id = t.item_id"
        f"{filter_sql} ORDER BY t.dist LIMIT {LIMIT}",
        *params,
        vec,
    )


@router.post("")
async def search(
    request: Request,
    conn: AppDb,
    user: CurrentUser,
    body: SearchFilters,
    q: str = "",
    mode: Literal["smart", "keywords", "meaning"] | None = None,
):
    q = q.strip()
    if not q:
        return []
    mode = mode or body.mode
    await conn.execute(
        "INSERT INTO search_queries (user_id, query) VALUES ($1, $2)", user["id"], q
    )
    filter_sql, params = await _filters(conn, body)
    base = file_base(request)
    if mode == "keywords":
        rows = await _fts(conn, q, filter_sql, params)
        dtos = await items_dto(conn, rows, base)
        return [
            {"item": d, "snippet": r["snippet"] or None, "matchKind": "keyword"}
            for r, d in zip(rows, dtos)
        ]
    if mode == "meaning":
        try:
            rows = await _vector(conn, q, filter_sql, params)
        except Exception:
            logger.warning("Semantic search unavailable (embeddings failed)", exc_info=True)
            return []
        dtos = await items_dto(conn, rows, base)
        return [
            {"item": d, "snippet": (r["snippet"] or "")[:160] or None, "matchKind": "meaning"}
            for r, d in zip(rows, dtos)
        ]
    kw = await _fts(conn, q, filter_sql, params)
    try:
        mean = await _vector(conn, q, filter_sql, params)
    except Exception:
        logger.warning("Semantic leg of smart search failed", exc_info=True)
        mean = []
    scores: dict = {}
    best: dict = {}
    for rank, r in enumerate(kw):
        scores[r["id"]] = scores.get(r["id"], 0) + 1 / (RRF_K + rank + 1)
        best[r["id"]] = (r, "keyword")
    for rank, r in enumerate(mean):
        scores[r["id"]] = scores.get(r["id"], 0) + 1 / (RRF_K + rank + 1)
        best.setdefault(r["id"], (r, "meaning"))
    order = sorted(scores, key=scores.get, reverse=True)[:LIMIT]
    rows = [best[i][0] for i in order]
    dtos = {d["id"]: d for d in await items_dto(conn, rows, base)}
    results = []
    for i in order:
        r, kind = best[i]
        snippet = r["snippet"] or ""
        if kind == "meaning":
            snippet = snippet[:160]
        results.append(
            {"item": dtos[str(i)], "snippet": snippet or None, "matchKind": kind}
        )
    return results


@router.get("/recent")
async def recent(conn: AppDb):
    rows = await conn.fetch(
        "SELECT query FROM search_queries GROUP BY query ORDER BY max(created_at) DESC LIMIT 8"
    )
    return [r["query"] for r in rows]
