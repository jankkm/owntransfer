from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings

_db_url = settings.get_database_url()
_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_async_engine(_db_url, echo=False, future=True, connect_args=_connect_args)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
