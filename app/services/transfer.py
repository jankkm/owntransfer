from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.download_grants import has_transfer_download_grant
from app.auth.passwords import hash_password, verify_password
from app.config import settings
from app.i18n import _
from app.models import AppSettings, Transfer, TransferDownloadLog, TransferFile, User
from app.services.audit import log_audit
from app.services.email import send_download_notify, send_share_email
from app.services.datetime_display import ensure_expiry_within_limit, ensure_utc, format_datetime_with_tz, utc_now
from app.services.download_limits import transfer_download_limit_reached
from app.services.settings import generate_public_token, is_extension_blocked, parse_blocklist
from app.services.share_lifecycle import is_past_expiry, reset_expiry_notifications
from app.services.staging import StagedFile, _save_upload
from app.services.storage import get_storage


def _utcnow() -> datetime:
    return utc_now()


async def create_transfer(
    db: AsyncSession,
    *,
    user: User,
    title: str,
    message: str | None,
    password: str | None,
    expires_at: datetime,
    max_downloads: int,
    notify_on_download: bool,
    recipient_emails: list[str],
    app_settings: AppSettings,
    ip_address: str | None,
    files: list[UploadFile] | None = None,
    staged_files: list[StagedFile] | None = None,
) -> Transfer:
    ensure_expiry_within_limit(expires_at, app_settings.max_share_expiry_days)
    if max_downloads < 0:
        raise HTTPException(status_code=400, detail=_("Max downloads cannot be negative"))
    blocklist = parse_blocklist(app_settings.file_type_blocklist)
    transfer = Transfer(
        public_token=generate_public_token(),
        created_by=user.id,
        title=title,
        message=message,
        password_hash=hash_password(password) if password else None,
        expires_at=expires_at,
        max_downloads=max_downloads,
        notify_on_download=notify_on_download,
        recipient_emails=",".join(recipient_emails) if recipient_emails else None,
    )
    db.add(transfer)
    await db.flush()

    storage = get_storage()
    total_size = 0
    saved_count = 0

    if staged_files:
        for staged in staged_files:
            if is_extension_blocked(staged.original_name, blocklist):
                raise HTTPException(
                    status_code=400, detail=_("File type not allowed: %(filename)s") % {"filename": staged.original_name}
                )
            total_size += staged.size_bytes
            if total_size > app_settings.max_file_size_bytes:
                raise HTTPException(status_code=400, detail=_("Total upload exceeds maximum file size"))
            rel_path = f"transfers/{transfer.id}/{uuid4()}/{_safe_filename(staged.original_name)}"
            content = storage.absolute_path(staged.storage_path).read_bytes()
            await storage.save_file(rel_path, content)
            db.add(
                TransferFile(
                    transfer_id=transfer.id,
                    original_name=staged.original_name,
                    storage_path=rel_path,
                    size_bytes=staged.size_bytes,
                    content_type=staged.content_type,
                )
            )
            saved_count += 1
    elif files:
        for upload in files:
            if not upload.filename:
                continue
            if is_extension_blocked(upload.filename, blocklist):
                raise HTTPException(status_code=400, detail=_("File type not allowed: %(filename)s") % {"filename": upload.filename})
            content = await upload.read()
            total_size += len(content)
            if total_size > app_settings.max_file_size_bytes:
                raise HTTPException(status_code=400, detail=_("Total upload exceeds maximum file size"))
            rel_path = f"transfers/{transfer.id}/{uuid4()}/{_safe_filename(upload.filename)}"
            await storage.save_file(rel_path, content)
            db.add(
                TransferFile(
                    transfer_id=transfer.id,
                    original_name=upload.filename,
                    storage_path=rel_path,
                    size_bytes=len(content),
                    content_type=upload.content_type,
                )
            )
            saved_count += 1

    if saved_count == 0:
        raise HTTPException(status_code=400, detail=_("Add at least one file"))

    await db.commit()
    await db.refresh(transfer)

    if recipient_emails and app_settings.allow_user_share_emails:
        link = f"{settings.base_url.rstrip('/')}/d/{transfer.public_token}"
        await send_share_email(
            app_settings,
            recipients=recipient_emails,
            title=title,
            message=message,
            link=link,
            password=password,
            expires_at=format_datetime_with_tz(transfer.expires_at),
        )

    await log_audit(
        db,
        action="transfer.created",
        resource_type="transfer",
        resource_id=str(transfer.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={"title": title},
    )
    return transfer


async def lookup_transfer_by_token(db: AsyncSession, token: str) -> Transfer | None:
    result = await db.execute(
        select(Transfer)
        .options(selectinload(Transfer.files))
        .where(Transfer.public_token == token)
    )
    return result.scalar_one_or_none()


async def get_transfer_by_token(db: AsyncSession, token: str) -> Transfer:
    transfer = await lookup_transfer_by_token(db, token)
    if not transfer:
        raise HTTPException(status_code=404, detail=_("Transfer not found"))
    return transfer


ACCESS_DISABLED = "disabled"
ACCESS_EXPIRED = "expired"
ACCESS_DOWNLOAD_LIMIT = "download_limit"


def transfer_access_issue(
    transfer: Transfer,
    *,
    session: dict | None = None,
    public_token: str | None = None,
) -> str | None:
    if transfer.is_disabled:
        return ACCESS_DISABLED
    if is_past_expiry(is_expired=transfer.is_expired, expires_at=transfer.expires_at):
        return ACCESS_EXPIRED
    if transfer_download_limit_reached(transfer):
        has_grant = (
            session is not None
            and public_token is not None
            and has_transfer_download_grant(session, public_token)
        )
        if not has_grant:
            return ACCESS_DOWNLOAD_LIMIT
    return None


def ensure_transfer_accessible(
    transfer: Transfer,
    *,
    session: dict | None = None,
    public_token: str | None = None,
) -> None:
    issue = transfer_access_issue(transfer, session=session, public_token=public_token)
    if issue == ACCESS_DISABLED:
        raise HTTPException(status_code=403, detail=_("This link has been disabled"))
    if issue == ACCESS_EXPIRED:
        raise HTTPException(status_code=410, detail=_("This link has expired"))
    if issue == ACCESS_DOWNLOAD_LIMIT:
        raise HTTPException(status_code=410, detail=_("Download limit reached"))


def verify_transfer_password(transfer: Transfer, password: str | None) -> bool:
    if transfer.password_hash:
        return verify_password(password or "", transfer.password_hash)
    return True


async def log_transfer_download(
    db: AsyncSession,
    *,
    transfer_id: UUID,
    ip_address: str | None,
    download_type: str,
    file_name: str | None = None,
) -> None:
    db.add(
        TransferDownloadLog(
            transfer_id=transfer_id,
            ip_address=ip_address,
            download_type=download_type,
            file_name=file_name,
        )
    )
    await db.commit()


async def record_download(
    db: AsyncSession,
    transfer: Transfer,
    app_settings: AppSettings,
    creator: User | None,
) -> None:
    transfer.download_count += 1
    await db.commit()
    await log_audit(
        db,
        action="transfer.downloaded",
        resource_type="transfer",
        resource_id=str(transfer.id),
    )
    if transfer.notify_on_download and creator and creator.email:
        max_label = "∞" if transfer.max_downloads <= 0 else str(transfer.max_downloads)
        recipient_locale = creator.locale
        asyncio.create_task(
            send_download_notify(
                app_settings,
                to=creator.email,
                title=transfer.title,
                download_count=transfer.download_count,
                max_downloads=max_label,
                locale=recipient_locale,
            )
        )


def transfer_zip_entries(transfer: Transfer) -> list[tuple[Path, str]]:
    storage = get_storage()
    used: dict[str, int] = {}
    entries: list[tuple[Path, str]] = []
    for tf in transfer.files:
        path = storage.absolute_path(tf.storage_path)
        arcname = _unique_zip_name(_safe_filename(tf.original_name), used)
        entries.append((path, arcname))
    return entries


async def get_transfer_file(transfer: Transfer, file_id: UUID) -> TransferFile:
    for tf in transfer.files:
        if tf.id == file_id:
            return tf
    raise HTTPException(status_code=404, detail=_("File not found"))


def _safe_filename(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    return re.sub(r"[^\w.\- ()]", "_", base) or "file"


def _unique_zip_name(name: str, used: dict[str, int]) -> str:
    used[name] = used.get(name, 0) + 1
    if used[name] == 1:
        return name
    path = Path(name)
    return f"{path.stem}_{used[name]}{path.suffix}"


def _transfer_total_bytes(transfer: Transfer) -> int:
    return sum(f.size_bytes for f in transfer.files)


async def add_transfer_file(
    db: AsyncSession,
    *,
    transfer: Transfer,
    upload: UploadFile,
    app_settings: AppSettings,
    user: User,
    ip_address: str | None,
) -> TransferFile:
    if not upload.filename:
        raise HTTPException(status_code=400, detail=_("Missing filename"))

    blocklist = parse_blocklist(app_settings.file_type_blocklist)
    if is_extension_blocked(upload.filename, blocklist):
        raise HTTPException(status_code=400, detail=_("File type not allowed: %(filename)s") % {"filename": upload.filename})

    file_id = uuid4()
    safe_name = _safe_filename(upload.filename)
    rel_path = f"transfers/{transfer.id}/{file_id}/{safe_name}"
    storage = get_storage()
    size_bytes = await _save_upload(rel_path, upload, app_settings.max_file_size_bytes)

    total_size = _transfer_total_bytes(transfer) + size_bytes
    if total_size > app_settings.max_file_size_bytes:
        await storage.delete_file(rel_path)
        raise HTTPException(status_code=400, detail=_("Total upload exceeds maximum file size"))

    transfer_file = TransferFile(
        id=file_id,
        transfer_id=transfer.id,
        original_name=upload.filename,
        storage_path=rel_path,
        size_bytes=size_bytes,
        content_type=upload.content_type,
    )
    db.add(transfer_file)
    await db.commit()
    await db.refresh(transfer_file)
    transfer.files.append(transfer_file)

    await log_audit(
        db,
        action="transfer.file_added",
        resource_type="transfer",
        resource_id=str(transfer.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={"file_name": upload.filename},
    )
    return transfer_file


async def delete_transfer_file(
    db: AsyncSession,
    *,
    transfer: Transfer,
    file_id: UUID,
    user: User,
    ip_address: str | None,
) -> None:
    if len(transfer.files) <= 1:
        raise HTTPException(status_code=400, detail=_("Transfer must have at least one file"))

    transfer_file = await get_transfer_file(transfer, file_id)
    file_name = transfer_file.original_name
    storage = get_storage()
    await storage.delete_file(transfer_file.storage_path)
    await db.delete(transfer_file)
    await db.commit()
    transfer.files = [f for f in transfer.files if f.id != file_id]

    await log_audit(
        db,
        action="transfer.file_removed",
        resource_type="transfer",
        resource_id=str(transfer.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={"file_name": file_name},
    )


def iter_transfer_file(transfer_file: TransferFile):
    """Sync iterator for StreamingResponse."""
    storage = get_storage()
    path = storage.absolute_path(transfer_file.storage_path)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            yield chunk


async def list_user_transfers(db: AsyncSession, user_id: UUID) -> list[Transfer]:
    result = await db.execute(
        select(Transfer)
        .options(selectinload(Transfer.files))
        .where(Transfer.created_by == user_id)
        .order_by(Transfer.created_at.desc())
    )
    return list(result.scalars().all())


async def get_user_transfer(db: AsyncSession, transfer_id: UUID, user_id: UUID) -> Transfer:
    result = await db.execute(
        select(Transfer)
        .options(selectinload(Transfer.files), selectinload(Transfer.download_logs))
        .where(Transfer.id == transfer_id, Transfer.created_by == user_id)
    )
    transfer = result.scalar_one_or_none()
    if not transfer:
        raise HTTPException(status_code=404, detail=_("Transfer not found"))
    return transfer


async def get_transfer_for_admin(db: AsyncSession, transfer_id: UUID) -> Transfer:
    result = await db.execute(
        select(Transfer)
        .options(
            selectinload(Transfer.files),
            selectinload(Transfer.download_logs),
            selectinload(Transfer.creator),
        )
        .where(Transfer.id == transfer_id)
    )
    transfer = result.scalar_one_or_none()
    if not transfer:
        raise HTTPException(status_code=404, detail=_("Transfer not found"))
    return transfer


async def update_transfer(
    db: AsyncSession,
    *,
    transfer: Transfer,
    user: User,
    title: str,
    message: str | None,
    password: str | None,
    remove_password: bool,
    expires_at: datetime,
    max_downloads: int,
    notify_on_download: bool,
    ip_address: str | None,
    enabled: bool | None = None,
    app_settings: AppSettings | None = None,
) -> Transfer:
    now = _utcnow()
    if app_settings:
        ensure_expiry_within_limit(expires_at, app_settings.max_share_expiry_days)
    if max_downloads < 0:
        raise HTTPException(status_code=400, detail=_("Max downloads cannot be negative"))
    if max_downloads > 0 and max_downloads < transfer.download_count:
        raise HTTPException(
            status_code=400,
            detail=_("Max downloads cannot be less than current count (%(count)s)")
            % {"count": transfer.download_count},
        )

    transfer.title = title
    transfer.message = message
    transfer.expires_at = expires_at
    transfer.max_downloads = max_downloads
    transfer.notify_on_download = notify_on_download
    if ensure_utc(expires_at) >= ensure_utc(now):
        transfer.is_expired = False
    reset_expiry_notifications(transfer, expires_at, now)

    if not remove_password and not password and not transfer.password_hash:
        raise HTTPException(status_code=400, detail=_("Enter a password to enable protection"))

    if remove_password:
        transfer.password_hash = None
    elif password:
        transfer.password_hash = hash_password(password)

    if enabled is not None:
        transfer.is_disabled = not enabled

    await db.commit()
    await db.refresh(transfer)

    await log_audit(
        db,
        action="transfer.updated",
        resource_type="transfer",
        resource_id=str(transfer.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={"title": title},
    )
    return transfer


async def delete_transfer(
    db: AsyncSession,
    *,
    transfer: Transfer,
    user: User,
    ip_address: str | None,
) -> None:
    storage = get_storage()
    await storage.delete_directory(f"transfers/{transfer.id}")
    transfer_id = str(transfer.id)
    await db.delete(transfer)
    await db.commit()
    await log_audit(
        db,
        action="transfer.deleted",
        resource_type="transfer",
        resource_id=transfer_id,
        actor_id=user.id,
        ip_address=ip_address,
    )


async def regenerate_transfer_link(
    db: AsyncSession,
    *,
    transfer: Transfer,
    user: User,
    ip_address: str | None,
) -> Transfer:
    old_token = transfer.public_token
    transfer.public_token = generate_public_token()
    await db.commit()
    await db.refresh(transfer)

    await log_audit(
        db,
        action="transfer.link_regenerated",
        resource_type="transfer",
        resource_id=str(transfer.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={"old_token_prefix": old_token[:8]},
    )
    return transfer
