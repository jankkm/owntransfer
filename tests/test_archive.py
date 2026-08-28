from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models import ArchivedShare, AuditLog, FileRequest, RequestUpload, Transfer, TransferDownloadLog, TransferFile, UploadFile, User
from app.services.admin_archive import purge_archived_shares
from app.services.audit import list_system_audit
from app.services.archive import archive_share_before_delete
from app.services.settings import get_app_settings
from app.services.share_timeline import build_request_timeline, build_transfer_timeline

async def _login_admin(client: AsyncClient) -> str:
    login_page = await client.get("/auth/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', login_page.text).group(1)
    await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=True,
    )
    return token


async def _create_transfer_with_file() -> Transfer:
    async with async_session() as db:
        admin = (
            await db.execute(select(User).where(User.email == "admin@test.com"))
        ).scalar_one()
        transfer = Transfer(
            public_token=f"archive-test-{uuid.uuid4().hex[:8]}",
            created_by=admin.id,
            title="Archive me",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=10,
        )
        db.add(transfer)
        await db.flush()
        db.add(
            TransferFile(
                transfer_id=transfer.id,
                original_name="report.pdf",
                storage_path=f"transfers/{transfer.id}/test/report.pdf",
                size_bytes=1024,
                content_type="application/pdf",
            )
        )
        db.add(
            TransferDownloadLog(
                transfer_id=transfer.id,
                ip_address="203.0.113.1",
                download_type="file",
                file_name="report.pdf",
            )
        )
        await db.commit()
        await db.refresh(transfer)
        return transfer


@pytest.mark.asyncio
async def test_delete_transfer_creates_archive(client: AsyncClient):
    transfer = await _create_transfer_with_file()
    token = await _login_admin(client)
    edit_page = await client.get(f"/transfers/{transfer.id}/edit")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', edit_page.text).group(1)

    response = await client.post(
        f"/transfers/{transfer.id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with async_session() as db:
        archived = (
            await db.execute(select(ArchivedShare).where(ArchivedShare.original_id == transfer.id))
        ).scalar_one_or_none()
        assert archived is not None
        assert archived.archived_reason == "user_deleted"
        assert archived.file_count == 1
        snapshot = json.loads(archived.snapshot_json)
        assert snapshot["files"][0]["name"] == "report.pdf"
        assert len(snapshot["download_logs"]) == 1
        assert snapshot["download_logs"][0]["ip_address"] == "203.0.113.1"
        audit_actions = [e["action"] for e in snapshot["audit_events"]]
        assert "transfer.deleted" in audit_actions
        gone = await db.get(Transfer, transfer.id)
        assert gone is None
        archive_id = archived.id

    detail = await client.get(f"/admin/shares/archive/{archive_id}?tab=archive")
    assert detail.status_code == 200
    assert "Archive me" in detail.text


@pytest.mark.asyncio
async def test_transfer_edit_shows_timeline(client: AsyncClient):
    transfer = await _create_transfer_with_file()
    token = await _login_admin(client)
    response = await client.get(f"/transfers/{transfer.id}/edit")
    assert response.status_code == 200
    assert "Activity timeline" in response.text
    assert "Download log" in response.text
    assert "203.0.113.1" in response.text


@pytest.mark.asyncio
async def test_build_transfer_timeline_merges_downloads():
    async with async_session() as db:
        admin = (
            await db.execute(select(User).where(User.email == "admin@test.com"))
        ).scalar_one()
        transfer = Transfer(
            public_token=f"timeline-{uuid.uuid4().hex[:8]}",
            created_by=admin.id,
            title="Timeline",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=5,
        )
        db.add(transfer)
        await db.flush()
        log = TransferDownloadLog(
            transfer_id=transfer.id,
            ip_address="198.51.100.2",
            download_type="zip",
            file_name=None,
        )
        db.add(log)
        audit = AuditLog(
            action="transfer.created",
            resource_type="transfer",
            resource_id=str(transfer.id),
            actor_id=admin.id,
        )
        db.add(audit)
        coarse = AuditLog(
            action="transfer.downloaded",
            resource_type="transfer",
            resource_id=str(transfer.id),
        )
        db.add(coarse)
        await db.commit()

        timeline = build_transfer_timeline(
            download_logs=[log],
            audit_events=[coarse, audit],
            actor_emails={admin.id: admin.email},
        )
        assert len(timeline) == 2
        kinds = {entry.kind for entry in timeline}
        assert "download" in kinds
        assert "created" in kinds
        assert "downloaded" not in kinds


@pytest.mark.asyncio
async def test_build_request_timeline_skips_coarse_upload_audit():
    async with async_session() as db:
        admin = (
            await db.execute(select(User).where(User.email == "admin@test.com"))
        ).scalar_one()
        req = FileRequest(
            public_token=f"req-timeline-{uuid.uuid4().hex[:8]}",
            created_by=admin.id,
            title="Request timeline",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_uploads=5,
            max_total_bytes=10 * 1024 * 1024,
        )
        db.add(req)
        await db.flush()
        upload = RequestUpload(
            file_request_id=req.id,
            ip_address="198.51.100.3",
            is_preparing=False,
        )
        db.add(upload)
        await db.flush()
        db.add(
            UploadFile(
                upload_id=upload.id,
                original_name="report.pdf",
                storage_path="/tmp/report.pdf",
                size_bytes=1024,
            )
        )
        coarse = AuditLog(
            action="file_request.uploaded",
            resource_type="file_request",
            resource_id=str(req.id),
            ip_address="198.51.100.3",
            metadata={"file_count": 1},
        )
        db.add(coarse)
        await db.commit()

        result = await db.execute(
            select(RequestUpload)
            .options(selectinload(RequestUpload.files))
            .where(RequestUpload.id == upload.id)
        )
        upload = result.scalar_one()

        timeline = build_request_timeline(
            uploads=[upload],
            audit_events=[coarse],
            actor_emails={admin.id: admin.email},
        )
        assert len(timeline) == 1
        assert timeline[0].kind == "upload"
        assert timeline[0].details.get("files")
        assert "uploaded" not in {entry.kind for entry in timeline}


@pytest.mark.asyncio
async def test_system_audit_excludes_share_events():
    async with async_session() as db:
        db.add(
            AuditLog(action="user.login", resource_type="user", resource_id="x")
        )
        db.add(
            AuditLog(
                action="transfer.created",
                resource_type="transfer",
                resource_id=str(uuid.uuid4()),
            )
        )
        await db.commit()
        rows = await list_system_audit(db, limit=50)
        assert all(r.resource_type not in ("transfer", "file_request") for r in rows)
        assert any(r.action == "user.login" for r in rows)


@pytest.mark.asyncio
async def test_purge_archived_shares():
    async with async_session() as db:
        settings = await get_app_settings(db)
        settings.archive_retention_days = 1
        await db.commit()

        archived = ArchivedShare(
            original_id=uuid.uuid4(),
            resource_type="transfer",
            archived_reason="user_deleted",
            creator_id=uuid.uuid4(),
            creator_email="old@test.com",
            title="Old",
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
            expires_at=datetime.now(timezone.utc) - timedelta(days=20),
            file_count=0,
            total_bytes=0,
            activity_count=0,
            snapshot_json="{}",
            archived_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        db.add(archived)
        db.add(
            AuditLog(
                action="transfer.created",
                resource_type="transfer",
                resource_id=str(archived.original_id),
            )
        )
        await db.commit()
        archive_id = archived.id
        original_id = str(archived.original_id)

    async with async_session() as db:
        count = await purge_archived_shares(db)
        assert count == 1
        assert await db.get(ArchivedShare, archive_id) is None
        audit_left = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.resource_type == "transfer",
                    AuditLog.resource_id == original_id,
                )
            )
        ).scalar_one_or_none()
        assert audit_left is None


@pytest.mark.asyncio
async def test_admin_archive_tab(client: AsyncClient):
    transfer = await _create_transfer_with_file()
    async with async_session() as db:
        admin = (
            await db.execute(select(User).where(User.email == "admin@test.com"))
        ).scalar_one()
        await archive_share_before_delete(
            db,
            resource_type="transfer",
            entity=transfer,
            reason="user_deleted",
            deleted_by=admin,
        )
        await db.delete(transfer)
        await db.commit()

    await _login_admin(client)
    response = await client.get("/admin/shares?tab=archive")
    assert response.status_code == 200
    assert "Archive me" in response.text
    assert "Archive (" in response.text

    requests_tab = await client.get("/admin/shares?tab=requests")
    assert requests_tab.status_code == 200
    assert re.search(r"Archive\s*\(\s*1\s*\)", requests_tab.text)
