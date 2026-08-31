from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class UserOAuthLinkGrant(Base, TimestampMixin):
    __tablename__ = "user_oauth_link_grants"
    __table_args__ = (UniqueConstraint("user_id", "provider_key", name="uq_user_oauth_link_grant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
