import json
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg

from .config import cfg

pools: dict[str, asyncpg.Pool] = {}


async def _codecs(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def init_pools() -> None:
    pools["auth"] = await asyncpg.create_pool(
        cfg.auth_dsn, min_size=1, max_size=5, init=_codecs
    )
    pools["app"] = await asyncpg.create_pool(
        cfg.app_dsn, min_size=1, max_size=10, init=_codecs
    )


async def close_pools() -> None:
    for pool in pools.values():
        await pool.close()
    pools.clear()


@asynccontextmanager
async def app_conn(user_id: UUID):
    """App-role connection scoped to one user via RLS (app.user_id)."""
    async with pools["app"].acquire() as conn:
        await conn.execute("SELECT set_config('app.user_id', $1, false)", str(user_id))
        try:
            yield conn
        finally:
            # RESET, not set_config(''): an empty string breaks the ::uuid cast in policies.
            await conn.execute("RESET app.user_id")
