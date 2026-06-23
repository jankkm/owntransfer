from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from app.models import Base


async def ensure_schema(conn: AsyncConnection) -> None:
    await conn.run_sync(Base.metadata.create_all)
