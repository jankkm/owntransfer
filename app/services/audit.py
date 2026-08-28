from __future__ import annotations

import json
import uuid
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

SHARE_RESOURCE_TYPES = frozenset({"transfer", "file_request"})
EXCLUDED_TIMELINE_ACTIONS = frozenset({"transfer.downloaded", "file_request.uploaded"})
OWNER_CHANGED_ACTION_SUFFIX = ".owner_changed"


async def log_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    db.add(entry)
    await db.commit()


async def list_share_audit(
    db: AsyncSession,
    *,
    resource_type: str,
    resource_id: str,
) -> list[AuditLog]:
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == resource_id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    return list(result.scalars().all())


async def list_system_audit(db: AsyncSession, *, limit: int = 50) -> list[AuditLog]:
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.resource_type.not_in(SHARE_RESOURCE_TYPES))
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def delete_share_audit(
    db: AsyncSession,
    *,
    resource_type: str,
    resource_id: str,
) -> int:
    result = await db.execute(
        delete(AuditLog).where(
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == resource_id,
        )
    )
    return result.rowcount or 0


async def resolve_actor_emails(db: AsyncSession, audit_events: list[AuditLog]) -> dict[uuid.UUID, str]:
    from app.models import User

    actor_ids = {event.actor_id for event in audit_events if event.actor_id}
    if not actor_ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(actor_ids)))
    return {user.id: user.email for user in result.scalars().all()}


def _owner_ids_from_metadata(meta: dict) -> set[uuid.UUID]:
    owner_ids: set[uuid.UUID] = set()
    for key in ("previous_owner_id", "new_owner_id"):
        raw = meta.get(key)
        if not raw:
            continue
        try:
            owner_ids.add(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return owner_ids


async def resolve_owner_emails_for_audit(
    db: AsyncSession,
    audit_events: list[AuditLog],
) -> dict[str, str]:
    from app.models import User

    owner_ids: set[uuid.UUID] = set()
    for entry in audit_events:
        if not entry.action.endswith(OWNER_CHANGED_ACTION_SUFFIX):
            continue
        meta = parse_audit_metadata(entry)
        if meta.get("changes"):
            continue
        owner_ids.update(_owner_ids_from_metadata(meta))
    if not owner_ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(owner_ids)))
    return {str(user.id): user.email for user in result.scalars().all()}


def collect_owner_ids_from_snapshot(snapshot: dict) -> set[uuid.UUID]:
    owner_ids: set[uuid.UUID] = set()
    for item in snapshot.get("audit_events") or []:
        if not str(item.get("action", "")).endswith(OWNER_CHANGED_ACTION_SUFFIX):
            continue
        meta = item.get("metadata") or {}
        if meta.get("changes"):
            continue
        owner_ids.update(_owner_ids_from_metadata(meta))
    return owner_ids


async def resolve_owner_emails_by_id(db: AsyncSession, owner_ids: set[uuid.UUID]) -> dict[str, str]:
    from app.models import User

    if not owner_ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(owner_ids)))
    return {str(user.id): user.email for user in result.scalars().all()}


def parse_audit_metadata(entry: AuditLog) -> dict:
    if not entry.metadata_json:
        return {}
    try:
        data = json.loads(entry.metadata_json)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
