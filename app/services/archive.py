from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import ArchivedShare, AuditLog, FileRequest, RequestUpload, Transfer, User
from app.services.audit import EXCLUDED_TIMELINE_ACTIONS, list_share_audit, parse_audit_metadata, resolve_actor_emails, resolve_owner_emails_for_audit
from app.services.share_timeline import build_request_timeline, build_transfer_timeline, transfer_file_download_logs


async def load_transfer_activity(
    db: AsyncSession,
    transfer: Transfer,
) -> tuple[list, list]:
    all_logs = sorted(transfer.download_logs, key=lambda log: log.created_at, reverse=True)
    download_logs = transfer_file_download_logs(all_logs)
    audit_events = await list_share_audit(db, resource_type="transfer", resource_id=str(transfer.id))
    actor_emails = await resolve_actor_emails(db, audit_events)
    owner_emails = await resolve_owner_emails_for_audit(db, audit_events)
    timeline = build_transfer_timeline(
        download_logs=transfer.download_logs,
        audit_events=audit_events,
        actor_emails=actor_emails,
        owner_emails=owner_emails,
    )
    return download_logs, timeline


async def load_request_activity(
    db: AsyncSession,
    req: FileRequest,
) -> tuple[list[RequestUpload], list]:
    uploads = [u for u in req.uploads if not u.is_preparing]
    uploads_sorted = sorted(uploads, key=lambda u: u.created_at, reverse=True)
    audit_events = await list_share_audit(db, resource_type="file_request", resource_id=str(req.id))
    actor_emails = await resolve_actor_emails(db, audit_events)
    owner_emails = await resolve_owner_emails_for_audit(db, audit_events)
    timeline = build_request_timeline(
        uploads=uploads,
        audit_events=audit_events,
        actor_emails=actor_emails,
        owner_emails=owner_emails,
    )
    return uploads_sorted, timeline


def _serialize_download_log(log) -> dict:
    return {
        "ip_address": log.ip_address,
        "download_type": log.download_type,
        "file_name": log.file_name,
        "at": log.created_at.isoformat(),
    }


def _serialize_upload(upload: RequestUpload) -> dict:
    return {
        "uploader_name": upload.uploader_name,
        "uploader_email": upload.uploader_email,
        "ip_address": upload.ip_address,
        "at": upload.created_at.isoformat(),
        "files": [
            {
                "name": f.original_name,
                "size_bytes": f.size_bytes,
                "content_type": f.content_type,
            }
            for f in upload.files
        ],
    }


def _serialize_audit_event(entry, actor_emails: dict[uuid.UUID, str]) -> dict:
    actor_email = actor_emails.get(entry.actor_id) if entry.actor_id else None
    return {
        "action": entry.action,
        "at": entry.created_at.isoformat(),
        "actor_email": actor_email,
        "ip_address": entry.ip_address,
        "metadata": parse_audit_metadata(entry),
    }


def _archive_deletion_action(resource_type: str, reason: str) -> str:
    if reason == "auto_purged":
        return f"{resource_type}.purged"
    return f"{resource_type}.deleted"


async def archive_share_before_delete(
    db: AsyncSession,
    *,
    resource_type: str,
    entity: Transfer | FileRequest,
    reason: str,
    deleted_by: User | None = None,
    ip_address: str | None = None,
) -> None:
    public_path = "d" if resource_type == "transfer" else "r"
    share_link = f"{settings.base_url.rstrip('/')}/{public_path}/{entity.public_token}"
    db.add(
        AuditLog(
            actor_id=deleted_by.id if deleted_by else None,
            action=_archive_deletion_action(resource_type, reason),
            resource_type=resource_type,
            resource_id=str(entity.id),
            ip_address=ip_address,
            metadata_json=json.dumps({
                "archived_reason": reason,
                "share_link": share_link,
            }),
        )
    )
    await db.flush()

    creator = await db.get(User, entity.created_by)
    creator_email = creator.email if creator else "unknown"

    if resource_type == "transfer":
        result = await db.execute(
            select(Transfer)
            .options(
                selectinload(Transfer.files),
                selectinload(Transfer.download_logs),
            )
            .where(Transfer.id == entity.id)
        )
        transfer = result.scalar_one()
        files = transfer.files
        download_logs = transfer.download_logs
        message = transfer.message
        activity_count = transfer.download_count
        snapshot_files = [
            {
                "name": f.original_name,
                "size_bytes": f.size_bytes,
                "content_type": f.content_type,
                "added_at": f.created_at.isoformat(),
            }
            for f in files
        ]
        audit_events = [
            e
            for e in await list_share_audit(db, resource_type="transfer", resource_id=str(transfer.id))
            if e.action not in EXCLUDED_TIMELINE_ACTIONS
        ]
        actor_emails = await resolve_actor_emails(db, audit_events)
        snapshot = {
            "message": message,
            "share_link": share_link,
            "files": snapshot_files,
            "download_logs": [_serialize_download_log(log) for log in download_logs],
            "uploads": [],
            "audit_events": [_serialize_audit_event(e, actor_emails) for e in audit_events],
        }
        file_count = len(files)
        total_bytes = sum(f.size_bytes for f in files)
        title = transfer.title
        created_at = transfer.created_at
        expires_at = transfer.expires_at
        created_by = transfer.created_by
    else:
        result = await db.execute(
            select(FileRequest)
            .options(selectinload(FileRequest.uploads).selectinload(RequestUpload.files))
            .where(FileRequest.id == entity.id)
        )
        req = result.scalar_one()
        uploads = [u for u in req.uploads if not u.is_preparing]
        all_files = [f for u in uploads for f in u.files]
        message = req.instructions
        activity_count = req.upload_count
        snapshot_files = [
            {
                "name": f.original_name,
                "size_bytes": f.size_bytes,
                "content_type": f.content_type,
                "added_at": f.created_at.isoformat(),
            }
            for f in all_files
        ]
        audit_events = [
            e
            for e in await list_share_audit(db, resource_type="file_request", resource_id=str(req.id))
            if e.action not in EXCLUDED_TIMELINE_ACTIONS
        ]
        actor_emails = await resolve_actor_emails(db, audit_events)
        snapshot = {
            "message": message,
            "share_link": share_link,
            "files": snapshot_files,
            "download_logs": [],
            "uploads": [_serialize_upload(u) for u in uploads],
            "audit_events": [_serialize_audit_event(e, actor_emails) for e in audit_events],
        }
        file_count = len(all_files)
        total_bytes = sum(f.size_bytes for f in all_files)
        title = req.title
        created_at = req.created_at
        expires_at = req.expires_at
        created_by = req.created_by

    archived = ArchivedShare(
        original_id=entity.id,
        resource_type=resource_type,
        archived_reason=reason,
        deleted_by_id=deleted_by.id if deleted_by else None,
        creator_id=created_by,
        creator_email=creator_email,
        title=title,
        created_at=created_at,
        expires_at=expires_at,
        file_count=file_count,
        total_bytes=total_bytes,
        activity_count=activity_count,
        snapshot_json=json.dumps(snapshot),
    )
    db.add(archived)
    await db.flush()
