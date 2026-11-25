import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import cfg
from .db import close_pools, init_pools
from .pipeline import queue
from .routers import (
    admin,
    auth,
    collections,
    export,
    files,
    items,
    search,
    settings,
    tags,
    tokens,
)

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pools()
    workers = await queue.start_workers()
    yield
    for task in workers:
        task.cancel()
    await close_pools()


app = FastAPI(title="Stash API", lifespan=lifespan)

MAX_BODY_BYTES = 3 * 1024**3  # ~2 GB video as base64 JSON plus overhead


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "Payload too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(items.router)
app.include_router(files.router)
app.include_router(search.router)
app.include_router(collections.router)
app.include_router(tags.router)
app.include_router(settings.router)
app.include_router(tokens.router)
app.include_router(export.router)


@app.get("/health")
async def health():
    return {"ok": True}


# Optional: serve the built frontend from the same origin (one tunnel for stashapp.ru).
_dist = os.environ.get("FRONTEND_DIST", "")
if _dist and Path(_dist).is_dir():
    _dist_path = Path(_dist).resolve()
    if (_dist_path / "assets").is_dir():
        app.mount(
            "/assets", StaticFiles(directory=_dist_path / "assets"), name="assets"
        )

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        candidate = (_dist_path / path).resolve()
        if path and candidate.is_relative_to(_dist_path) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dist_path / "index.html")
