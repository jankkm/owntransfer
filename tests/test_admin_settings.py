from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.database import async_session
from app.services.settings import get_app_settings
from app.services.share_status import file_request_is_accessible, file_request_inactive_reason_code


def _make_request(max_uploads: int, upload_count: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        is_disabled=False,
        is_expired=False,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        max_uploads=max_uploads,
        upload_count=upload_count,
    )


async def _login_admin(client: AsyncClient) -> str:
    login_page = await client.get("/auth/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', login_page.text).group(1)
    await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=True,
    )
    return token


@pytest.mark.asyncio
async def test_save_share_settings_persists_max_uploads_default(client: AsyncClient):
    token = await _login_admin(client)
    admin_page = await client.get("/admin")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', admin_page.text).group(1)

    response = await client.post(
        "/admin/limits/shares",
        data={
            "default_expiry_days": "14",
            "max_share_expiry_days": "365",
            "max_downloads_default": "5",
            "max_uploads_default": "7",
            "purge_grace_days": "7",
            "purge_notify_days": "0",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin?shares_saved=1"

    async with async_session() as db:
        settings = await get_app_settings(db)
        assert settings.max_uploads_default == 7
        assert settings.max_downloads_default == 5


@pytest.mark.asyncio
async def test_new_request_form_prefills_max_uploads_default(client: AsyncClient):
    await _login_admin(client)
    response = await client.get("/requests/new")
    assert response.status_code == 200
    # conftest seeds max_uploads_default=3; the form value should reflect it
    assert 'value="3"' in response.text


@pytest.mark.asyncio
async def test_new_transfer_form_prefills_max_downloads_default(client: AsyncClient):
    await _login_admin(client)
    response = await client.get("/transfers/new")
    assert response.status_code == 200
    # conftest seeds max_downloads_default=5; the form value should reflect it
    assert 'value="5"' in response.text


def test_max_uploads_zero_means_unlimited():
    now = datetime.now(timezone.utc)
    req = _make_request(max_uploads=0, upload_count=999)
    assert file_request_is_accessible(req, now) is True
    assert file_request_inactive_reason_code(req, now) is None


def test_max_uploads_nonzero_blocks_when_limit_reached():
    now = datetime.now(timezone.utc)
    req = _make_request(max_uploads=5, upload_count=5)
    assert file_request_is_accessible(req, now) is False
    assert file_request_inactive_reason_code(req, now) == "upload_limit"


def test_max_uploads_nonzero_allows_when_under_limit():
    now = datetime.now(timezone.utc)
    req = _make_request(max_uploads=5, upload_count=4)
    assert file_request_is_accessible(req, now) is True
