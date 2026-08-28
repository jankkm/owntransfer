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


async def _login(client: AsyncClient, email: str, password: str = "password123") -> str:
    login_page = await client.get("/auth/login")
    token = re.search(r'name="csrf-token" content="([^"]+)"', login_page.text).group(1)
    await client.post(
        "/auth/login/local",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=True,
    )
    return token


async def _csrf_from_page(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    return re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)


def _expiry_date(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


async def _create_users() -> tuple[User, User]:
    async with async_session() as db:
        owner = User(
            email="owner@test.com",
            password_hash=hash_password("password123"),
            is_admin=False,
        )
        new_owner = User(
            email="newowner@test.com",
            password_hash=hash_password("password123"),
            is_admin=False,
        )
        db.add(owner)
        db.add(new_owner)
        await db.commit()
        await db.refresh(owner)
        await db.refresh(new_owner)
        return owner, new_owner


async def _create_transfer(owner_id: uuid.UUID) -> Transfer:
    async with async_session() as db:
        transfer = Transfer(
            public_token="owner-change-transfer",
            created_by=owner_id,
            title="Shared files",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_downloads=5,
        )
        db.add(transfer)
        await db.commit()
        await db.refresh(transfer)
        return transfer


async def _create_file_request(owner_id: uuid.UUID) -> FileRequest:
    async with async_session() as db:
        req = FileRequest(
            public_token="owner-change-request",
            created_by=owner_id,
            title="Upload please",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            max_uploads=3,
            max_total_bytes=10 * 1024 * 1024,
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)
        return req


def _transfer_edit_data(*, created_by: uuid.UUID, csrf: str) -> dict[str, str]:
    return {
        "title": "Shared files",
        "message": "",
        "expires_at": _expiry_date(),
        "max_downloads": "5",
        "has_enabled_field": "1",
        "enabled": "1",
        "created_by": str(created_by),
        "csrf_token": csrf,
    }


def _request_edit_data(*, created_by: uuid.UUID, csrf: str) -> dict[str, str]:
    return {
        "title": "Upload please",
        "instructions": "",
        "expires_at": _expiry_date(),
        "max_uploads": "3",
        "max_total_mb": "10",
        "has_enabled_field": "1",
        "enabled": "1",
        "created_by": str(created_by),
        "csrf_token": csrf,
    }


@pytest.mark.asyncio
async def test_admin_can_change_transfer_owner(client: AsyncClient):
    owner, new_owner = await _create_users()
    transfer = await _create_transfer(owner.id)

    await _login(client, "admin@test.com")
    csrf = await _csrf_from_page(client, f"/admin/shares/transfers/{transfer.id}/edit")

    response = await client.post(
        f"/admin/shares/transfers/{transfer.id}/edit",
        data=_transfer_edit_data(created_by=new_owner.id, csrf=csrf),
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with async_session() as db:
        updated = await db.get(Transfer, transfer.id)
        assert updated is not None
        assert updated.created_by == new_owner.id

        audit = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action == "transfer.owner_changed", AuditLog.resource_id == str(transfer.id))
                .order_by(AuditLog.created_at.desc())
            )
        ).scalar_one()
        metadata = json.loads(audit.metadata_json or "{}")
        assert metadata["previous_owner_id"] == str(owner.id)
        assert metadata["new_owner_id"] == str(new_owner.id)
        assert metadata["changes"][0]["old"] == owner.email
        assert metadata["changes"][0]["new"] == new_owner.email


@pytest.mark.asyncio
async def test_admin_can_change_file_request_owner(client: AsyncClient):
    owner, new_owner = await _create_users()
    req = await _create_file_request(owner.id)

    await _login(client, "admin@test.com")
    csrf = await _csrf_from_page(client, f"/admin/shares/requests/{req.id}/edit")

    response = await client.post(
        f"/admin/shares/requests/{req.id}/edit",
        data=_request_edit_data(created_by=new_owner.id, csrf=csrf),
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with async_session() as db:
        updated = await db.get(FileRequest, req.id)
        assert updated is not None
        assert updated.created_by == new_owner.id

        audit = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.action == "file_request.owner_changed", AuditLog.resource_id == str(req.id))
                .order_by(AuditLog.created_at.desc())
            )
        ).scalar_one()
        metadata = json.loads(audit.metadata_json or "{}")
        assert metadata["previous_owner_id"] == str(owner.id)
        assert metadata["new_owner_id"] == str(new_owner.id)
        assert metadata["changes"][0]["old"] == owner.email
        assert metadata["changes"][0]["new"] == new_owner.email


@pytest.mark.asyncio
async def test_admin_cannot_assign_inactive_user_as_transfer_owner(client: AsyncClient):
    owner, new_owner = await _create_users()
    transfer = await _create_transfer(owner.id)

    async with async_session() as db:
        inactive = await db.get(User, new_owner.id)
        assert inactive is not None
        inactive.is_active = False
        await db.commit()

    await _login(client, "admin@test.com")
    csrf = await _csrf_from_page(client, f"/admin/shares/transfers/{transfer.id}/edit")

    response = await client.post(
        f"/admin/shares/transfers/{transfer.id}/edit",
        data=_transfer_edit_data(created_by=new_owner.id, csrf=csrf),
        follow_redirects=False,
    )
    assert response.status_code == 400

    async with async_session() as db:
        unchanged = await db.get(Transfer, transfer.id)
        assert unchanged is not None
        assert unchanged.created_by == owner.id


@pytest.mark.asyncio
async def test_admin_rejects_invalid_transfer_owner_id(client: AsyncClient):
    owner, _new_owner = await _create_users()
    transfer = await _create_transfer(owner.id)

    await _login(client, "admin@test.com")
    csrf = await _csrf_from_page(client, f"/admin/shares/transfers/{transfer.id}/edit")

    response = await client.post(
        f"/admin/shares/transfers/{transfer.id}/edit",
        data=_transfer_edit_data(created_by=uuid.uuid4(), csrf=csrf),
        follow_redirects=False,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_regular_user_cannot_change_transfer_owner(client: AsyncClient):
    owner, new_owner = await _create_users()
    transfer = await _create_transfer(owner.id)

    await _login(client, "owner@test.com")
    csrf = await _csrf_from_page(client, f"/transfers/{transfer.id}/edit")

    response = await client.post(
        f"/transfers/{transfer.id}/edit",
        data=_transfer_edit_data(created_by=new_owner.id, csrf=csrf),
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with async_session() as db:
        unchanged = await db.get(Transfer, transfer.id)
        assert unchanged is not None
        assert unchanged.created_by == owner.id
