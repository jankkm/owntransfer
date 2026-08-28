from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.database import async_session
from app.models import FileRequest, RequestUpload, Transfer, TransferFile, UploadFile, User
from app.services.file_request import begin_request_upload, finalize_request_upload_files
from app.services.settings import generate_public_token, get_app_settings
from app.services.staging import add_staged_file, get_staged_files
from app.services.transfer import create_transfer, finalize_transfer_files


async def _csrf_token(client: AsyncClient, path: str = "/auth/login") -> str:
    response = await client.get(path)
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)


async def _login(client: AsyncClient) -> None:
    token = await _csrf_token(client)
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
async def test_create_transfer_with_staged_files_marks_preparing():
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        app_settings = await get_app_settings(db)
        scope = f"transfer_{user.id}"
        await _stage_file(scope, "hello.txt", b"hello world")
        staged = get_staged_files(scope)

        transfer = await create_transfer(
            db,
            user=user,
            title="Async transfer",
            message=None,
            password=None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=5,
            notify_on_download=False,
            recipient_emails=[],
            app_settings=app_settings,
            ip_address="127.0.0.1",
            staged_files=staged,
        )
        assert transfer.is_preparing is True

    async with async_session() as db:
        file_count = await db.scalar(
            select(func.count()).select_from(TransferFile).where(TransferFile.transfer_id == transfer.id)
        )
        assert file_count == 0


@pytest.mark.asyncio
async def test_finalize_transfer_files_moves_staged_files():
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        app_settings = await get_app_settings(db)
        scope = f"transfer_{user.id}"
        await _stage_file(scope, "hello.txt", b"hello world")
        staged = get_staged_files(scope)

        transfer = await create_transfer(
            db,
            user=user,
            title="Async transfer",
            message=None,
            password=None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=5,
            notify_on_download=False,
            recipient_emails=[],
            app_settings=app_settings,
            ip_address="127.0.0.1",
            staged_files=staged,
        )

    await finalize_transfer_files(
        transfer.id,
        staged,
        user_id=user.id,
        title="Async transfer",
        message=None,
        password=None,
        recipient_emails=[],
        ip_address="127.0.0.1",
    )

    async with async_session() as db:
        result = await db.execute(
            select(Transfer).options(selectinload(Transfer.files))
        )
        transfer = result.scalar_one()
        assert transfer.is_preparing is False
        assert len(transfer.files) == 1
        assert transfer.files[0].original_name == "hello.txt"


@pytest.mark.asyncio
async def test_begin_request_upload_marks_preparing():
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        app_settings = await get_app_settings(db)
        req = FileRequest(
            public_token=generate_public_token(),
            created_by=user.id,
            title="Upload here",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_uploads=5,
            max_total_bytes=10 * 1024 * 1024,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)

        scope = f"request_{req.public_token}"
        await _stage_file(scope, "from-guest.txt", b"guest upload")
        staged = get_staged_files(scope)

        upload = await begin_request_upload(
            db,
            req=req,
            staged_files=staged,
            uploader_name="Guest",
            uploader_email=None,
            app_settings=app_settings,
            ip_address="127.0.0.1",
        )
        assert upload.is_preparing is True
        assert req.upload_count == 1

    async with async_session() as db:
        file_count = await db.scalar(
            select(func.count()).select_from(UploadFile).where(UploadFile.upload_id == upload.id)
        )
        assert file_count == 0


@pytest.mark.asyncio
async def test_finalize_request_upload_files_moves_staged_files():
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        app_settings = await get_app_settings(db)
        req = FileRequest(
            public_token=generate_public_token(),
            created_by=user.id,
            title="Upload here",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_uploads=5,
            max_total_bytes=10 * 1024 * 1024,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)

        scope = f"request_{req.public_token}"
        await _stage_file(scope, "from-guest.txt", b"guest upload")
        staged = get_staged_files(scope)

        upload = await begin_request_upload(
            db,
            req=req,
            staged_files=staged,
            uploader_name="Guest",
            uploader_email=None,
            app_settings=app_settings,
            ip_address="127.0.0.1",
        )

    await finalize_request_upload_files(
        upload.id,
        req.id,
        staged,
        uploader_name="Guest",
        uploader_email=None,
        ip_address="127.0.0.1",
    )

    async with async_session() as db:
        result = await db.execute(
            select(RequestUpload).options(selectinload(RequestUpload.files))
        )
        upload = result.scalar_one()
        assert upload.is_preparing is False
        assert len(upload.files) == 1
        assert upload.files[0].original_name == "from-guest.txt"


@pytest.mark.asyncio
async def test_public_request_upload_page_has_staging_endpoint(client: AsyncClient):
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

    page = await client.get(f"/r/{token}")
    assert re.search(rf'/r/{re.escape(token)}/staging', page.text)


@pytest.mark.asyncio
async def test_public_request_upload_http_flow(client: AsyncClient):
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

    scope = f"request_{token}"
    await _stage_file(scope, "from-guest.txt", b"guest upload")

    csrf = await _csrf_token(client, f"/r/{token}")
    response = await client.post(
        f"/r/{token}",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Upload successful" in response.text

    async with async_session() as db:
        result = await db.execute(
            select(RequestUpload).options(selectinload(RequestUpload.files))
        )
        upload = result.scalar_one()
        assert upload.is_preparing is False
        assert len(upload.files) == 1
