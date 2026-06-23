from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid


class TransferDownloadLog(Base, TimestampMixin):
    __tablename__ = "transfer_download_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("transfers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    download_type: Mapped[str] = mapped_column(String(32), nullable=False, default="file")
    file_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    transfer = relationship("Transfer", back_populates="download_logs")
