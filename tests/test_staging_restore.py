from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.database import async_session
from app.models import FileRequest, User
from app.services.settings import generate_public_token, get_app_settings
from app.services.staging import add_staged_file, get_staged_files, purge_stale_staging
from app.services.storage import get_storage


async def _login(client: AsyncClient) -> None:
    response = await client.get("/auth/login")
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match
    token = match.group(1)
    response = await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def _stage_file(scope: str, name: str, content: bytes) -> None:
    async with async_session() as db:
        app_settings = await get_app_settings(db)
    upload_file = StarletteUploadFile(filename=name, file=BytesIO(content))
    await add_staged_file(scope, upload_file, app_settings)


@pytest.mark.asyncio
async def test_list_staged_transfer_files(client: AsyncClient):
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        scope = f"transfer_{user.id}"

    await _login(client)
    await _stage_file(scope, "notes.pdf", b"%PDF-1.4")

    response = await client.get("/transfers/staging")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["name"] == "notes.pdf"
    assert payload[0]["size_bytes"] == len(b"%PDF-1.4")
    assert payload[0]["id"]


@pytest.mark.asyncio
async def test_clear_staged_transfer_files(client: AsyncClient):
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        scope = f"transfer_{user.id}"

    await _login(client)
    csrf = await client.get("/transfers/new")
    match = re.search(r'name="csrf-token" content="([^"]+)"', csrf.text)
    assert match
    csrf_token = match.group(1)

    await _stage_file(scope, "notes.pdf", b"%PDF-1.4")
    assert len(get_staged_files(scope)) == 1

    response = await client.delete(
        "/transfers/staging",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert get_staged_files(scope) == []


@pytest.mark.asyncio
async def test_purge_stale_staging_removes_old_keeps_fresh():
    storage = get_storage()
    old_scope = "test_purge_old"
    fresh_scope = "test_purge_fresh"
    await _stage_file(old_scope, "old.txt", b"old")
    await _stage_file(fresh_scope, "fresh.txt", b"fresh")

    old_staged = get_staged_files(old_scope)[0]
    old_path = storage.absolute_path(old_staged.storage_path)
    stale_time = time.time() - (25 * 3600)
    os.utime(old_path, (stale_time, stale_time))

    removed = await purge_stale_staging(max_age_hours=24)
    assert removed >= 1
    assert get_staged_files(old_scope) == []
    assert len(get_staged_files(fresh_scope)) == 1


@pytest.mark.asyncio
async def test_create_transfer_via_post_with_staged_files(client: AsyncClient):
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        scope = f"transfer_{user.id}"

    await _login(client)
    new_page = await client.get("/transfers/new")
    csrf_match = re.search(r'name="csrf-token" content="([^"]+)"', new_page.text)
    assert csrf_match
    csrf_token = csrf_match.group(1)

    await _stage_file(scope, "notes.pdf", b"%PDF-1.4")

    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
    response = await client.post(
        "/transfers/new",
        data={
            "title": "Staged transfer",
            "message": "",
            "expires_at": expiry,
            "max_downloads": "5",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "created=" in response.headers["location"]


@pytest.mark.asyncio
async def test_transfers_new_page_enables_staged_restore(client: AsyncClient):
    await _login(client)
    response = await client.get("/transfers/new")
    assert response.status_code == 200
    assert 'data-restore-staged="true"' in response.text
    assert 'data-clear-all-url="/transfers/staging"' in response.text


@pytest.mark.asyncio
async def test_public_request_upload_page_does_not_restore_staged(client: AsyncClient):
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        token = generate_public_token()
        db.add(
            FileRequest(
                public_token=token,
                created_by=user.id,
                title="Upload here",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                max_uploads=5,
                max_total_bytes=10 * 1024 * 1024,
            )
        )
        await db.commit()

    response = await client.get(f"/r/{token}")
    assert response.status_code == 200
    assert 'data-restore-staged="true"' not in response.text
