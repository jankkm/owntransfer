from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import FileRequest, Transfer
from app.services.datetime_display import utc_now


async def get_user_shares_summary(db: AsyncSession, user_id: UUID) -> dict[str, int]:
    now = utc_now().replace(tzinfo=None)
    transfer_total = await db.scalar(
        select(func.count()).select_from(Transfer).where(Transfer.created_by == user_id)
    ) or 0
    request_total = await db.scalar(
        select(func.count()).select_from(FileRequest).where(FileRequest.created_by == user_id)
    ) or 0
    active_transfers = await db.scalar(
        select(func.count())
        .select_from(Transfer)
        .where(
            Transfer.created_by == user_id,
            Transfer.is_disabled.is_(False),
            Transfer.is_expired.is_(False),
            Transfer.expires_at >= now,
            or_(Transfer.max_downloads == 0, Transfer.download_count < Transfer.max_downloads),
        )
    ) or 0
    active_requests = await db.scalar(
        select(func.count())
        .select_from(FileRequest)
        .where(
            FileRequest.created_by == user_id,
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


async def list_recent_user_transfers(
    db: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 5,
) -> list[Transfer]:
    result = await db.execute(
        select(Transfer)
        .options(selectinload(Transfer.files))
        .where(Transfer.created_by == user_id)
        .order_by(Transfer.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_recent_user_requests(
    db: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 5,
) -> list[FileRequest]:
    result = await db.execute(
        select(FileRequest)
        .options(selectinload(FileRequest.uploads))
        .where(FileRequest.created_by == user_id)
        .order_by(FileRequest.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
