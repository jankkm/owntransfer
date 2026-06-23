from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class FileRequest(Base, TimestampMixin):
    __tablename__ = "file_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    public_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_uploads: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    upload_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_expired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expired_unused_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purge_warned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    creator = relationship("User", back_populates="file_requests")
    uploads = relationship(
        "RequestUpload", back_populates="file_request", cascade="all, delete-orphan"
    )


class RequestUpload(Base, TimestampMixin):
    __tablename__ = "request_uploads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    file_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("file_requests.id", ondelete="CASCADE"), nullable=False
    )
    uploader_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    uploader_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    file_request = relationship("FileRequest", back_populates="uploads")
    files = relationship(
        "UploadFile", back_populates="upload", cascade="all, delete-orphan"
    )


class UploadFile(Base, TimestampMixin):
    __tablename__ = "upload_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    upload_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("request_uploads.id", ondelete="CASCADE"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    upload = relationship("RequestUpload", back_populates="files")
