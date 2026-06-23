from __future__ import annotations

from datetime import datetime

from app.i18n import _
from app.services.download_limits import transfer_download_limit_reached
from app.services.datetime_display import ensure_utc
from app.models import FileRequest, Transfer

REASON_DISABLED = "disabled"
REASON_EXPIRED = "expired"
REASON_DOWNLOAD_LIMIT = "download_limit"
REASON_UPLOAD_LIMIT = "upload_limit"

_REASON_LABELS = {
    REASON_DISABLED: lambda: _("Link disabled"),
    REASON_EXPIRED: lambda: _("Expired"),
    REASON_DOWNLOAD_LIMIT: lambda: _("Download limit reached"),
    REASON_UPLOAD_LIMIT: lambda: _("Upload limit reached"),
}


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


def transfer_inactive_reason_code(transfer: Transfer, now: datetime) -> str | None:
    if transfer.is_disabled:
        return REASON_DISABLED
    if _is_past_expiry(is_expired=transfer.is_expired, expires_at=transfer.expires_at, now=now):
        return REASON_EXPIRED
    if transfer_download_limit_reached(transfer):
        return REASON_DOWNLOAD_LIMIT
    return None


def transfer_inactive_reason(transfer: Transfer, now: datetime) -> str | None:
    code = transfer_inactive_reason_code(transfer, now)
    return _REASON_LABELS[code]() if code else None


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


def file_request_inactive_reason_code(req: FileRequest, now: datetime) -> str | None:
    if req.is_disabled:
        return REASON_DISABLED
    if _is_past_expiry(is_expired=req.is_expired, expires_at=req.expires_at, now=now):
        return REASON_EXPIRED
    if req.upload_count >= req.max_uploads:
        return REASON_UPLOAD_LIMIT
    return None


def file_request_inactive_reason(req: FileRequest, now: datetime) -> str | None:
    code = file_request_inactive_reason_code(req, now)
    return _REASON_LABELS[code]() if code else None
