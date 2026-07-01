from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Boolean, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    app_name: Mapped[str] = mapped_column(String(255), nullable=False, default="OwnTransfer")
    logo_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    logo_content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    color_scheme: Mapped[str] = mapped_column(String(32), nullable=False, default="#2563eb")
    max_file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    default_expiry_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    max_share_expiry_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365)
    max_downloads_default: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_uploads_default: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    smtp_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    smtp_user: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_from: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_local_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_user_share_emails: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    file_type_blocklist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upload_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    purge_grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    purge_notify_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    setup_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    impressum_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    impressum_markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    privacy_policy_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    privacy_policy_markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_tpl_share: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_tpl_request: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_tpl_upload_notify: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_tpl_download_notify: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_tpl_expired_unused: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_tpl_purge_reminder: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_subj_share: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    email_subj_request: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    email_subj_upload_notify: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    email_subj_download_notify: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    email_subj_expired_unused: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    email_subj_purge_reminder: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
