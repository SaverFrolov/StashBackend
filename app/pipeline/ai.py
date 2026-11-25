import json
import re

import httpx

from ..config import cfg
from ..security import decrypt_api_key

SYSTEM_PROMPT = (
    "Ты — ассистент личного архива материалов. Прочитай материал и верни строго "
    'JSON-объект вида {"annotation": "...", "tags": ["...", "..."]}. '
    "annotation — краткая аннотация на языке материала (3–6 предложений, без воды). "
    "tags — 3–8 коротких тегов на языке материала (одно слово или короткая фраза). "
    "Никаких пояснений вне JSON."
)

MAX_AI_CHARS = 300_000


def chunk_text(text: str, size: int = 1500, overlap: int = 200) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            brk = text.rfind("\n", start + size // 2, end)
            if brk == -1:
                brk = text.rfind(" ", start + size // 2, end)
            if brk > start:
                end = brk
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


async def embed_texts(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{cfg.ollama_url}/api/embed",
            json={"model": cfg.embed_model, "input": texts},
        )
        r.raise_for_status()
        return r.json()["embeddings"]


def _chat_url(provider: str, base_url: str) -> str:
    base = base_url.rstrip("/")
    if provider == "ollama" and not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def _key(settings) -> str | None:
    enc = settings["api_key_enc"]
    return decrypt_api_key(enc) if enc else None


async def annotate(settings, text: str) -> dict:
    headers = {}
    key = _key(settings)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:MAX_AI_CHARS]},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            _chat_url(settings["provider"], settings["base_url"]),
            json=payload,
            headers=headers,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    data = json.loads(content)
    annotation = str(data.get("annotation", "")).strip()
    tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()]
    if not annotation:
        raise ValueError("AI returned empty annotation")
    return {"annotation": annotation, "tags": list(dict.fromkeys(tags))[:8]}


async def test_connection(settings) -> dict:
    base = settings["base_url"].rstrip("/")
    if not base:
        return {"ok": False, "message": "Base URL is empty"}
    if settings["provider"] == "ollama":
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(base + "/api/tags")
            if r.status_code == 200:
                return {"ok": True, "message": f"Connected to {settings['model']}"}
            return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:200]}"}
        except httpx.HTTPError as e:
            return {"ok": False, "message": str(e)[:200]}
    # /models can be public — validate the key with a 1-token chat completion.
    headers = {}
    key = _key(settings)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                _chat_url(settings["provider"], base),
                json={
                    "model": settings["model"],
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
                headers=headers,
            )
        if r.status_code == 200:
            return {"ok": True, "message": f"Connected to {settings['model']}"}
        return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:200]}"}
    except httpx.HTTPError as e:
        return {"ok": False, "message": str(e)[:200]}
