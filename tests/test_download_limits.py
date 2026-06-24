from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database import async_session
from app.models import Transfer, TransferFile, User
from app.services.storage import get_storage


async def _csrf_token(client: AsyncClient, path: str) -> str:
    import re

    response = await client.get(path)
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)


async def _unlock_transfer(client: AsyncClient, token: str, *, password: str = "") -> None:
    token_value = await _csrf_token(client, f"/d/{token}")
    response = await client.post(
        f"/d/{token}",
        data={"password": password, "csrf_token": token_value},
        follow_redirects=False,
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_unlock_counts_once_and_allows_all_files_in_session(client: AsyncClient):
    storage = get_storage()
    rel_a = "limits/transfer-a.txt"
    rel_b = "limits/transfer-b.txt"
    await storage.save_file(rel_a, b"file-a")
    await storage.save_file(rel_b, b"file-b")

    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        transfer = Transfer(
            public_token="limit-one",
            created_by=user.id,
            title="Two files",
            max_downloads=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(transfer)
        await session.flush()
        file_a = TransferFile(
            transfer_id=transfer.id,
            original_name="a.txt",
            storage_path=rel_a,
            size_bytes=6,
            content_type="text/plain",
        )
        file_b = TransferFile(
            transfer_id=transfer.id,
            original_name="b.txt",
            storage_path=rel_b,
            size_bytes=6,
            content_type="text/plain",
        )
        session.add(file_a)
        session.add(file_b)
        await session.commit()
        file_a_id = file_a.id
        file_b_id = file_b.id

    page = await client.get("/d/limit-one")
    assert page.status_code == 200
    assert 'href="/d/limit-one/files/' not in page.text

    await _unlock_transfer(client, "limit-one")

    async with async_session() as session:
        transfer = (
            await session.execute(select(Transfer).where(Transfer.public_token == "limit-one"))
        ).scalar_one()
        assert transfer.download_count == 1

    r1 = await client.get(f"/d/limit-one/files/{file_a_id}")
    r2 = await client.get(f"/d/limit-one/files/{file_b_id}")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.content == b"file-a"
    assert r2.content == b"file-b"

    async with async_session() as session:
        transfer = (
            await session.execute(select(Transfer).where(Transfer.public_token == "limit-one"))
        ).scalar_one()
        assert transfer.download_count == 1


@pytest.mark.asyncio
async def test_new_session_blocked_after_download_limit_reached(client: AsyncClient):
    storage = get_storage()
    rel_path = "limits/single.txt"
    await storage.save_file(rel_path, b"only")

    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        transfer = Transfer(
            public_token="limit-exhausted",
            created_by=user.id,
            title="Single slot",
            max_downloads=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(transfer)
        await session.flush()
        session.add(
            TransferFile(
                transfer_id=transfer.id,
                original_name="only.txt",
                storage_path=rel_path,
                size_bytes=4,
                content_type="text/plain",
            )
        )
        await session.commit()

    await _unlock_transfer(client, "limit-exhausted")

    from httpx import ASGITransport

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        blocked_page = await other.get("/d/limit-exhausted")
        assert blocked_page.status_code == 410
        assert "Download limit reached" in blocked_page.text
        assert "application/json" not in blocked_page.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_download_limit_message_is_localized_in_german(client: AsyncClient):
    from app.i18n import LOCALE_COOKIE

    storage = get_storage()
    rel_path = "limits/de-locale.txt"
    await storage.save_file(rel_path, b"de")

    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        transfer = Transfer(
            public_token="limit-de",
            created_by=user.id,
            title="German limit test",
            max_downloads=1,
            download_count=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(transfer)
        await session.flush()
        session.add(
            TransferFile(
                transfer_id=transfer.id,
                original_name="de.txt",
                storage_path=rel_path,
                size_bytes=2,
                content_type="text/plain",
            )
        )
        await session.commit()

    response = await client.get(
        "/d/limit-de",
        cookies={LOCALE_COOKIE: "de"},
    )
    assert response.status_code == 410
    assert "Alle verfügbaren Downloads" in response.text
    assert "All available downloads" not in response.text


@pytest.mark.asyncio
async def test_passwordless_transfer_requires_explicit_unlock(client: AsyncClient):
    storage = get_storage()
    rel_path = "limits/plain.txt"
    await storage.save_file(rel_path, b"plain")

    async with async_session() as session:
        user = (await session.execute(select(User))).scalar_one()
        transfer = Transfer(
            public_token="plain-unlock",
            created_by=user.id,
            title="No password",
            max_downloads=5,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        session.add(transfer)
        await session.flush()
        tf = TransferFile(
            transfer_id=transfer.id,
            original_name="plain.txt",
            storage_path=rel_path,
            size_bytes=5,
            content_type="text/plain",
        )
        session.add(tf)
        await session.commit()
        file_id = tf.id

    page = await client.get("/d/plain-unlock")
    assert page.status_code == 200
    assert "Unlock" in page.text
    assert 'href="/d/plain-unlock/files/' not in page.text

    blocked = await client.get(f"/d/plain-unlock/files/{file_id}")
    assert blocked.status_code == 403

    await _unlock_transfer(client, "plain-unlock")

    after = await client.get("/d/plain-unlock")
    assert 'href="/d/plain-unlock/files/' in after.text

    download = await client.get(f"/d/plain-unlock/files/{file_id}")
    assert download.status_code == 200
    assert download.content == b"plain"
