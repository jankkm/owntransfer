from __future__ import annotations

import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import _
from app.models import Transfer


def downloads_unlimited(max_downloads: int) -> bool:
    return max_downloads <= 0


def uploads_unlimited(max_uploads: int) -> bool:
    return max_uploads <= 0


def _format_limit_short(count: int, maximum: int) -> str:
    if maximum <= 0:
        return _("%(count)s / ∞") % {"count": count}
    return _("%(count)s / %(max)s") % {"count": count, "max": maximum}


def transfer_download_limit_reached(transfer: Transfer) -> bool:
    if downloads_unlimited(transfer.max_downloads):
        return False
    return transfer.download_count >= transfer.max_downloads


async def try_reserve_download_slot(
    db: AsyncSession,
    transfer_id: uuid.UUID,
    *,
    max_downloads: int,
) -> bool:
    if downloads_unlimited(max_downloads):
        stmt = (
            update(Transfer)
            .where(Transfer.id == transfer_id)
            .values(download_count=Transfer.download_count + 1)
            .returning(Transfer.id)
        )
    else:
        stmt = (
            update(Transfer)
            .where(Transfer.id == transfer_id)
            .where(Transfer.download_count < max_downloads)
            .values(download_count=Transfer.download_count + 1)
            .returning(Transfer.id)
        )

    result = await db.execute(stmt)
    await db.commit()
    return result.scalar_one_or_none() is not None


def format_download_limit(download_count: int, max_downloads: int) -> str:
    return format_download_limit_short(download_count, max_downloads)


def format_download_limit_short(download_count: int, max_downloads: int) -> str:
    return _format_limit_short(download_count, max_downloads)


def format_upload_limit_short(upload_count: int, max_uploads: int) -> str:
    return _format_limit_short(upload_count, max_uploads)
