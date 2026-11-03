from __future__ import annotations

from psycopg.rows import dict_row
from sqlalchemy import text

from db.settings import settings

_setup_done = False


def checkpoint_dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _schema_ident() -> str:
    name = settings.checkpoint_schema or "langgraph"
    if not name.replace("_", "").isalnum():
        raise ValueError("invalid checkpoint schema name")
    return name


async def ensure_checkpoint_schema(session) -> None:
    await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_schema_ident()}"))


async def setup_checkpoint_tables() -> None:
    """Create LangGraph checkpoint tables inside the dedicated schema, then close."""
    global _setup_done
    if _setup_done:
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection

    schema = _schema_ident()
    conn = await AsyncConnection.connect(
        checkpoint_dsn(), autocommit=True, prepare_threshold=0, row_factory=dict_row
    )
    try:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        await conn.execute(f"SET search_path TO {schema}, public")
        saver = AsyncPostgresSaver(conn)
        await saver.setup()
        _setup_done = True
    finally:
        await conn.close()
