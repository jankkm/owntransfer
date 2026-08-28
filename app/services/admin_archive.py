from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ArchivedShare
from app.services.audit import delete_share_audit
from app.services.datetime_display import utc_now
from app.services.settings import get_app_settings
from app.services.share_timeline import build_timeline_from_snapshot


async def list_archived_shares(
    db: AsyncSession,
    *,
    creator_id: uuid.UUID | None = None,
) -> list[ArchivedShare]:
    query = select(ArchivedShare).order_by(ArchivedShare.archived_at.desc())
    if creator_id:
        query = query.where(ArchivedShare.creator_id == creator_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_archived_share(db: AsyncSession, archive_id: uuid.UUID) -> ArchivedShare | None:
    result = await db.execute(select(ArchivedShare).where(ArchivedShare.id == archive_id))
    return result.scalar_one_or_none()


def parse_snapshot(archived: ArchivedShare) -> dict:
    return json.loads(archived.snapshot_json)


def archived_timeline(archived: ArchivedShare) -> list:
    snapshot = parse_snapshot(archived)
    return build_timeline_from_snapshot(snapshot, archived.resource_type)


def snapshot_download_logs_display(snapshot: dict) -> list[SimpleNamespace]:
    rows = []
    for item in snapshot.get("download_logs") or []:
        rows.append(
            SimpleNamespace(
                created_at=datetime.fromisoformat(item["at"]),
                ip_address=item.get("ip_address"),
                download_type=item.get("download_type", "file"),
                file_name=item.get("file_name"),
            )
        )
    return rows


def snapshot_uploads_display(snapshot: dict) -> list[SimpleNamespace]:
    rows = []
    for item in snapshot.get("uploads") or []:
        files = item.get("files") or []
        rows.append(
            SimpleNamespace(
                created_at=datetime.fromisoformat(item["at"]),
                ip_address=item.get("ip_address"),
                uploader_name=item.get("uploader_name"),
                uploader_email=item.get("uploader_email"),
                files=[SimpleNamespace(**f) for f in files],
            )
        )
    return rows


async def delete_archived_share(db: AsyncSession, archived: ArchivedShare) -> None:
    await delete_share_audit(
        db,
        resource_type=archived.resource_type,
        resource_id=str(archived.original_id),
    )
    await db.delete(archived)
    await db.commit()


async def purge_archived_shares(db: AsyncSession) -> int:
    app_settings = await get_app_settings(db)
    if app_settings.archive_retention_days <= 0:
        return 0

    cutoff = utc_now() - timedelta(days=app_settings.archive_retention_days)
    result = await db.execute(
        select(ArchivedShare).where(ArchivedShare.archived_at < cutoff)
    )
    rows = list(result.scalars().all())
    for archived in rows:
        await delete_share_audit(
            db,
            resource_type=archived.resource_type,
            resource_id=str(archived.original_id),
        )
        await db.delete(archived)
    await db.commit()
    return len(rows)
