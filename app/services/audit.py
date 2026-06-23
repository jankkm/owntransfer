from __future__ import annotations

import json
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


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
