from datetime import datetime, timezone

from .config import cfg
from .security import sign_file_url


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (
        dt.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _clean(d: dict) -> dict:
    # Zod .optional() accepts undefined but rejects null — drop None fields.
    return {k: v for k, v in d.items() if v is not None}


def file_base(request) -> str:
    return cfg.public_base_url or str(request.base_url).rstrip("/")


def step_dto(row) -> dict:
    return _clean(
        {
            "name": row["name"],
            "status": row["status"],
            "startedAt": iso(row["started_at"]),
            "finishedAt": iso(row["finished_at"]),
            "error": row["error"],
        }
    )


def collection_dto(row) -> dict:
    return _clean(
        {
            "id": str(row["id"]),
            "name": row["name"],
            "description": row["description"],
            "createdAt": iso(row["created_at"]),
            "updatedAt": iso(row["updated_at"]),
        }
    )


def tag_dto(row) -> dict:
    return {"id": str(row["id"]), "name": row["name"], "origin": row["origin"]}


def item_dto(row, user_tags: list[str], collection_ids: list[str], steps: list, base: str) -> dict:
    original: dict = {}
    if row["file_path"]:
        original["file"] = {
            "name": row["file_name"],
            "size": row["file_size"] or 0,
            "mime": row["file_mime"] or "",
            "url": base + sign_file_url(row["user_id"], row["id"]),
        }
    elif row["original_text"] is not None:
        original["text"] = row["original_text"]
    video_meta = row.get("video_meta")
    video = None
    if video_meta:
        video = _clean(
            {
                "platform": video_meta.get("platform"),
                "videoId": video_meta.get("video_id"),
                "title": video_meta.get("title"),
                "channel": video_meta.get("channel"),
                "durationSec": video_meta.get("duration_sec"),
                "sourceUrl": video_meta.get("source_url"),
            }
        )
    stage = row.get("extract_stage")
    progress = row.get("extract_progress") or {}
    video_progress = (
        _clean({"stage": stage, "current": progress.get("current"),
                "total": progress.get("total"), "percent": progress.get("percent")})
        if stage else None
    )
    error_code = row.get("extract_error_code")
    video_error = (
        {"code": error_code, "message": row.get("extracted_error") or ""}
        if error_code else None
    )
    return _clean(
        {
            "id": str(row["id"]),
            "type": row["type"],
            "status": row["status"],
            "title": row["title"],
            "lang": row["lang"],
            "capturedAt": iso(row["captured_at"]),
            "source": row["source"] or {},
            "video": video,
            "videoProgress": video_progress,
            "videoError": video_error,
            "original": original,
            "extracted": _clean(
                {"text": row["extracted_text"], "error": row["extracted_error"]}
            ),
            "ai": _clean(
                {
                    "annotation": row["ai_annotation"],
                    "tags": row["ai_tags"],
                    "model": row["ai_model"] or "",
                    "generatedAt": iso(row["ai_generated_at"]),
                    "edited": row["ai_edited"],
                }
            ),
            "userComment": row["user_comment"],
            "userTags": user_tags,
            "suggestedTags": row["ai_tags"],
            "collectionIds": collection_ids,
            "processing": [step_dto(s) for s in steps],
        }
    )


async def items_dto(conn, rows: list, base: str) -> list[dict]:
    """Serialize item rows with their tags/collections/steps (4 queries, no N+1)."""
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    tag_rows = await conn.fetch(
        "SELECT it.item_id, t.name FROM item_tags it JOIN tags t ON t.id = it.tag_id "
        "WHERE it.item_id = ANY($1::uuid[])",
        ids,
    )
    col_rows = await conn.fetch(
        "SELECT item_id, collection_id FROM item_collections WHERE item_id = ANY($1::uuid[])",
        ids,
    )
    step_rows = await conn.fetch(
        "SELECT * FROM processing_steps WHERE item_id = ANY($1::uuid[]) "
        "ORDER BY CASE name WHEN 'extract' THEN 0 ELSE 1 END",
        ids,
    )
    tags: dict = {}
    cols: dict = {}
    steps: dict = {}
    for r in tag_rows:
        tags.setdefault(r["item_id"], []).append(r["name"])
    for r in col_rows:
        cols.setdefault(r["item_id"], []).append(str(r["collection_id"]))
    for r in step_rows:
        steps.setdefault(r["item_id"], []).append(r)
    return [
        item_dto(
            r,
            tags.get(r["id"], []),
            cols.get(r["id"], []),
            steps.get(r["id"], []),
            base,
        )
        for r in rows
    ]


async def one_item_dto(conn, row, base: str) -> dict:
    return (await items_dto(conn, [row], base))[0]
