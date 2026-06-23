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
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp())

from app.i18n import (
    LOCALE_COOKIE,
    activate,
    gettext,
    negotiate_from_header,
    ngettext,
    normalize_locale,
    resolve_locale,
)
from app.main import app
from app.database import async_session, engine, get_db
from app.models import AppSettings, Base, User
from app.auth.passwords import hash_password


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


def test_normalize_locale():
    assert normalize_locale("de-DE") == "de"
    assert normalize_locale("en_US") == "en"
    assert normalize_locale("fr") is None


def test_negotiate_from_header():
    assert negotiate_from_header("de-DE,de;q=0.9,en;q=0.8") == "de"
    assert negotiate_from_header("fr-FR,fr;q=0.9") is None


def test_gettext_german_translation():
    activate("de")
    assert gettext("Dashboard") == "Dashboard"
    assert gettext("Login") == "Anmelden"
    assert gettext("Invalid credentials") == "Ungültige Anmeldedaten"


def test_ngettext_german():
    activate("de")
    assert ngettext("%(count)s day", "%(count)s days", 1) % {"count": 1} == "1 Tag"
    assert ngettext("%(count)s day", "%(count)s days", 3) % {"count": 3} == "3 Tage"


@pytest.mark.asyncio
async def test_accept_language_selects_german(client: AsyncClient):
    response = await client.get("/auth/login", headers={"Accept-Language": "de-DE,de;q=0.9"})
    assert response.status_code == 200
    assert 'lang="de"' in response.text
    assert "Anmelden" in response.text


@pytest.mark.asyncio
async def test_locale_cookie_overrides_accept_language(client: AsyncClient):
    response = await client.get(
        "/auth/login",
        headers={"Accept-Language": "de-DE"},
        cookies={LOCALE_COOKIE: "en"},
    )
    assert response.status_code == 200
    assert 'lang="en"' in response.text
    assert "Sign in" in response.text


@pytest.mark.asyncio
async def test_post_locale_sets_cookie(client: AsyncClient):
    page = await client.get("/auth/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    response = await client.post(
        "/locale",
        data={"locale": "de", "csrf_token": token},
        headers={"Referer": "http://test/auth/login"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.cookies[LOCALE_COOKIE] == "de"

    page = await client.get("/auth/login", cookies=response.cookies)
    assert "Anmelden" in page.text


@pytest.mark.asyncio
async def test_footer_language_switcher_present(client: AsyncClient):
    response = await client.get("/auth/login")
    assert response.status_code == 200
    assert 'action="/locale"' in response.text
    assert 'value="de"' in response.text
    assert 'value="en"' in response.text


def test_resolve_locale_fallback():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)
    assert resolve_locale(request) == "en"
