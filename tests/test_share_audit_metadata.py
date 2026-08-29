from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.database import async_session
from app.models import AuditLog, FileRequest, Transfer, User
from app.services.file_request import create_file_request, regenerate_file_request_link
from app.services.share_audit_metadata import (
    build_transfer_update_changes,
    serialize_file_row,
)
from app.services.transfer import regenerate_transfer_link, update_transfer
from app.services.settings import get_app_settings


@pytest.mark.asyncio
async def test_build_transfer_update_changes_detects_diffs():
    now = datetime.now(timezone.utc)
    later = now + timedelta(days=14)
    changes = build_transfer_update_changes(
        old_title="Old",
        new_title="New",
        old_message="Hi",
        new_message="Hello",
        old_expires_at=now,
        new_expires_at=later,
        old_max_downloads=5,
        new_max_downloads=10,
        old_notify_on_download=False,
        new_notify_on_download=True,
        had_password=False,
        remove_password=False,
        new_password=None,
        old_enabled=True,
        enabled=None,
    )
    fields = {c["field"] for c in changes}
    assert "title" in fields
    assert "message" in fields
    assert "expires_at" in fields
    assert "max_downloads" in fields
    assert "notify_on_download" in fields
    assert "password" not in fields


def test_serialize_file_row():
    row = serialize_file_row("notes.pdf", 2048, "application/pdf")
    assert row["name"] == "notes.pdf"
    assert row["size_bytes"] == 2048
    assert row["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_update_transfer_audit_records_field_changes():
    async with async_session() as db:
        app_settings = await get_app_settings(db)
        user = (await db.execute(select(User).where(User.email == "admin@test.com"))).scalar_one()
        transfer = Transfer(
            public_token=f"audit-update-{uuid.uuid4().hex[:8]}",
            created_by=user.id,
            title="Before",
            message="Old message",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=5,
            notify_on_download=False,
        )
        db.add(transfer)
        await db.commit()
        await db.refresh(transfer)

        new_expiry = datetime.now(timezone.utc) + timedelta(days=14)
        await update_transfer(
            db,
            transfer=transfer,
            user=user,
            title="After",
            message="New message",
            password=None,
            remove_password=True,
            expires_at=new_expiry,
            max_downloads=10,
            notify_on_download=True,
            ip_address="127.0.0.1",
            app_settings=app_settings,
        )

        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "transfer.updated",
                    AuditLog.resource_id == str(transfer.id),
                )
            )
        ).scalar_one()
        meta = json.loads(row.metadata_json)
        fields = {c["field"] for c in meta["changes"]}
        assert "title" in fields
        assert "message" in fields
        assert "max_downloads" in fields
        assert "notify_on_download" in fields


@pytest.mark.asyncio
async def test_create_transfer_via_post_audit_has_files(client: AsyncClient):
    from app.services.staging import add_staged_file
    from starlette.datastructures import UploadFile as StarletteUploadFile
    from io import BytesIO

    async with async_session() as db:
        user = (await db.execute(select(User))).scalar_one()
        app_settings = await get_app_settings(db)
        scope = f"transfer_{user.id}"

    login_page = await client.get("/auth/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', login_page.text).group(1)
    await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=True,
    )

    upload_file = StarletteUploadFile(filename="doc.pdf", file=BytesIO(b"%PDF-1.4"))
    await add_staged_file(scope, upload_file, app_settings)

    new_page = await client.get("/transfers/new")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', new_page.text).group(1)
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
    response = await client.post(
        "/transfers/new",
        data={
            "title": "Audit files test",
            "expires_at": expiry,
            "max_downloads": "5",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    # Background finalize runs inline in tests usually - wait briefly or check audit after finalize
    # For preparing transfers, audit is logged in finalize_transfer_files background task
    import asyncio

    await asyncio.sleep(0.5)

    async with async_session() as db:
        transfer = (
            await db.execute(select(Transfer).where(Transfer.title == "Audit files test"))
        ).scalar_one_or_none()
        assert transfer is not None
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "transfer.created",
                    AuditLog.resource_id == str(transfer.id),
                )
            )
        ).scalar_one_or_none()
        assert row is not None
        meta = json.loads(row.metadata_json)
        assert meta.get("file_count") == 1
        assert meta["files"][0]["name"] == "doc.pdf"
        assert meta["share_link"].endswith(f"/d/{transfer.public_token}")


@pytest.mark.asyncio
async def test_file_request_create_and_link_regeneration_audit_full_links():
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.email == "admin@test.com"))).scalar_one()
        app_settings = await get_app_settings(db)
        req = await create_file_request(
            db,
            user=user,
            title="Audit request links",
            instructions=None,
            password=None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_uploads=5,
            max_total_bytes=1024 * 1024,
            recipient_emails=[],
            app_settings=app_settings,
            ip_address="127.0.0.1",
        )
        old_token = req.public_token

        await regenerate_file_request_link(
            db,
            req=req,
            user=user,
            ip_address="127.0.0.1",
        )

        rows = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.resource_id == str(req.id))
            )
        ).scalars().all()
        metadata_by_action = {
            row.action: json.loads(row.metadata_json)
            for row in rows
        }
        created_meta = metadata_by_action["file_request.created"]
        regenerated_meta = metadata_by_action["file_request.link_regenerated"]
        assert created_meta["share_link"].endswith(f"/r/{old_token}")
        assert regenerated_meta["old_share_link"].endswith(f"/r/{old_token}")
        assert regenerated_meta["new_share_link"].endswith(f"/r/{req.public_token}")


@pytest.mark.asyncio
async def test_transfer_link_regeneration_audit_has_old_and_new_links():
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.email == "admin@test.com"))).scalar_one()
        transfer = Transfer(
            public_token=f"audit-regenerate-{uuid.uuid4().hex}",
            created_by=user.id,
            title="Regenerate link",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=5,
        )
        db.add(transfer)
        await db.commit()
        await db.refresh(transfer)
        old_token = transfer.public_token

        await regenerate_transfer_link(
            db,
            transfer=transfer,
            user=user,
            ip_address="127.0.0.1",
        )

        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "transfer.link_regenerated",
                    AuditLog.resource_id == str(transfer.id),
                )
            )
        ).scalar_one()
        meta = json.loads(row.metadata_json)
        assert meta["old_share_link"].endswith(f"/d/{old_token}")
        assert meta["new_share_link"].endswith(f"/d/{transfer.public_token}")
