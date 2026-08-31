from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models import FileRequest, RequestUpload, UploadFile, User
from app.services.archive import load_request_activity
from app.services.file_request import delete_request_upload_file
from app.services.storage.local import LocalStorage


@pytest.mark.asyncio
async def test_delete_request_file_preserves_upload_log_and_timeline():
    async with async_session() as db:
        owner = (await db.execute(select(User).where(User.email == "admin@test.com"))).scalar_one()
        req = FileRequest(
            public_token=f"req-del-{uuid.uuid4().hex[:8]}",
            created_by=owner.id,
            title="Delete test",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_uploads=5,
            max_total_bytes=10 * 1024 * 1024,
            upload_count=1,
        )
        db.add(req)
        await db.flush()
        upload = RequestUpload(
            file_request_id=req.id,
            uploader_name="Guest",
            uploader_email="guest@example.com",
            ip_address="198.51.100.1",
            is_preparing=False,
        )
        db.add(upload)
        await db.flush()
        storage_path = f"requests/{req.id}/report.pdf"
        upload_file = UploadFile(
            upload_id=upload.id,
            original_name="report.pdf",
            storage_path=storage_path,
            size_bytes=2048,
            content_type="application/pdf",
        )
        db.add(upload_file)
        await db.commit()
        request_id = req.id
        upload_id = upload.id
        file_id = upload_file.id

    storage = LocalStorage(settings.upload_dir)
    disk_path = storage.absolute_path(storage_path)
    disk_path.parent.mkdir(parents=True, exist_ok=True)
    disk_path.write_bytes(b"%PDF-test-content")

    async with async_session() as db:
        req = (
            await db.execute(
                select(FileRequest)
                .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
                .where(FileRequest.id == request_id)
            )
        ).scalar_one()
        owner = (await db.execute(select(User).where(User.email == "admin@test.com"))).scalar_one()

        await delete_request_upload_file(
            db,
            req=req,
            file_id=file_id,
            user=owner,
            ip_address="127.0.0.1",
        )

    assert not disk_path.exists()

    async with async_session() as db:
        req = (
            await db.execute(
                select(FileRequest)
                .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
                .where(FileRequest.id == request_id)
            )
        ).scalar_one()
        assert req.upload_count == 1

        upload_count = await db.scalar(
            select(func.count()).select_from(RequestUpload).where(RequestUpload.id == upload_id)
        )
        assert upload_count == 1

        file_row = await db.get(UploadFile, file_id)
        assert file_row is not None
        assert file_row.deleted_at is not None

        request_uploads, timeline = await load_request_activity(db, req)
        assert len(request_uploads) == 1
        assert len(request_uploads[0].files) == 1
        assert request_uploads[0].files[0].deleted_at is not None
        assert len(request_uploads[0].active_files) == 0

        upload_entries = [entry for entry in timeline if entry.kind == "upload"]
        assert len(upload_entries) == 1
        assert upload_entries[0].details["files"][0]["name"] == "report.pdf"
        assert upload_entries[0].details["files"][0]["removed"] is True

        removal_entries = [entry for entry in timeline if entry.kind == "file_removed"]
        assert len(removal_entries) == 1
