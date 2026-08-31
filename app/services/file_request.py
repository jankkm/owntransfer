from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.passwords import hash_password, verify_password
from app.config import settings
from app.database import async_session
from app.i18n import _
from app.models import AppSettings, FileRequest, RequestUpload, UploadFile, User
from app.services.audit import log_audit
from app.services.archive import archive_share_before_delete
from app.services.email import send_request_email, send_upload_notify
from app.services.datetime_display import ensure_expiry_within_limit, ensure_utc, format_datetime_with_tz, utc_now
from app.services.settings import generate_public_token, get_app_settings, is_extension_blocked, parse_blocklist
from app.services.share_audit_metadata import build_file_request_update_changes, build_owner_change_metadata
from app.services.share_lifecycle import is_past_expiry, reset_expiry_notifications
from app.services.staging import StagedFile, discard_staged_paths
from app.services.storage import get_storage

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return utc_now()


def _safe_filename(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    return re.sub(r"[^\w.\- ()]", "_", base) or "file"


def effective_request_max_total_bytes(req: FileRequest, _app_settings: AppSettings) -> int:
    return req.max_total_bytes


def ensure_max_total_is_positive(max_total_bytes: int) -> None:
    if max_total_bytes <= 0:
        raise HTTPException(
            status_code=400,
            detail=_("Max total size must be greater than zero"),
        )


async def create_file_request(
    db: AsyncSession,
    *,
    user: User,
    title: str,
    instructions: str | None,
    password: str | None,
    expires_at: datetime,
    max_uploads: int,
    max_total_bytes: int,
    recipient_emails: list[str],
    app_settings: AppSettings,
    ip_address: str | None,
) -> FileRequest:
    ensure_expiry_within_limit(expires_at, app_settings.max_share_expiry_days)
    ensure_max_total_is_positive(max_total_bytes)
    req = FileRequest(
        public_token=generate_public_token(),
        created_by=user.id,
        title=title,
        instructions=instructions,
        password_hash=hash_password(password) if password else None,
        expires_at=expires_at,
        max_uploads=max_uploads,
        max_total_bytes=max_total_bytes,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    if recipient_emails and app_settings.allow_user_share_emails:
        link = f"{settings.base_url.rstrip('/')}/r/{req.public_token}"
        await send_request_email(
            app_settings,
            recipients=recipient_emails,
            sender=user.email,
            title=title,
            instructions=instructions,
            link=link,
            password=password,
            expires_at=format_datetime_with_tz(req.expires_at),
        )

    await log_audit(
        db,
        action="file_request.created",
        resource_type="file_request",
        resource_id=str(req.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={
            "share_link": f"{settings.base_url.rstrip('/')}/r/{req.public_token}",
        },
    )
    return req


async def lookup_request_by_token(db: AsyncSession, token: str) -> FileRequest | None:
    result = await db.execute(
        select(FileRequest)
        .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
        .where(FileRequest.public_token == token)
    )
    return result.scalar_one_or_none()


async def get_request_by_token(db: AsyncSession, token: str) -> FileRequest:
    req = await lookup_request_by_token(db, token)
    if not req:
        raise HTTPException(status_code=404, detail=_("File request not found"))
    return req


ACCESS_DISABLED = "disabled"
ACCESS_EXPIRED = "expired"
ACCESS_UPLOAD_LIMIT = "upload_limit"


def request_access_issue(req: FileRequest) -> str | None:
    if req.is_disabled:
        return ACCESS_DISABLED
    if is_past_expiry(is_expired=req.is_expired, expires_at=req.expires_at):
        return ACCESS_EXPIRED
    if req.max_uploads != 0 and req.upload_count >= req.max_uploads:
        return ACCESS_UPLOAD_LIMIT
    return None


def ensure_request_accessible(req: FileRequest) -> None:
    issue = request_access_issue(req)
    if issue == ACCESS_DISABLED:
        raise HTTPException(status_code=403, detail=_("This link has been disabled"))
    if issue == ACCESS_EXPIRED:
        raise HTTPException(status_code=410, detail=_("This link has expired"))
    if issue == ACCESS_UPLOAD_LIMIT:
        raise HTTPException(status_code=410, detail=_("Upload limit reached"))


def verify_request_password(req: FileRequest, password: str | None) -> bool:
    if req.password_hash:
        return verify_password(password or "", req.password_hash)
    return True


def _validate_staged_request_files(
    staged_files: list[StagedFile],
    *,
    max_total_bytes: int,
    file_type_blocklist: str | None,
) -> None:
    if not staged_files:
        raise HTTPException(status_code=400, detail=_("Add at least one file to upload"))

    blocklist = parse_blocklist(file_type_blocklist)
    total_size = 0
    for staged in staged_files:
        if is_extension_blocked(staged.original_name, blocklist):
            raise HTTPException(
                status_code=400,
                detail=_("File type not allowed: %(filename)s") % {"filename": staged.original_name},
            )
        total_size += staged.size_bytes
        if total_size > max_total_bytes:
            raise HTTPException(status_code=400, detail=_("Upload exceeds maximum allowed size for this request"))


async def begin_request_upload(
    db: AsyncSession,
    *,
    req: FileRequest,
    staged_files: list[StagedFile],
    uploader_name: str | None,
    uploader_email: str | None,
    app_settings: AppSettings,
    ip_address: str | None,
) -> RequestUpload:
    _validate_staged_request_files(
        staged_files,
        max_total_bytes=effective_request_max_total_bytes(req, app_settings),
        file_type_blocklist=app_settings.file_type_blocklist,
    )

    upload = RequestUpload(
        file_request_id=req.id,
        uploader_name=uploader_name,
        uploader_email=uploader_email,
        ip_address=ip_address,
        is_preparing=True,
    )
    db.add(upload)
    req.upload_count += 1
    await db.commit()
    await db.refresh(upload)
    return upload


async def finalize_request_upload_files(
    upload_id: UUID,
    request_id: UUID,
    staged_files: list[StagedFile],
    *,
    uploader_name: str | None,
    uploader_email: str | None,
    ip_address: str | None,
) -> None:
    """Background task: move staged files into the request upload folder and mark it ready."""
    try:
        async with async_session() as db:
            req = await db.get(FileRequest, request_id)
            upload = await db.get(RequestUpload, upload_id)
            if req is None or upload is None:
                return
            app_settings = await get_app_settings(db)
            creator = await db.get(User, req.created_by)
            if creator is None:
                return

            storage = get_storage()
            for staged in staged_files:
                rel_path = f"requests/{req.id}/{upload.id}/{staged.id}/{_safe_filename(staged.original_name)}"
                await storage.move_file(staged.storage_path, rel_path)
                db.add(
                    UploadFile(
                        upload_id=upload.id,
                        original_name=staged.original_name,
                        storage_path=rel_path,
                        size_bytes=staged.size_bytes,
                        content_type=staged.content_type,
                    )
                )
            upload.is_preparing = False
            await db.commit()

        await discard_staged_paths(staged_files)

        async with async_session() as db:
            req = await db.get(FileRequest, request_id)
            app_settings = await get_app_settings(db)
            creator = await db.get(User, req.created_by) if req else None
            if req is None or creator is None:
                return
            await send_upload_notify(
                app_settings,
                to=creator.email,
                title=req.title,
                dashboard_link=f"{settings.base_url.rstrip('/')}/requests",
                locale=creator.locale,
            )
            await log_audit(
                db,
                action="file_request.uploaded",
                resource_type="file_request",
                resource_id=str(req.id),
                ip_address=ip_address,
                metadata={"uploader_email": uploader_email, "file_count": len(staged_files)},
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to finalize request upload %s; rolling back", upload_id)
        try:
            async with async_session() as db:
                req = await db.get(FileRequest, request_id)
                upload = await db.get(RequestUpload, upload_id)
                if upload is not None:
                    await db.delete(upload)
                if req is not None:
                    req.upload_count = max(0, req.upload_count - 1)
                    await db.commit()
        except Exception:
            logger.exception("Also failed to roll back broken request upload %s", upload_id)
        await discard_staged_paths(staged_files)


async def handle_public_upload(
    db: AsyncSession,
    *,
    req: FileRequest,
    files: list[UploadFile],
    uploader_name: str | None,
    uploader_email: str | None,
    app_settings: AppSettings,
    creator: User,
    ip_address: str | None,
) -> RequestUpload:
    blocklist = parse_blocklist(app_settings.file_type_blocklist)
    storage = get_storage()
    valid_files = [f for f in files if f.filename]
    if not valid_files:
        raise HTTPException(status_code=400, detail=_("Select at least one file to upload"))

    upload = RequestUpload(
        file_request_id=req.id,
        uploader_name=uploader_name,
        uploader_email=uploader_email,
        ip_address=ip_address,
    )
    db.add(upload)
    await db.flush()

    total_size = 0
    saved_count = 0
    for f in valid_files:
        if is_extension_blocked(f.filename, blocklist):
            raise HTTPException(status_code=400, detail=_("File type not allowed: %(filename)s") % {"filename": f.filename})
        content = await f.read()
        if len(content) > app_settings.max_file_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=_("File exceeds maximum size (%(max_mb)s MB): %(filename)s")
                % {"max_mb": app_settings.max_file_size_bytes // (1024 * 1024), "filename": f.filename},
            )
        total_size += len(content)
        if total_size > effective_request_max_total_bytes(req, app_settings):
            raise HTTPException(status_code=400, detail=_("Upload exceeds maximum allowed size for this request"))
        rel_path = f"requests/{req.id}/{upload.id}/{uuid4()}/{_safe_filename(f.filename)}"
        await storage.save_file(rel_path, content)
        db.add(
            UploadFile(
                upload_id=upload.id,
                original_name=f.filename,
                storage_path=rel_path,
                size_bytes=len(content),
                content_type=f.content_type,
            )
        )
        saved_count += 1

    if saved_count == 0:
        raise HTTPException(status_code=400, detail=_("Select at least one file to upload"))

    req.upload_count += 1
    await db.commit()
    await db.refresh(upload)

    await send_upload_notify(
        app_settings,
        to=creator.email,
        title=req.title,
        dashboard_link=f"{settings.base_url.rstrip('/')}/requests",
        locale=creator.locale,
    )
    await log_audit(
        db,
        action="file_request.uploaded",
        resource_type="file_request",
        resource_id=str(req.id),
        ip_address=ip_address,
        metadata={"uploader_email": uploader_email},
    )
    return upload


async def find_user_request(db: AsyncSession, request_id: UUID, user_id: UUID) -> FileRequest | None:
    result = await db.execute(
        select(FileRequest)
        .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
        .where(FileRequest.id == request_id, FileRequest.created_by == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_request(db: AsyncSession, request_id: UUID, user_id: UUID) -> FileRequest:
    req = await find_user_request(db, request_id, user_id)
    if not req:
        raise HTTPException(status_code=404, detail=_("File request not found"))
    return req


async def get_file_request_for_admin(db: AsyncSession, request_id: UUID) -> FileRequest:
    result = await db.execute(
        select(FileRequest)
        .options(
            selectinload(FileRequest.uploads).selectinload(RequestUpload.files),
            selectinload(FileRequest.creator),
        )
        .where(FileRequest.id == request_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail=_("File request not found"))
    return req


async def get_request_upload_file(
    db: AsyncSession, request_id: UUID, file_id: UUID, user_id: UUID
) -> UploadFile:
    req = await get_user_request(db, request_id, user_id)
    return _find_upload_file(req, file_id)


def _find_upload_file(req: FileRequest, file_id: UUID) -> UploadFile:
    for upload in req.uploads:
        if upload.is_preparing:
            continue
        for upload_file in upload.files:
            if upload_file.id == file_id and upload_file.deleted_at is None:
                return upload_file
    raise HTTPException(status_code=404, detail=_("File not found"))


async def delete_request_upload_file(
    db: AsyncSession,
    *,
    req: FileRequest,
    file_id: UUID,
    user: User,
    ip_address: str | None,
) -> None:
    file_match = _find_upload_file(req, file_id)

    file_name = file_match.original_name
    size_bytes = file_match.size_bytes
    content_type = file_match.content_type
    storage_path = file_match.storage_path
    storage = get_storage()
    await storage.delete_file(storage_path)
    file_match.deleted_at = utc_now()
    await db.commit()

    await log_audit(
        db,
        action="file_request.file_removed",
        resource_type="file_request",
        resource_id=str(req.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={
            "file_name": file_name,
            "size_bytes": size_bytes,
            "content_type": content_type,
        },
    )


def iter_upload_file(upload_file: UploadFile):
    if upload_file.deleted_at is not None:
        raise HTTPException(status_code=404, detail=_("File not found"))
    storage = get_storage()
    path = storage.absolute_path(upload_file.storage_path)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            yield chunk


def _unique_zip_name(name: str, used: dict[str, int]) -> str:
    used[name] = used.get(name, 0) + 1
    if used[name] == 1:
        return name
    path = Path(name)
    return f"{path.stem}_{used[name]}{path.suffix}"


def file_request_zip_entries(req: FileRequest) -> list[tuple[Path, str]]:
    storage = get_storage()
    used: dict[str, int] = {}
    entries: list[tuple[Path, str]] = []
    for upload in sorted(req.uploads, key=lambda item: item.created_at):
        if upload.is_preparing:
            continue
        for upload_file in upload.active_files:
            path = storage.absolute_path(upload_file.storage_path)
            arcname = _unique_zip_name(_safe_filename(upload_file.original_name), used)
            entries.append((path, arcname))
    if not entries:
        raise HTTPException(status_code=404, detail=_("No files to download"))
    return entries


def find_request_upload(req: FileRequest, upload_id: UUID) -> RequestUpload:
    for upload in req.uploads:
        if upload.id == upload_id:
            return upload
    raise HTTPException(status_code=404, detail=_("Upload not found"))


def request_upload_zip_entries(upload: RequestUpload) -> list[tuple[Path, str]]:
    if upload.is_preparing:
        raise HTTPException(status_code=503, detail=_("Upload is still being prepared"))
    storage = get_storage()
    used: dict[str, int] = {}
    entries: list[tuple[Path, str]] = []
    for upload_file in upload.active_files:
        path = storage.absolute_path(upload_file.storage_path)
        arcname = _unique_zip_name(_safe_filename(upload_file.original_name), used)
        entries.append((path, arcname))
    if not entries:
        raise HTTPException(status_code=404, detail=_("No files to download"))
    return entries


async def list_user_requests(db: AsyncSession, user_id: UUID) -> list[FileRequest]:
    result = await db.execute(
        select(FileRequest)
        .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
        .where(FileRequest.created_by == user_id)
        .order_by(FileRequest.created_at.desc())
    )
    return list(result.scalars().all())


async def update_file_request(
    db: AsyncSession,
    *,
    req: FileRequest,
    user: User,
    title: str,
    instructions: str | None,
    password: str | None,
    remove_password: bool,
    expires_at: datetime,
    max_uploads: int,
    max_total_bytes: int,
    ip_address: str | None,
    enabled: bool | None = None,
    app_settings: AppSettings | None = None,
    new_owner_id: UUID | None = None,
) -> FileRequest:
    now = _utcnow()
    if app_settings:
        ensure_expiry_within_limit(expires_at, app_settings.max_share_expiry_days)
    ensure_max_total_is_positive(max_total_bytes)
    if max_uploads != 0 and max_uploads < req.upload_count:
        raise HTTPException(
            status_code=400,
            detail=_("Max uploads cannot be less than current count (%(count)s)")
            % {"count": req.upload_count},
        )

    old_title = req.title
    old_instructions = req.instructions
    old_expires_at = req.expires_at
    old_max_uploads = req.max_uploads
    old_max_total_bytes = req.max_total_bytes
    had_password = bool(req.password_hash)
    old_enabled = not req.is_disabled

    req.title = title
    req.instructions = instructions
    req.expires_at = expires_at
    req.max_uploads = max_uploads
    req.max_total_bytes = max_total_bytes
    if ensure_utc(expires_at) >= ensure_utc(now):
        req.is_expired = False
    reset_expiry_notifications(req, expires_at, now)

    if not remove_password and not password and not req.password_hash:
        raise HTTPException(status_code=400, detail=_("Enter a password to enable protection"))

    if remove_password:
        req.password_hash = None
    elif password:
        req.password_hash = hash_password(password)

    if enabled is not None:
        req.is_disabled = not enabled

    previous_owner_id = req.created_by
    owner_changed = False
    new_owner_user: User | None = None
    if new_owner_id is not None and new_owner_id != req.created_by:
        result = await db.execute(
            select(User).where(User.id == new_owner_id, User.is_active.is_(True))
        )
        new_owner_user = result.scalar_one_or_none()
        if new_owner_user is None:
            raise HTTPException(status_code=400, detail=_("Invalid owner"))
        req.created_by = new_owner_id
        owner_changed = True

    await db.commit()
    await db.refresh(req)

    update_changes = build_file_request_update_changes(
        old_title=old_title,
        new_title=title,
        old_instructions=old_instructions,
        new_instructions=instructions,
        old_expires_at=old_expires_at,
        new_expires_at=expires_at,
        old_max_uploads=old_max_uploads,
        new_max_uploads=max_uploads,
        old_max_total_bytes=old_max_total_bytes,
        new_max_total_bytes=max_total_bytes,
        had_password=had_password,
        remove_password=remove_password,
        new_password=password,
        old_enabled=old_enabled,
        enabled=enabled,
    )
    if update_changes:
        await log_audit(
            db,
            action="file_request.updated",
            resource_type="file_request",
            resource_id=str(req.id),
            actor_id=user.id,
            ip_address=ip_address,
            metadata={"changes": update_changes},
        )
    if owner_changed and new_owner_user is not None:
        previous_owner = await db.get(User, previous_owner_id)
        await log_audit(
            db,
            action="file_request.owner_changed",
            resource_type="file_request",
            resource_id=str(req.id),
            actor_id=user.id,
            ip_address=ip_address,
            metadata=build_owner_change_metadata(
                previous_owner_email=previous_owner.email if previous_owner else None,
                new_owner_email=new_owner_user.email,
                previous_owner_id=previous_owner_id,
                new_owner_id=new_owner_id,
            ),
        )
    return req


async def delete_file_request(
    db: AsyncSession,
    *,
    req: FileRequest,
    user: User,
    ip_address: str | None,
) -> None:
    await archive_share_before_delete(
        db,
        resource_type="file_request",
        entity=req,
        reason="user_deleted",
        deleted_by=user,
        ip_address=ip_address,
    )
    request_id = req.id
    await db.delete(req)
    await db.commit()
    storage = get_storage()
    await storage.delete_directory(f"requests/{request_id}")


async def regenerate_file_request_link(
    db: AsyncSession,
    *,
    req: FileRequest,
    user: User,
    ip_address: str | None,
) -> FileRequest:
    old_token = req.public_token
    req.public_token = generate_public_token()
    await db.commit()
    await db.refresh(req)

    await log_audit(
        db,
        action="file_request.link_regenerated",
        resource_type="file_request",
        resource_id=str(req.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={
            "old_share_link": f"{settings.base_url.rstrip('/')}/r/{old_token}",
            "new_share_link": f"{settings.base_url.rstrip('/')}/r/{req.public_token}",
        },
    )
    return req
