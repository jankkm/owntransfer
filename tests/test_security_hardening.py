from __future__ import annotations

import pytest

from app.http.csrf import CSRFMiddleware
from app.services.branding import normalize_hex_color
from app.services.unlock_lockout import (
    UNLOCK_MAX_ATTEMPTS,
    clear_unlock_lockout_store,
    is_unlock_locked,
    record_failed_unlock,
    reset_unlock_lockout,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#2563eb", "#2563eb"),
        ("#ABCDEF", "#abcdef"),
        ("  #112233  ", "#112233"),
    ],
)
def test_normalize_hex_color_accepts_valid_values(value: str, expected: str):
    assert normalize_hex_color(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "2563eb", "#abc", "#gggggg", "red", "#2563eb; background: url(https://evil)"],
)
def test_normalize_hex_color_rejects_invalid_values(value: str):
    assert normalize_hex_color(value) is None


@pytest.mark.asyncio
async def test_unlock_lockout_after_max_attempts():
    clear_unlock_lockout_store()
    token = "share-token"

    for _ in range(UNLOCK_MAX_ATTEMPTS - 1):
        assert await record_failed_unlock("transfer", token) is False
        assert await is_unlock_locked("transfer", token) is False

    assert await record_failed_unlock("transfer", token) is True
    assert await is_unlock_locked("transfer", token) is True


@pytest.mark.asyncio
async def test_unlock_lockout_resets_on_success():
    clear_unlock_lockout_store()
    token = "share-token"

    for _ in range(UNLOCK_MAX_ATTEMPTS):
        await record_failed_unlock("request", token)
    assert await is_unlock_locked("request", token) is True

    await reset_unlock_lockout("request", token)
    assert await is_unlock_locked("request", token) is False


@pytest.mark.asyncio
async def test_csrf_rejects_oversized_body_without_header():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login/local",
        "headers": [
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"content-length", b"100000"),
        ],
        "session": {"csrf_token": "expected"},
    }
    status_code: int | None = None

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = message["status"]

    middleware = CSRFMiddleware(lambda scope, receive, send: None)
    await middleware(scope, receive, send)
    assert status_code == 403


@pytest.mark.asyncio
async def test_transfer_unlock_lockout_blocks_after_repeated_failures(client):
    from datetime import datetime, timedelta, timezone

    from httpx import AsyncClient
    from sqlalchemy import select

    from app.auth.passwords import hash_password as _hash
    from app.database import async_session
    from app.models import Transfer, User
    from app.services.unlock_lockout import UNLOCK_MAX_ATTEMPTS, clear_unlock_lockout_store
    from tests.conftest import csrf_token

    clear_unlock_lockout_store()
    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        session.add(
            Transfer(
                public_token="lockout-token",
                created_by=user.id,
                title="Secret",
                password_hash=_hash("letmein"),
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
        )
        await session.commit()

    for _ in range(UNLOCK_MAX_ATTEMPTS):
        token = await csrf_token(client, "/d/lockout-token")
        response = await client.post(
            "/d/lockout-token",
            data={"password": "wrong", "csrf_token": token},
        )
        assert response.status_code == 401

    token = await csrf_token(client, "/d/lockout-token")
    locked = await client.post(
        "/d/lockout-token",
        data={"password": "wrong", "csrf_token": token},
    )
    assert locked.status_code == 429
    assert "Too many failed password attempts" in locked.text
