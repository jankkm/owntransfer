from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import quote

import pytest
from httpx import AsyncClient
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.database import async_session
from app.http.uploads import (
    decode_raw_upload_filename,
    decode_staged_file_ids,
    new_upload_batch,
    validate_upload_batch,
)
from app.models import FileRequest, RequestUpload, Transfer, TransferFile, UploadFile, User
from app.services.file_request import begin_request_upload, finalize_request_upload_files
from app.services.settings import generate_public_token, get_app_settings
from app.services.staging import StagedFile, add_staged_file, get_staged_files
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


def _staging_batch(html: str) -> str:
    match = re.search(r'data-staging-batch="([^"]+)"', html)
    assert match
    return match.group(1)


async def _stage_file(scope: str, name: str, content: bytes) -> None:
    async with async_session() as db:
        app_settings = await get_app_settings(db)
    upload_file = StarletteUploadFile(filename=name, file=BytesIO(content))
    await add_staged_file(scope, upload_file, app_settings)


def test_decode_raw_upload_filename_preserves_unicode():
    filename = "résumé 日本語.pdf"
    assert decode_raw_upload_filename(quote(filename, safe="~()*!.'-")) == filename


@pytest.mark.parametrize("value", [None, "", "bad%name", "%FF", "a" * 256])
def test_decode_raw_upload_filename_rejects_invalid_values(value: str | None):
    with pytest.raises(ValueError, match="Invalid encoded filename"):
        decode_raw_upload_filename(value)


def test_upload_batch_and_staged_file_selection_validation():
    batch = new_upload_batch()
    assert validate_upload_batch(batch) == batch
    with pytest.raises(ValueError, match="Invalid upload batch"):
        validate_upload_batch("../shared")

    file_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    assert decode_staged_file_ids(json.dumps(file_ids)) == file_ids
    with pytest.raises(ValueError, match="Invalid staged file selection"):
        decode_staged_file_ids(json.dumps([file_ids[0], file_ids[0]]))


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
async def test_create_transfer_allows_combined_size_above_per_file_limit():
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        app_settings = await get_app_settings(db)
        per_file_size = 6 * 1024 * 1024
        staged = [
            StagedFile(
                id="first",
                original_name="first.bin",
                storage_path="staging/test/first.bin",
                size_bytes=per_file_size,
                content_type="application/octet-stream",
            ),
            StagedFile(
                id="second",
                original_name="second.bin",
                storage_path="staging/test/second.bin",
                size_bytes=per_file_size,
                content_type="application/octet-stream",
            ),
        ]

        transfer = await create_transfer(
            db,
            user=user,
            title="Multiple large files",
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

        assert sum(file.size_bytes for file in staged) > app_settings.max_file_size_bytes
        assert transfer.is_preparing is True


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
async def test_public_request_upload_page_and_raw_staging_endpoint(client: AsyncClient):
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
    assert 'data-max-file-size-bytes="10485760"' in page.text

    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    batch = _staging_batch(page.text)
    response = await client.post(
        f"/r/{token}/staging",
        content=b"direct request upload",
        headers={
            "Content-Type": "text/plain",
            "X-CSRF-Token": csrf,
            "X-Upload-Filename": "direct.txt",
            "X-Upload-Batch": batch,
        },
    )
    assert response.status_code == 200
    assert response.json()["name"] == "direct.txt"


@pytest.mark.asyncio
async def test_public_request_staging_is_isolated_per_page_batch(client: AsyncClient):
    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        token = generate_public_token()
        db.add(
            FileRequest(
                public_token=token,
                created_by=user.id,
                title="Concurrent visitors",
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                max_uploads=5,
                max_total_bytes=10 * 1024 * 1024,
            )
        )
        await db.commit()

    first_page = await client.get(f"/r/{token}")
    second_page = await client.get(f"/r/{token}")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', first_page.text).group(1)
    first_batch = _staging_batch(first_page.text)
    second_batch = _staging_batch(second_page.text)
    assert first_batch != second_batch

    staged_ids: dict[str, str] = {}
    for batch, filename in (
        (first_batch, "first.txt"),
        (first_batch, "not-listed.txt"),
        (second_batch, "second.txt"),
    ):
        response = await client.post(
            f"/r/{token}/staging",
            content=filename.encode(),
            headers={
                "Content-Type": "text/plain",
                "X-CSRF-Token": csrf,
                "X-Upload-Filename": filename,
                "X-Upload-Batch": batch,
            },
        )
        assert response.status_code == 200
        staged_ids[filename] = response.json()["id"]

    cross_delete = await client.delete(
        f"/r/{token}/staging/{staged_ids['first.txt']}",
        headers={
            "X-CSRF-Token": csrf,
            "X-Upload-Batch": second_batch,
        },
    )
    assert cross_delete.status_code == 404

    first_complete = await client.post(
        f"/r/{token}",
        data={
            "csrf_token": csrf,
            "staging_batch": first_batch,
            "staged_file_ids": json.dumps([staged_ids["first.txt"]]),
        },
    )
    assert first_complete.status_code == 200

    async with async_session() as db:
        uploads = (
            await db.execute(
                select(RequestUpload).options(selectinload(RequestUpload.files))
            )
        ).scalars().all()
        assert len(uploads) == 1
        assert [file.original_name for file in uploads[0].files] == ["first.txt"]

    first_remaining = await client.get(
        f"/r/{token}/staging",
        headers={"X-Upload-Batch": first_batch},
    )
    assert first_remaining.status_code == 200
    assert [file["name"] for file in first_remaining.json()] == ["not-listed.txt"]

    second_complete = await client.post(
        f"/r/{token}",
        data={
            "csrf_token": csrf,
            "staging_batch": second_batch,
            "staged_file_ids": json.dumps([staged_ids["second.txt"]]),
        },
    )
    assert second_complete.status_code == 200

    async with async_session() as db:
        uploads = (
            await db.execute(
                select(RequestUpload).options(selectinload(RequestUpload.files))
            )
        ).scalars().all()
        assert {
            tuple(file.original_name for file in upload.files)
            for upload in uploads
        } == {("first.txt",), ("second.txt",)}


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

    page = await client.get(f"/r/{token}")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    batch = _staging_batch(page.text)
    staged_response = await client.post(
        f"/r/{token}/staging",
        content=b"guest upload",
        headers={
            "Content-Type": "text/plain",
            "X-CSRF-Token": csrf,
            "X-Upload-Filename": "from-guest.txt",
            "X-Upload-Batch": batch,
        },
    )
    assert staged_response.status_code == 200

    response = await client.post(
        f"/r/{token}",
        data={
            "csrf_token": csrf,
            "staging_batch": batch,
            "staged_file_ids": json.dumps([staged_response.json()["id"]]),
        },
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
