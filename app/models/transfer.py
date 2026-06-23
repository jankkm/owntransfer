from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class Transfer(Base, TimestampMixin):
    __tablename__ = "transfers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    public_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_downloads: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notify_on_download: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recipient_emails: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_expired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expired_unused_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purge_warned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    creator = relationship("User", back_populates="transfers")
    files = relationship(
        "TransferFile", back_populates="transfer", cascade="all, delete-orphan"
    )
    download_logs = relationship(
        "TransferDownloadLog",
        back_populates="transfer",
        cascade="all, delete-orphan",
    )


class TransferFile(Base, TimestampMixin):
    __tablename__ = "transfer_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    transfer = relationship("Transfer", back_populates="files")
