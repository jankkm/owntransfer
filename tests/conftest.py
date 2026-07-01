from __future__ import annotations

import os
import re
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_test_db.name}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SETUP_TOKEN", "test-setup-token")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp())

from app.auth.passwords import hash_password
from app.database import async_session, engine, get_db
from app.logging_config import configure_logging
from app.main import app
from app.models import AppSettings, Base, User

configure_logging()


@pytest_asyncio.fixture(autouse=True)
async def prepare_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        session.add(
            AppSettings(
                id=1,
                app_name="Test",
                max_file_size_bytes=10 * 1024 * 1024,
                default_expiry_days=7,
                max_share_expiry_days=365,
                max_downloads_default=5,
                max_uploads_default=3,
                purge_grace_days=7,
                setup_completed=True,
            )
        )
        session.add(
            User(
                email="admin@test.com",
                password_hash=hash_password("password123"),
                is_admin=True,
            )
        )
        await session.commit()
    yield


@pytest_asyncio.fixture
async def client():
    async def override_get_db():
        async with async_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def csrf_token(client: AsyncClient, path: str = "/auth/login") -> str:
    response = await client.get(path)
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)
