from __future__ import annotations

from datetime import datetime

from app.services.download_limits import transfer_download_limit_reached
from app.services.datetime_display import ensure_utc
from app.models import FileRequest, Transfer


def _is_past_expiry(*, is_expired: bool, expires_at: datetime, now: datetime) -> bool:
    return is_expired or ensure_utc(expires_at) < ensure_utc(now)


def transfer_is_active(transfer: Transfer, now: datetime) -> bool:
    if transfer.is_disabled:
        return False
    if _is_past_expiry(is_expired=transfer.is_expired, expires_at=transfer.expires_at, now=now):
        return False
    if transfer_download_limit_reached(transfer):
        return False
    return True


def transfer_can_toggle(transfer: Transfer, now: datetime) -> bool:
    if _is_past_expiry(is_expired=transfer.is_expired, expires_at=transfer.expires_at, now=now):
        return False
    if transfer_download_limit_reached(transfer):
        return False
    return True


def transfer_inactive_reason(transfer: Transfer, now: datetime) -> str | None:
    if transfer.is_disabled:
        return "Link disabled"
    if _is_past_expiry(is_expired=transfer.is_expired, expires_at=transfer.expires_at, now=now):
        return "Expired"
    if transfer_download_limit_reached(transfer):
        return "Download limit reached"
    return None


def file_request_is_active(req: FileRequest, now: datetime) -> bool:
    if req.is_disabled:
        return False
    if _is_past_expiry(is_expired=req.is_expired, expires_at=req.expires_at, now=now):
        return False
    if req.upload_count >= req.max_uploads:
        return False
    return True


def file_request_can_toggle(req: FileRequest, now: datetime) -> bool:
    if _is_past_expiry(is_expired=req.is_expired, expires_at=req.expires_at, now=now):
        return False
    if req.upload_count >= req.max_uploads:
        return False
    return True


def file_request_inactive_reason(req: FileRequest, now: datetime) -> str | None:
    if req.is_disabled:
        return "Link disabled"
    if _is_past_expiry(is_expired=req.is_expired, expires_at=req.expires_at, now=now):
        return "Expired"
    if req.upload_count >= req.max_uploads:
        return "Upload limit reached"
    return None
