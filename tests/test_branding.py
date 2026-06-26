from __future__ import annotations

import hashlib
import re

import pytest
from httpx import AsyncClient

from app.database import async_session
from app.models import AppSettings
from app.services.branding import DEFAULT_LOGO_URL, logo_url
from app.services.settings import get_app_settings


def _settings_with_logo(data: bytes) -> AppSettings:
    settings = AppSettings(
        id=99,
        app_name="Test",
        max_file_size_bytes=10 * 1024 * 1024,
    )
    settings.logo_data = data
    settings.logo_content_type = "image/png"
    return settings


def test_logo_url_without_custom_logo():
    settings = AppSettings(id=99, app_name="Test", max_file_size_bytes=1024)
    settings.logo_data = None
    assert logo_url(settings) == DEFAULT_LOGO_URL


def test_logo_url_includes_content_hash():
    data = b"logo-bytes-v1"
    settings = _settings_with_logo(data)
    version = hashlib.sha256(data).hexdigest()[:12]
    assert logo_url(settings) == f"/branding/logo?v={version}"


def test_logo_url_changes_when_content_changes():
    first = _settings_with_logo(b"logo-a")
    second = _settings_with_logo(b"logo-b")
    assert logo_url(first) != logo_url(second)


@pytest.mark.asyncio
async def test_large_logo_upload_with_csrf_header(client: AsyncClient):
    token = await _login_admin(client)
    admin_page = await client.get("/admin")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', admin_page.text).group(1)

    logo_bytes = b"\x89PNG\r\n\x1a\n" + (b"x" * 100_000)
    response = await client.post(
        "/admin/branding",
        data={
            "app_name": "OwnTransfer",
            "color_scheme": "#2563eb",
            "csrf_token": csrf,
        },
        files={"logo": ("logo.png", logo_bytes, "image/png")},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin?branding_saved=1"

    async with async_session() as db:
        app_settings = await get_app_settings(db)
        assert app_settings.logo_data == logo_bytes
        assert logo_url(app_settings).startswith("/branding/logo?v=")


async def _login_admin(client: AsyncClient) -> str:
    login_page = await client.get("/auth/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', login_page.text).group(1)
    await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=True,
    )
    return token
