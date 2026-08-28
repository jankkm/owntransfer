from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.i18n import _
from app.models import AuditLog, RequestUpload, TransferDownloadLog
from app.services.audit import EXCLUDED_TIMELINE_ACTIONS, OWNER_CHANGED_ACTION_SUFFIX, parse_audit_metadata


@dataclass
class TimelineEntry:
    kind: str
    at: datetime
    label: str
    actor_email: str | None = None
    ip_address: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


_ACTION_MSGIDS: dict[str, str] = {
    "transfer.created": "Transfer created",
    "transfer.updated": "Transfer updated",
    "transfer.file_added": "File added",
    "transfer.file_removed": "File removed",
    "transfer.link_regenerated": "Share link regenerated",
    "transfer.owner_changed": "Owner changed",
    "transfer.deleted": "Transfer deleted",
    "transfer.purged": "Transfer auto-deleted after expiry",
    "file_request.created": "File request created",
    "file_request.updated": "File request updated",
    "file_request.uploaded": "Files uploaded",
    "file_request.file_removed": "File removed",
    "file_request.link_regenerated": "Share link regenerated",
    "file_request.owner_changed": "Owner changed",
    "file_request.deleted": "File request deleted",
    "file_request.purged": "File request auto-deleted after expiry",
}


def _action_label(action: str) -> str:
    msgid = _ACTION_MSGIDS.get(action)
    return _(msgid) if msgid else action


def download_type_label(download_type: str) -> str:
    return _("ZIP download") if download_type == "zip" else _("File download")


def _enrich_owner_change_metadata(meta: dict, owner_emails: dict[str, str]) -> dict:
    if meta.get("changes"):
        return meta
    prev_id = str(meta.get("previous_owner_id") or "")
    new_id = str(meta.get("new_owner_id") or "")
    if not prev_id and not new_id:
        return meta
    old = meta.get("previous_owner_email") or owner_emails.get(prev_id) or prev_id
    new = meta.get("new_owner_email") or owner_emails.get(new_id) or new_id
    enriched = dict(meta)
    enriched["changes"] = [
        {
            "field": "owner",
            "label": _("Owner"),
            "old": old or _("(unknown)"),
            "new": new or _("(unknown)"),
        }
    ]
    return enriched


def _audit_to_entry(
    entry: AuditLog,
    actor_emails: dict,
    owner_emails: dict[str, str],
) -> TimelineEntry | None:
    if entry.action in EXCLUDED_TIMELINE_ACTIONS:
        return None
    meta = parse_audit_metadata(entry)
    if entry.action.endswith(OWNER_CHANGED_ACTION_SUFFIX):
        meta = _enrich_owner_change_metadata(meta, owner_emails)
    actor_email = actor_emails.get(entry.actor_id) if entry.actor_id else None
    return TimelineEntry(
        kind=entry.action.split(".", 1)[-1],
        at=entry.created_at,
        label=_action_label(entry.action),
        actor_email=actor_email,
        ip_address=entry.ip_address,
        details=meta,
    )


def _download_to_entry(log: TransferDownloadLog) -> TimelineEntry:
    file_label = log.file_name or _("All files (ZIP)")
    download_type = download_type_label(log.download_type)
    return TimelineEntry(
        kind="download",
        at=log.created_at,
        label=_("%(type)s: %(file)s") % {"type": download_type, "file": file_label},
        ip_address=log.ip_address,
        details={
            "file_name": log.file_name,
            "download_type": log.download_type,
        },
    )


def _upload_to_entry(upload: RequestUpload) -> TimelineEntry:
    files = [
        {
            "name": f.original_name,
            "size_bytes": f.size_bytes,
            "content_type": f.content_type,
        }
        for f in upload.files
    ]
    return TimelineEntry(
        kind="upload",
        at=upload.created_at,
        label=_("Files uploaded (%(count)s)") % {"count": len(files)},
        ip_address=upload.ip_address,
        details={
            "uploader_name": upload.uploader_name,
            "uploader_email": upload.uploader_email,
            "files": files,
        },
    )


def _snapshot_download_to_entry(data: dict) -> TimelineEntry:
    file_name = data.get("file_name")
    download_type = data.get("download_type", "file")
    file_label = file_name or _("All files (ZIP)")
    type_label = download_type_label(download_type)
    at = datetime.fromisoformat(data["at"])
    return TimelineEntry(
        kind="download",
        at=at,
        label=_("%(type)s: %(file)s") % {"type": type_label, "file": file_label},
        ip_address=data.get("ip_address"),
        details={"file_name": file_name, "download_type": download_type},
    )


def _snapshot_audit_to_entry(data: dict, owner_emails: dict[str, str]) -> TimelineEntry | None:
    action = data.get("action", "")
    if action in EXCLUDED_TIMELINE_ACTIONS:
        return None
    at = datetime.fromisoformat(data["at"])
    meta = data.get("metadata") or {}
    if action.endswith(OWNER_CHANGED_ACTION_SUFFIX):
        meta = _enrich_owner_change_metadata(meta, owner_emails)
    return TimelineEntry(
        kind=action.split(".", 1)[-1] if "." in action else action,
        at=at,
        label=_action_label(action),
        actor_email=data.get("actor_email"),
        ip_address=data.get("ip_address"),
        details=meta,
    )


def _snapshot_upload_to_entry(data: dict) -> TimelineEntry:
    files = data.get("files") or []
    at = datetime.fromisoformat(data["at"])
    return TimelineEntry(
        kind="upload",
        at=at,
        label=_("Files uploaded (%(count)s)") % {"count": len(files)},
        ip_address=data.get("ip_address"),
        details={
            "uploader_name": data.get("uploader_name"),
            "uploader_email": data.get("uploader_email"),
            "files": files,
        },
    )


def build_transfer_timeline(
    *,
    download_logs: list[TransferDownloadLog],
    audit_events: list[AuditLog],
    actor_emails: dict,
    owner_emails: dict[str, str] | None = None,
) -> list[TimelineEntry]:
    owner_emails = owner_emails or {}
    entries: list[TimelineEntry] = []
    for log in download_logs:
        entries.append(_download_to_entry(log))
    for event in audit_events:
        entry = _audit_to_entry(event, actor_emails, owner_emails)
        if entry:
            entries.append(entry)
    entries.sort(key=lambda e: e.at, reverse=True)
    return entries


def build_request_timeline(
    *,
    uploads: list[RequestUpload],
    audit_events: list[AuditLog],
    actor_emails: dict,
    owner_emails: dict[str, str] | None = None,
) -> list[TimelineEntry]:
    owner_emails = owner_emails or {}
    entries: list[TimelineEntry] = []
    for upload in uploads:
        if upload.is_preparing:
            continue
        entries.append(_upload_to_entry(upload))
    for event in audit_events:
        entry = _audit_to_entry(event, actor_emails, owner_emails)
        if entry:
            entries.append(entry)
    entries.sort(key=lambda e: e.at, reverse=True)
    return entries


def build_timeline_from_snapshot(
    snapshot: dict,
    resource_type: str,
    owner_emails: dict[str, str] | None = None,
) -> list[TimelineEntry]:
    owner_emails = owner_emails or {}
    entries: list[TimelineEntry] = []
    if resource_type == "transfer":
        for item in snapshot.get("download_logs") or []:
            entries.append(_snapshot_download_to_entry(item))
    else:
        for item in snapshot.get("uploads") or []:
            entries.append(_snapshot_upload_to_entry(item))
    for item in snapshot.get("audit_events") or []:
        entry = _snapshot_audit_to_entry(item, owner_emails)
        if entry:
            entries.append(entry)
    entries.sort(key=lambda e: e.at, reverse=True)
    return entries
