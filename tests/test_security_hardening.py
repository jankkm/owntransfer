from __future__ import annotations

import pytest

from app.http.csrf import CSRFMiddleware
from app.http.safe_redirect import safe_next_path, safe_redirect_target
from app.services.branding import normalize_hex_color
from app.services.download_limits import try_reserve_download_slot
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


def test_safe_redirect_target_allows_same_origin_path():
    from starlette.requests import Request

    class _Request(Request):
        @property
        def base_url(self):
            return "http://test/"

    req = _Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/locale",
            "headers": [(b"referer", b"http://test/dashboard?q=1")],
        }
    )
    assert safe_redirect_target(req) == "/dashboard?q=1"


def test_safe_redirect_target_rejects_external_referer():
    from starlette.requests import Request

    class _Request(Request):
        @property
        def base_url(self):
            return "http://test/"

    req = _Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/locale",
            "headers": [(b"referer", b"https://evil.example/phish")],
        }
    )
    assert safe_redirect_target(req) == "/"


def test_safe_next_path_allows_relative_path():
    from starlette.requests import Request

    class _Request(Request):
        @property
        def base_url(self):
            return "http://test/"

    req = _Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert safe_next_path(req, "/requests/abc/edit") == "/requests/abc/edit"
    assert safe_next_path(req, "/requests/abc/edit?q=1") == "/requests/abc/edit?q=1"


def test_safe_next_path_rejects_external_and_login_loop():
    from starlette.requests import Request

    class _Request(Request):
        @property
        def base_url(self):
            return "http://test/"

    req = _Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert safe_next_path(req, "https://evil.example/phish") == "/"
    assert safe_next_path(req, "//evil.example/phish") == "/"
    assert safe_next_path(req, "/auth/login?next=/dashboard") == "/"


@pytest.mark.asyncio
async def test_unauthenticated_html_page_redirects_to_login_with_next(client):
    response = await client.get("/transfers", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?next=%2Ftransfers"


@pytest.mark.asyncio
async def test_unauthenticated_api_returns_json_401(client):
    response = await client.get(
        "/dashboard",
        headers={"Accept": "application/json", "Sec-Fetch-Dest": "empty"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_login_redirects_back_to_next_page(client):
    import re

    from conftest import csrf_token

    await client.get("/auth/login?next=/transfers", follow_redirects=False)
    token = await csrf_token(client)
    response = await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/transfers"


@pytest.mark.asyncio
async def test_login_tab_switch_preserves_next(client):
    from conftest import csrf_token

    await client.get("/auth/login?next=%2Ftransfers&tab=oauth", follow_redirects=False)
    await client.get("/auth/login?tab=local", follow_redirects=False)
    token = await csrf_token(client, "/auth/login?tab=local")
    response = await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/transfers"


@pytest.mark.asyncio
async def test_logged_in_user_on_missing_request_edit_goes_to_dashboard(client):
    import re
    import uuid

    from conftest import csrf_token

    token = await csrf_token(client)
    await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=True,
    )
    response = await client.get(f"/requests/{uuid.uuid4()}/edit", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_login_after_next_to_missing_request_goes_to_dashboard(client):
    import re
    import uuid

    from conftest import csrf_token

    missing_id = uuid.uuid4()
    await client.get(f"/auth/login?next=/requests/{missing_id}/edit", follow_redirects=False)
    token = await csrf_token(client)
    response = await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/requests/{missing_id}/edit"

    follow = await client.get(response.headers["location"], follow_redirects=False)
    assert follow.status_code == 303
    assert follow.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_post_locale_rejects_external_referer(client):
    import re

    from tests.conftest import csrf_token

    token = await csrf_token(client, "/auth/login")
    response = await client.post(
        "/locale",
        data={"locale": "de", "csrf_token": token},
        headers={"Referer": "https://evil.example/phish"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_admin_rejects_invalid_color_scheme(client):
    import re

    from tests.conftest import csrf_token

    token = await csrf_token(client, "/auth/login")
    await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=True,
    )
    admin_page = await client.get("/admin")
    token = re.search(r'name="csrf-token" content="([^"]+)"', admin_page.text).group(1)
    response = await client.post(
        "/admin/branding",
        data={
            "app_name": "OwnTransfer",
            "color_scheme": "#2563eb; background: url(https://evil)",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_get_client_ip_ignores_spoofed_forwarded_without_trusted_proxy(monkeypatch):
    from starlette.requests import Request

    from app.http.client_ip import get_client_ip

    monkeypatch.setattr("app.http.client_ip.settings.trust_proxy_headers", False)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.99")],
            "client": ("10.0.0.5", 12345),
        }
    )
    assert get_client_ip(request) == "10.0.0.5"


def test_get_client_ip_honors_forwarded_from_trusted_proxy(monkeypatch):
    from starlette.requests import Request

    from app.http.client_ip import get_client_ip

    monkeypatch.setattr("app.http.client_ip.settings.trust_proxy_headers", True)
    monkeypatch.setattr("app.http.client_ip.settings.trusted_proxy_ips", "10.0.0.5")

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-real-ip", b"203.0.113.99")],
            "client": ("10.0.0.5", 12345),
        }
    )
    assert get_client_ip(request) == "203.0.113.99"


@pytest.mark.asyncio
async def test_try_reserve_download_slot_is_atomic():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.database import async_session
    from app.models import Transfer, User

    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        transfer = Transfer(
            public_token="atomic-token",
            created_by=user.id,
            title="Limited",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=1,
            download_count=0,
        )
        db.add(transfer)
        await db.commit()
        await db.refresh(transfer)

        assert await try_reserve_download_slot(db, transfer.id, max_downloads=1) is True
        await db.refresh(transfer)
        assert transfer.download_count == 1
        assert await try_reserve_download_slot(db, transfer.id, max_downloads=1) is False
        await db.refresh(transfer)
        assert transfer.download_count == 1
