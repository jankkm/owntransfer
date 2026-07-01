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


def transfer_is_enabled(transfer: Transfer) -> bool:
    return not transfer.is_disabled


def transfer_is_expired(transfer: Transfer, now: datetime) -> bool:
    return _is_past_expiry(is_expired=transfer.is_expired, expires_at=transfer.expires_at, now=now)


def transfer_is_active(transfer: Transfer, now: datetime) -> bool:
    """Whether the owner has left the link enabled (independent of expiry)."""
    return transfer_is_enabled(transfer)


def transfer_is_accessible(transfer: Transfer, now: datetime) -> bool:
    """Whether the public can currently use the link."""
    if transfer.is_disabled:
        return False
    if transfer_is_expired(transfer, now):
        return False
    if transfer_download_limit_reached(transfer):
        return False
    return True


def transfer_can_toggle(transfer: Transfer, now: datetime) -> bool:
    return True


def transfer_inactive_reason_code(transfer: Transfer, now: datetime) -> str | None:
    if transfer.is_disabled:
        return REASON_DISABLED
    if transfer_is_expired(transfer, now):
        return REASON_EXPIRED
    if transfer_download_limit_reached(transfer):
        return REASON_DOWNLOAD_LIMIT
    return None


def transfer_inactive_reason(transfer: Transfer, now: datetime) -> str | None:
    code = transfer_inactive_reason_code(transfer, now)
    return _REASON_LABELS[code]() if code else None


def file_request_is_enabled(req: FileRequest) -> bool:
    return not req.is_disabled


def file_request_is_expired(req: FileRequest, now: datetime) -> bool:
    return _is_past_expiry(is_expired=req.is_expired, expires_at=req.expires_at, now=now)


def file_request_is_active(req: FileRequest, now: datetime) -> bool:
    return file_request_is_enabled(req)


def file_request_is_accessible(req: FileRequest, now: datetime) -> bool:
    if req.is_disabled:
        return False
    if file_request_is_expired(req, now):
        return False
    if req.max_uploads != 0 and req.upload_count >= req.max_uploads:
        return False
    return True


def file_request_can_toggle(req: FileRequest, now: datetime) -> bool:
    return True


def file_request_inactive_reason_code(req: FileRequest, now: datetime) -> str | None:
    if req.is_disabled:
        return REASON_DISABLED
    if file_request_is_expired(req, now):
        return REASON_EXPIRED
    if req.max_uploads != 0 and req.upload_count >= req.max_uploads:
        return REASON_UPLOAD_LIMIT
    return None


def file_request_inactive_reason(req: FileRequest, now: datetime) -> str | None:
    code = file_request_inactive_reason_code(req, now)
    return _REASON_LABELS[code]() if code else None
