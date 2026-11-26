"""«Спроси архив»: retrieval по кускам + один ответ LLM с цитатами."""
from __future__ import annotations

import hashlib
import json
import re
import time

import httpx

from .ai import _chat_url, embed_texts, resolve_key

ASK_SYSTEM = (
    "Ты — ассистент личного архива. Отвечай на вопрос ТОЛЬКО на основе пронумерованных "
    "фрагментов ниже. После каждого утверждения ставь номер источника в квадратных "
    "скобках, например [1] или [2][3]. Если во фрагментах ответа нет — прямо скажи, что "
    "в архиве этого нет, и не выдумывай. Ссылайся ТОЛЬКО на фрагменты, которые подтверждают ответ; нерелевантные не перечисляй. Отвечай на языке вопроса, кратко и по делу."
)

TOP_K = 12
PER_ITEM = 2
PER_CHUNK = 900
MAX_CONTEXT = 9000
MAX_DIST = 0.55   # калибровано по bge-m3: релевантно <=0.475, мусор >=0.637
SPREAD = 0.10     # от лучшего куска: дальше — уже не про то
CACHE_TTL = 600
_cache: dict[tuple[str, str], tuple[float, dict]] = {}

_SQL = """
WITH top AS (
    SELECT item_id, chunk_no, content, embedding <=> $1::vector AS dist
    FROM item_chunks ORDER BY embedding <=> $1::vector LIMIT 200
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY item_id ORDER BY dist) AS rn FROM top
)
SELECT r.item_id, r.chunk_no, r.content, r.dist, i.title, i.type, i.source
FROM ranked r JOIN items i ON i.id = r.item_id
WHERE r.rn <= $2 AND r.dist <= $4 ORDER BY r.dist LIMIT $3
"""


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def narrow(rows: list[dict], spread: float = SPREAD) -> list[dict]:
    """Оставляем кластер вокруг лучшего куска, а не всё, что влезло под порог."""
    if not rows:
        return rows
    best = float(rows[0]["dist"])
    return [r for r in rows if float(r["dist"]) <= best + spread]


async def retrieve(conn, question: str) -> list[dict]:
    vec = (await embed_texts([question]))[0]
    rows = await conn.fetch(_SQL, _vec_literal(vec), PER_ITEM, TOP_K, MAX_DIST)
    return narrow([dict(r) for r in rows])


def _url(source) -> str | None:
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError:
            return None
    return (source or {}).get("url")


def build_context(rows, max_chars: int = MAX_CONTEXT, per_chunk: int = PER_CHUNK):
    blocks, sources, used = [], [], 0
    for r in rows:
        body = " ".join((r["content"] or "").split())[:per_chunk]
        if not body:
            continue
        n = len(sources) + 1
        title = r["title"] or "без названия"
        block = f"[{n}] «{title}»\n{body}\n"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
        sources.append({
            "n": n, "itemId": str(r["item_id"]), "title": title,
            "chunkNo": r["chunk_no"], "type": r["type"], "url": _url(r["source"]),
            "dist": round(float(r.get("dist") or 0.0), 3),
        })
    return "\n".join(blocks), sources


def cited_sources(text: str, sources: list[dict]) -> list[dict]:
    nums = {int(m) for m in re.findall(r"\[(\d{1,2})\]", text)}
    picked = [s for s in sources if s["n"] in nums]
    return picked or sources[:3]


def dedupe_sources(picked: list[dict]) -> list[dict]:
    """Один чип на материал, куски собираем вместе."""
    by_item: dict = {}
    for s in picked:
        e = by_item.setdefault(s["itemId"], {**s, "chunks": []})
        if s["chunkNo"] not in e["chunks"]:
            e["chunks"].append(s["chunkNo"])
    return list(by_item.values())


async def complete(settings, question: str, context: str) -> str:
    headers = {}
    key = resolve_key(settings)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": ASK_SYSTEM},
            {"role": "user", "content": f"ВОПРОС: {question}\n\nФРАГМЕНТЫ:\n{context}"},
        ],
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            _chat_url(settings["provider"], settings["base_url"]),
            json=payload, headers=headers,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def ask(conn, settings, user_id, question: str) -> dict:
    q = (question or "").strip()
    if not q:
        return {"answer": "", "sources": []}
    ck = (str(user_id), hashlib.md5(q.lower().encode()).hexdigest())
    now = time.time()
    hit = _cache.get(ck)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    rows = await retrieve(conn, q)
    if not rows:
        return {"answer": "В архиве нет материалов, релевантных этому вопросу.", "sources": []}
    context, sources = build_context(rows)
    text = await complete(settings, q, context)
    out = {"answer": text, "sources": dedupe_sources(cited_sources(text, sources))}
    if len(_cache) > 500:
        _cache.clear()
    _cache[ck] = (now, out)
    return out
