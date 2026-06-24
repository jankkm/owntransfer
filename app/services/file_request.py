from __future__ import annotations

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
from app.i18n import _
from app.models import AppSettings, FileRequest, RequestUpload, UploadFile, User
from app.services.audit import log_audit
from app.services.email import send_request_email, send_upload_notify
from app.services.datetime_display import ensure_expiry_within_limit, ensure_utc, format_datetime_with_tz, utc_now
from app.services.settings import generate_public_token, is_extension_blocked, parse_blocklist
from app.services.share_status import file_request_can_toggle
from app.services.share_lifecycle import is_past_expiry, reset_expiry_notifications
from app.services.staging import StagedFile
from app.services.storage import get_storage


def _utcnow() -> datetime:
    return utc_now()


def _safe_filename(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    return re.sub(r"[^\w.\- ()]", "_", base) or "file"


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


def ensure_request_accessible(req: FileRequest) -> None:
    if req.is_disabled:
        raise HTTPException(status_code=403, detail=_("This link has been disabled"))
    if is_past_expiry(is_expired=req.is_expired, expires_at=req.expires_at):
        raise HTTPException(status_code=410, detail=_("This link has expired"))
    if req.upload_count >= req.max_uploads:
        raise HTTPException(status_code=410, detail=_("Upload limit reached"))


def verify_request_password(req: FileRequest, password: str | None) -> bool:
    if req.password_hash:
        return verify_password(password or "", req.password_hash)
    return True


async def finalize_request_upload(
    db: AsyncSession,
    *,
    req: FileRequest,
    staged_files: list[StagedFile],
    uploader_name: str | None,
    uploader_email: str | None,
    app_settings: AppSettings,
    creator: User,
    ip_address: str | None,
) -> RequestUpload:
    if not staged_files:
        raise HTTPException(status_code=400, detail=_("Add at least one file to upload"))

    blocklist = parse_blocklist(app_settings.file_type_blocklist)
    storage = get_storage()
    upload = RequestUpload(
        file_request_id=req.id,
        uploader_name=uploader_name,
        uploader_email=uploader_email,
        ip_address=ip_address,
    )
    db.add(upload)
    await db.flush()

    total_size = 0
    for staged in staged_files:
        if is_extension_blocked(staged.original_name, blocklist):
            raise HTTPException(status_code=400, detail=_("File type not allowed: %(filename)s") % {"filename": staged.original_name})
        total_size += staged.size_bytes
        if total_size > req.max_total_bytes:
            raise HTTPException(status_code=400, detail=_("Upload exceeds maximum allowed size for this request"))
        rel_path = f"requests/{req.id}/{upload.id}/{staged.id}/{_safe_filename(staged.original_name)}"
        content = storage.absolute_path(staged.storage_path).read_bytes()
        await storage.save_file(rel_path, content)
        db.add(
            UploadFile(
                upload_id=upload.id,
                original_name=staged.original_name,
                storage_path=rel_path,
                size_bytes=staged.size_bytes,
                content_type=staged.content_type,
            )
        )

    req.upload_count += 1
    await db.commit()
    await db.refresh(upload)

    await send_upload_notify(
        app_settings,
        to=creator.email,
        title=req.title,
        dashboard_link=f"{settings.base_url.rstrip('/')}/requests",
    )
    await log_audit(
        db,
        action="file_request.uploaded",
        resource_type="file_request",
        resource_id=str(req.id),
        ip_address=ip_address,
        metadata={"uploader_email": uploader_email, "file_count": len(staged_files)},
    )
    return upload


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
        if total_size > req.max_total_bytes:
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


async def get_user_request(db: AsyncSession, request_id: UUID, user_id: UUID) -> FileRequest:
    result = await db.execute(
        select(FileRequest)
        .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
        .where(FileRequest.id == request_id, FileRequest.created_by == user_id)
    )
    req = result.scalar_one_or_none()
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
        for upload_file in upload.files:
            if upload_file.id == file_id:
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
    upload_match: RequestUpload | None = None
    file_match: UploadFile | None = None
    for upload in req.uploads:
        for upload_file in upload.files:
            if upload_file.id == file_id:
                upload_match = upload
                file_match = upload_file
                break
        if file_match:
            break
    if not file_match or not upload_match:
        raise HTTPException(status_code=404, detail=_("File not found"))

    file_name = file_match.original_name
    storage = get_storage()
    await storage.delete_file(file_match.storage_path)
    await db.delete(file_match)
    upload_match.files = [f for f in upload_match.files if f.id != file_id]

    if not upload_match.files:
        await db.delete(upload_match)
        req.upload_count = max(0, req.upload_count - 1)
        req.uploads = [upload for upload in req.uploads if upload.id != upload_match.id]

    await db.commit()

    await log_audit(
        db,
        action="file_request.file_removed",
        resource_type="file_request",
        resource_id=str(req.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={"file_name": file_name},
    )


def iter_upload_file(upload_file: UploadFile):
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
        for upload_file in upload.files:
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
    storage = get_storage()
    used: dict[str, int] = {}
    entries: list[tuple[Path, str]] = []
    for upload_file in upload.files:
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
) -> FileRequest:
    now = _utcnow()
    if app_settings:
        ensure_expiry_within_limit(expires_at, app_settings.max_share_expiry_days)
    if max_uploads < req.upload_count:
        raise HTTPException(
            status_code=400,
            detail=_("Max uploads cannot be less than current count (%(count)s)")
            % {"count": req.upload_count},
        )

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

    if enabled is not None and file_request_can_toggle(req, now):
        req.is_disabled = not enabled

    await db.commit()
    await db.refresh(req)

    await log_audit(
        db,
        action="file_request.updated",
        resource_type="file_request",
        resource_id=str(req.id),
        actor_id=user.id,
        ip_address=ip_address,
        metadata={"title": title},
    )
    return req


async def delete_file_request(
    db: AsyncSession,
    *,
    req: FileRequest,
    user: User,
    ip_address: str | None,
) -> None:
    storage = get_storage()
    await storage.delete_directory(f"requests/{req.id}")
    request_id = str(req.id)
    await db.delete(req)
    await db.commit()
    await log_audit(
        db,
        action="file_request.deleted",
        resource_type="file_request",
        resource_id=request_id,
        actor_id=user.id,
        ip_address=ip_address,
    )


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
        metadata={"old_token_prefix": old_token[:8]},
    )
    return req
