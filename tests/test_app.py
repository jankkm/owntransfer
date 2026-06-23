from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db.close()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_test_db.name}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp())

from app.auth.passwords import hash_password, verify_password
from app.database import async_session, engine, get_db
from app.logging_config import SECURITY_LOGGER_NAME, configure_logging
from app.main import app
from app.models import AppSettings, Base, FileRequest, Transfer, User
from app.services.settings import generate_public_token, get_app_settings

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


async def _csrf_token(client: AsyncClient, path: str = "/auth/login") -> str:
    """Fetch a page to obtain a session cookie and its CSRF token."""
    response = await client.get(path)
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)


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


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_setup_redirect_when_not_complete(client: AsyncClient):
    async with async_session() as session:
        settings = await get_app_settings(session)
        settings.setup_completed = False
        await session.commit()
    response = await client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


@pytest.mark.asyncio
async def test_local_login(client: AsyncClient):
    token = await _csrf_token(client)
    response = await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert "owntransfer_session" in response.cookies


def test_ensure_utc_compares_naive_and_aware():
    from app.services.datetime_display import ensure_utc, utc_now

    naive = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    aware = utc_now()
    assert ensure_utc(naive) > aware
    assert ensure_utc(aware) == aware


def test_expiry_notice_only_within_seven_days():
    from app.services.share_lifecycle import build_share_timeline_notice

    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    far = build_share_timeline_notice(
        is_expired=False,
        expires_at=now + timedelta(days=30),
        purge_grace_days=7,
        now=now,
    )
    assert far is None

    soon = build_share_timeline_notice(
        is_expired=False,
        expires_at=now + timedelta(days=3),
        purge_grace_days=7,
        now=now,
    )
    assert soon is not None
    assert soon["variant"] == "expiry"

    at_seven_days = build_share_timeline_notice(
        is_expired=False,
        expires_at=now + timedelta(days=7),
        purge_grace_days=7,
        now=now,
    )
    assert at_seven_days is None


def test_deletion_notice_always_when_pending():
    from app.services.share_lifecycle import build_share_timeline_notice

    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    expires_at = now - timedelta(days=1)

    notice = build_share_timeline_notice(
        is_expired=True,
        expires_at=expires_at,
        purge_grace_days=7,
        now=now,
    )
    assert notice is not None
    assert notice["variant"] == "deletion"


def test_password_hashing():
    hashed = hash_password("secret")
    assert verify_password("secret", hashed)
    assert not verify_password("wrong", hashed)


def test_public_token_length():
    assert len(generate_public_token()) >= 32


@pytest.mark.asyncio
async def test_concurrent_staging_adds_all_files():
    from starlette.datastructures import UploadFile as StarletteUploadFile

    from app.services.staging import add_staged_file, get_staged_files

    async with async_session() as session:
        app_settings = await get_app_settings(session)

    scope = "test-user"

    async def upload(name: str, content: bytes):
        upload_file = StarletteUploadFile(filename=name, file=BytesIO(content))
        return await add_staged_file(scope, upload_file, app_settings)

    await asyncio.gather(
        upload("a.txt", b"aaa"),
        upload("b.txt", b"bbb"),
        upload("c.txt", b"ccc"),
        upload("d.txt", b"ddd"),
    )

    staged = get_staged_files(scope)
    assert len(staged) == 4
    assert {f.original_name for f in staged} == {"a.txt", "b.txt", "c.txt", "d.txt"}


@pytest.mark.asyncio
async def test_invalid_login_security_log(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    token = await _csrf_token(client)
    with caplog.at_level(logging.WARNING, logger=SECURITY_LOGGER_NAME):
        response = await client.post(
            "/auth/login/local",
            data={"email": "nobody@test.com", "password": "wrong", "csrf_token": token},
            follow_redirects=False,
        )
    assert response.status_code == 401
    assert any("event=invalid_login" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_login_without_csrf_is_rejected(client: AsyncClient):
    response = await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123"},
        follow_redirects=False,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_password_protected_transfer_cannot_be_bypassed_with_forged_cookie(client: AsyncClient):
    from app.auth.passwords import hash_password as _hash

    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(
            Transfer(
                public_token="protected-token",
                created_by=user.id,
                title="Secret",
                password_hash=_hash("letmein"),
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
        )
        await session.commit()

    # Forging the old plaintext "1" unlock cookie must NOT unlock the transfer.
    forged = await client.get(
        "/d/protected-token",
        cookies={"unlock_d_protected-token": "1"},
        follow_redirects=False,
    )
    assert forged.status_code == 200
    assert "needs_password" not in forged.text or 'name="password"' in forged.text
    # The ZIP endpoint must reject because no valid signed unlock/grant exists.
    blocked = await client.get(
        "/d/protected-token/download",
        cookies={"unlock_d_protected-token": "1"},
        follow_redirects=False,
    )
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_invalid_transfer_token_security_log(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger=SECURITY_LOGGER_NAME):
        response = await client.get("/d/not-a-real-token", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert any("event=invalid_transfer_link" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_invalid_request_token_security_log(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger=SECURITY_LOGGER_NAME):
        response = await client.get("/r/not-a-real-token", follow_redirects=False)
    assert response.status_code == 303
    assert any("event=invalid_request_link" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_expired_transfer_not_security_logged(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(
            Transfer(
                public_token="expired-transfer-token",
                created_by=user.id,
                title="Expired",
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                is_expired=True,
            )
        )
        await session.commit()

    with caplog.at_level(logging.WARNING, logger=SECURITY_LOGGER_NAME):
        response = await client.get("/d/expired-transfer-token", follow_redirects=False)
    assert response.status_code == 410
    assert not any("event=invalid_transfer_link" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_disabled_transfer_redirects_to_login(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(
            Transfer(
                public_token="disabled-transfer-token",
                created_by=user.id,
                title="Disabled",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                is_disabled=True,
            )
        )
        await session.commit()

    with caplog.at_level(logging.WARNING, logger=SECURITY_LOGGER_NAME):
        response = await client.get("/d/disabled-transfer-token", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert not any("event=invalid_transfer_link" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_disabled_request_not_security_logged(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(
            FileRequest(
                public_token="disabled-request-token",
                created_by=user.id,
                title="Disabled",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                max_uploads=5,
                max_total_bytes=10 * 1024 * 1024,
                is_disabled=True,
            )
        )
        await session.commit()

    with caplog.at_level(logging.WARNING, logger=SECURITY_LOGGER_NAME):
        response = await client.get("/r/disabled-request-token", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert not any("event=invalid_request_link" in record.message for record in caplog.records)
