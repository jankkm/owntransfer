from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient

from app.database import async_session
from app.logging_config import configure_logging
from app.main import app
from app.services.settings import get_app_settings
from app.services.setup_token import init_setup_token, log_setup_token_if_needed
from tests.conftest import csrf_token

SETUP_LOGGER = "owntransfer.setup"


@pytest.mark.asyncio
async def test_setup_logs_generated_token(monkeypatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setattr("app.services.setup_token.settings.setup_token", None)
    configure_logging()
    init_setup_token(app)

    async with async_session() as db:
        settings = await get_app_settings(db)
        settings.setup_completed = False
        await db.commit()

    with caplog.at_level(logging.WARNING, logger=SETUP_LOGGER):
        async with async_session() as db:
            await log_setup_token_if_needed(app, db)

    assert any("Setup token (required once):" in record.message for record in caplog.records)
    assert app.state.setup_token in caplog.text


@pytest.mark.asyncio
async def test_setup_does_not_log_when_complete(caplog: pytest.LogCaptureFixture):
    configure_logging()
    init_setup_token(app)

    with caplog.at_level(logging.WARNING, logger=SETUP_LOGGER):
        async with async_session() as db:
            await log_setup_token_if_needed(app, db)

    assert not any("Setup token (required once):" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_setup_requires_valid_token(client: AsyncClient):
    async with async_session() as db:
        settings = await get_app_settings(db)
        settings.setup_completed = False
        await db.commit()

    token = await csrf_token(client, "/setup")
    response = await client.post(
        "/setup",
        data={
            "setup_token": "wrong-token",
            "app_name": "My App",
            "email": "admin@example.com",
            "password": "password123",
            "csrf_token": token,
        },
    )
    assert response.status_code == 400
    assert "Invalid setup token" in response.text

    token = await csrf_token(client, "/setup")
    response = await client.post(
        "/setup",
        data={
            "setup_token": "test-setup-token",
            "app_name": "My App",
            "email": "admin@example.com",
            "password": "password123",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"

    async with async_session() as db:
        settings = await get_app_settings(db)
        assert settings.setup_completed is True
