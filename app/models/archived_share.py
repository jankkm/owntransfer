from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow


class ArchivedShare(Base):
    __tablename__ = "archived_shares"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    original_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    archived_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    deleted_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    creator_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    creator_email: Mapped[str] = mapped_column(String(320), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    activity_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
