from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.services.datetime_display import utc_now
from app.services.share_lifecycle import is_past_expiry
from app.models import FileRequest, Transfer, User


def _utcnow() -> datetime:
    return utc_now()


def is_item_expired(*, is_expired: bool, expires_at: datetime) -> bool:
    return is_past_expiry(is_expired=is_expired, expires_at=expires_at)


async def get_user_resource_counts(db: AsyncSession) -> dict[UUID, dict[str, int]]:
    transfer_rows = await db.execute(
        select(Transfer.created_by, func.count()).group_by(Transfer.created_by)
    )
    request_rows = await db.execute(
        select(FileRequest.created_by, func.count()).group_by(FileRequest.created_by)
    )
    counts: dict[UUID, dict[str, int]] = {}
    for user_id, total in transfer_rows.all():
        counts.setdefault(user_id, {"transfers": 0, "requests": 0})["transfers"] = total
    for user_id, total in request_rows.all():
        counts.setdefault(user_id, {"transfers": 0, "requests": 0})["requests"] = total
    return counts


async def list_all_transfers(
    db: AsyncSession,
    *,
    creator_id: UUID | None = None,
) -> list[Transfer]:
    query = (
        select(Transfer)
        .options(selectinload(Transfer.files), selectinload(Transfer.creator))
        .order_by(Transfer.created_at.desc())
    )
    if creator_id:
        query = query.where(Transfer.created_by == creator_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def list_all_file_requests(
    db: AsyncSession,
    *,
    creator_id: UUID | None = None,
) -> list[FileRequest]:
    query = (
        select(FileRequest)
        .options(selectinload(FileRequest.uploads), selectinload(FileRequest.creator))
        .order_by(FileRequest.created_at.desc())
    )
    if creator_id:
        query = query.where(FileRequest.created_by == creator_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_shares_summary(db: AsyncSession) -> dict[str, int]:
    now = _utcnow().replace(tzinfo=None)
    transfer_total = await db.scalar(select(func.count()).select_from(Transfer)) or 0
    request_total = await db.scalar(select(func.count()).select_from(FileRequest)) or 0
    active_transfers = await db.scalar(
        select(func.count()).select_from(Transfer).where(
            Transfer.is_disabled.is_(False),
            Transfer.is_expired.is_(False),
            Transfer.expires_at >= now,
            or_(Transfer.max_downloads == 0, Transfer.download_count < Transfer.max_downloads),
        )
    ) or 0
    active_requests = await db.scalar(
        select(func.count()).select_from(FileRequest).where(
            FileRequest.is_disabled.is_(False),
            FileRequest.is_expired.is_(False),
            FileRequest.expires_at >= now,
            FileRequest.upload_count < FileRequest.max_uploads,
        )
    ) or 0
    return {
        "transfer_total": transfer_total,
        "request_total": request_total,
        "active_transfers": active_transfers,
        "active_requests": active_requests,
    }
