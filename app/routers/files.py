import re
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..db import app_conn
from ..security import verify_file_url

router = APIRouter(tags=["files"])

CHUNK = 1024 * 1024


def _iter_file(path: Path, start: int, length: int):
    with path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            data = f.read(min(CHUNK, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


@router.get("/files/{item_id}")
async def get_file(item_id: str, u: str, exp: str, sig: str, request: Request):
    if not verify_file_url(u, item_id, exp, sig):
        raise HTTPException(403, "Invalid or expired link")
    try:
        uid, iid = UUID(u), UUID(item_id)
    except ValueError:
        raise HTTPException(403, "Invalid link")
    async with app_conn(uid) as conn:
        row = await conn.fetchrow(
            "SELECT file_path, file_name, file_mime FROM items WHERE id = $1", iid
        )
    if row is None or not row["file_path"]:
        raise HTTPException(404, "File not found")
    path = Path(row["file_path"])
    if not path.is_file():
        raise HTTPException(404, "File not found")
    size = path.stat().st_size
    mime = row["file_mime"] or "application/octet-stream"
    range_header = request.headers.get("range")
    if range_header:
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(size - int(m.group(2)), 0)
                end = size - 1
            end = min(end, size - 1)
            if start >= size or start > end:
                return Response(
                    status_code=416, headers={"Content-Range": f"bytes */{size}"}
                )
            length = end - start + 1
            return StreamingResponse(
                _iter_file(path, start, length),
                status_code=206,
                media_type=mime,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(length),
                    "Accept-Ranges": "bytes",
                },
            )
    return FileResponse(path, media_type=mime, headers={"Accept-Ranges": "bytes"})
