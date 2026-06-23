from __future__ import annotations

import json
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSettings


async def get_app_settings(db: AsyncSession) -> AppSettings:
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    row = result.scalar_one_or_none()
    if row:
        return row
    row = AppSettings(
        id=1,
        app_name=settings.app_name,
        color_scheme=settings.color_scheme,
        max_file_size_bytes=settings.max_file_size_bytes,
        default_expiry_days=settings.default_expiry_days,
        max_share_expiry_days=settings.max_share_expiry_days,
        max_downloads_default=settings.max_downloads_default,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_user=settings.smtp_user,
        smtp_password=settings.smtp_password,
        smtp_from=settings.smtp_from,
        smtp_use_tls=settings.smtp_use_tls,
        allow_local_login=settings.allow_local_login,
        allow_user_share_emails=settings.allow_user_share_emails,
        purge_grace_days=settings.purge_grace_days,
        purge_notify_days=settings.purge_notify_days,
        setup_completed=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def generate_public_token() -> str:
    return secrets.token_urlsafe(32)


def parse_blocklist(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).lower() for x in data]
    except json.JSONDecodeError:
        pass
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def is_extension_blocked(filename: str, blocklist: list[str]) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in blocklist


async def is_setup_complete(db: AsyncSession) -> bool:
    app_settings = await get_app_settings(db)
    return app_settings.setup_completed
