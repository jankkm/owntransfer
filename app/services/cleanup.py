from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import FileRequest, RequestUpload, Transfer, User
from app.services.audit import log_audit
from app.services.datetime_display import format_datetime_with_tz, utc_now
from app.services.email import send_expired_unused, send_purge_reminder
from app.services.settings import get_app_settings
from app.services.share_lifecycle import purge_cutoff, purge_notify_at
from app.services.storage import get_storage


def _utcnow() -> datetime:
    return utc_now()


def _utcnow_naive() -> datetime:
    return utc_now().replace(tzinfo=None)


async def mark_expired(db: AsyncSession) -> int:
    now = _utcnow_naive()
    transfers = await db.execute(
        select(Transfer).where(Transfer.is_expired.is_(False), Transfer.expires_at < now)
    )
    count = 0
    for transfer in transfers.scalars():
        transfer.is_expired = True
        count += 1

    requests = await db.execute(
        select(FileRequest).where(FileRequest.is_expired.is_(False), FileRequest.expires_at < now)
    )
    for req in requests.scalars():
        req.is_expired = True
        count += 1

    await db.commit()
    return count


async def notify_expired_unused(db: AsyncSession) -> int:
    app_settings = await get_app_settings(db)
    base_url = settings.base_url.rstrip("/")
    sent = 0

    transfer_rows = await db.execute(
        select(Transfer).where(
            Transfer.is_expired.is_(True),
            Transfer.expired_unused_notified.is_(False),
            Transfer.download_count == 0,
        )
    )
    for transfer in transfer_rows.scalars():
        creator = await db.get(User, transfer.created_by)
        if not creator or not creator.is_active:
            transfer.expired_unused_notified = True
            continue
        try:
            if await send_expired_unused(
                app_settings,
                to=creator.email,
                title=transfer.title,
                resource_label="transfer",
                expires_at=format_datetime_with_tz(transfer.expires_at),
                edit_link=f"{base_url}/transfers/{transfer.id}/edit",
            ):
                sent += 1
        except Exception:
            pass
        transfer.expired_unused_notified = True

    request_rows = await db.execute(
        select(FileRequest).where(
            FileRequest.is_expired.is_(True),
            FileRequest.expired_unused_notified.is_(False),
            FileRequest.upload_count == 0,
        )
    )
    for req in request_rows.scalars():
        creator = await db.get(User, req.created_by)
        if not creator or not creator.is_active:
            req.expired_unused_notified = True
            continue
        try:
            if await send_expired_unused(
                app_settings,
                to=creator.email,
                title=req.title,
                resource_label="file request",
                expires_at=format_datetime_with_tz(req.expires_at),
                edit_link=f"{base_url}/requests/{req.id}/edit",
            ):
                sent += 1
        except Exception:
            pass
        req.expired_unused_notified = True

    await db.commit()
    return sent


async def send_purge_reminders(db: AsyncSession) -> int:
    app_settings = await get_app_settings(db)
    if app_settings.purge_notify_days <= 0 or app_settings.purge_grace_days <= 0:
        return 0

    now = _utcnow()
    grace_days = app_settings.purge_grace_days
    notify_days = app_settings.purge_notify_days
    base_url = settings.base_url.rstrip("/")
    sent = 0

    transfer_rows = await db.execute(
        select(Transfer).where(Transfer.is_expired.is_(True), Transfer.purge_warned.is_(False))
    )
    for transfer in transfer_rows.scalars():
        purge_at = purge_cutoff(transfer.expires_at, grace_days)
        notify_at = purge_notify_at(transfer.expires_at, grace_days, notify_days)
        if now < notify_at or now >= purge_at:
            continue
        creator = await db.get(User, transfer.created_by)
        if not creator or not creator.is_active:
            transfer.purge_warned = True
            continue
        days_until = max(1, (purge_at - now).days)
        try:
            if await send_purge_reminder(
                app_settings,
                to=creator.email,
                title=transfer.title,
                resource_label="transfer",
                expires_at=format_datetime_with_tz(transfer.expires_at),
                edit_link=f"{base_url}/transfers/{transfer.id}/edit",
                purge_at=format_datetime_with_tz(purge_at),
                days_until_purge=days_until,
            ):
                sent += 1
        except Exception:
            pass
        transfer.purge_warned = True

    request_rows = await db.execute(
        select(FileRequest).where(FileRequest.is_expired.is_(True), FileRequest.purge_warned.is_(False))
    )
    for req in request_rows.scalars():
        purge_at = purge_cutoff(req.expires_at, grace_days)
        notify_at = purge_notify_at(req.expires_at, grace_days, notify_days)
        if now < notify_at or now >= purge_at:
            continue
        creator = await db.get(User, req.created_by)
        if not creator or not creator.is_active:
            req.purge_warned = True
            continue
        days_until = max(1, (purge_at - now).days)
        try:
            if await send_purge_reminder(
                app_settings,
                to=creator.email,
                title=req.title,
                resource_label="file request",
                expires_at=format_datetime_with_tz(req.expires_at),
                edit_link=f"{base_url}/requests/{req.id}/edit",
                purge_at=format_datetime_with_tz(purge_at),
                days_until_purge=days_until,
            ):
                sent += 1
        except Exception:
            pass
        req.purge_warned = True

    await db.commit()
    return sent


async def purge_expired(db: AsyncSession) -> int:
    app_settings = await get_app_settings(db)
    if app_settings.purge_grace_days <= 0:
        return 0

    grace = timedelta(days=app_settings.purge_grace_days)
    cutoff = _utcnow_naive() - grace
    storage = get_storage()
    purged = 0

    result = await db.execute(
        select(Transfer)
        .options(selectinload(Transfer.files))
        .where(Transfer.is_expired.is_(True), Transfer.expires_at < cutoff)
    )
    for transfer in result.scalars():
        await storage.delete_directory(f"transfers/{transfer.id}")
        await db.delete(transfer)
        purged += 1
        await log_audit(
            db,
            action="transfer.purged",
            resource_type="transfer",
            resource_id=str(transfer.id),
        )

    result = await db.execute(
        select(FileRequest)
        .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
        .where(FileRequest.is_expired.is_(True), FileRequest.expires_at < cutoff)
    )
    for req in result.scalars():
        await storage.delete_directory(f"requests/{req.id}")
        await db.delete(req)
        purged += 1
        await log_audit(
            db,
            action="file_request.purged",
            resource_type="file_request",
            resource_id=str(req.id),
        )

    await db.commit()
    return purged


async def purge_orphan_staging() -> int:
    storage = get_storage()
    staging_root = storage.absolute_path("staging")
    if not staging_root.exists():
        return 0

    cutoff = _utcnow().timestamp() - (24 * 3600)
    removed = 0
    for path in staging_root.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    for path in sorted(staging_root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return removed


async def run_cleanup(db: AsyncSession) -> dict[str, int]:
    expired = await mark_expired(db)
    unused = await notify_expired_unused(db)
    purge_reminders = await send_purge_reminders(db)
    purged = await purge_expired(db)
    staging = await purge_orphan_staging()
    return {
        "expired": expired,
        "unused": unused,
        "purge_reminders": purge_reminders,
        "purged": purged,
        "staging": staging,
    }
