from __future__ import annotations

from datetime import datetime, timedelta

from app.i18n import _, ngettext
from app.services.datetime_display import ensure_utc, format_datetime_with_tz, utc_now
from app.models import FileRequest, Transfer


def _utcnow() -> datetime:
    return utc_now()


def is_past_expiry(*, is_expired: bool, expires_at: datetime, now: datetime | None = None) -> bool:
    now = now or _utcnow()
    return is_expired or ensure_utc(expires_at) < ensure_utc(now)


def purge_cutoff(expires_at: datetime, purge_grace_days: int) -> datetime:
    return ensure_utc(expires_at) + timedelta(days=purge_grace_days)


def purge_notify_at(
    expires_at: datetime,
    purge_grace_days: int,
    purge_notify_days: int,
) -> datetime:
    purge_at = purge_cutoff(expires_at, purge_grace_days)
    if purge_notify_days <= 0:
        return purge_at
    notify_at = purge_at - timedelta(days=purge_notify_days)
    expires_utc = ensure_utc(expires_at)
    if notify_at < expires_utc:
        return expires_utc
    return notify_at


def is_deletion_pending(
    *,
    is_expired: bool,
    expires_at: datetime,
    purge_grace_days: int,
    now: datetime | None = None,
) -> bool:
    if purge_grace_days <= 0:
        return False
    now = now or _utcnow()
    if not is_past_expiry(is_expired=is_expired, expires_at=expires_at, now=now):
        return False
    return ensure_utc(now) < purge_cutoff(expires_at, purge_grace_days)


def transfer_deletion_pending(transfer: Transfer, purge_grace_days: int, now: datetime | None = None) -> bool:
    return is_deletion_pending(
        is_expired=transfer.is_expired,
        expires_at=transfer.expires_at,
        purge_grace_days=purge_grace_days,
        now=now,
    )


def file_request_deletion_pending(req: FileRequest, purge_grace_days: int, now: datetime | None = None) -> bool:
    return is_deletion_pending(
        is_expired=req.is_expired,
        expires_at=req.expires_at,
        purge_grace_days=purge_grace_days,
        now=now,
    )


def reset_expiry_notifications(
    item: Transfer | FileRequest,
    new_expires_at: datetime,
    now: datetime | None = None,
) -> None:
    now = now or _utcnow()
    if ensure_utc(new_expires_at) > ensure_utc(now):
        item.expired_unused_notified = False
        item.purge_warned = False


EXPIRY_NOTICE_WITHIN_DAYS = 7


def is_expiry_pending(
    *,
    is_expired: bool,
    expires_at: datetime,
    now: datetime | None = None,
) -> bool:
    now = now or _utcnow()
    if is_past_expiry(is_expired=is_expired, expires_at=expires_at, now=now):
        return False
    remaining = ensure_utc(expires_at) - ensure_utc(now)
    return timedelta(0) < remaining < timedelta(days=EXPIRY_NOTICE_WITHIN_DAYS)


def transfer_expiry_pending(transfer: Transfer, now: datetime | None = None) -> bool:
    return is_expiry_pending(
        is_expired=transfer.is_expired,
        expires_at=transfer.expires_at,
        now=now,
    )


def file_request_expiry_pending(req: FileRequest, now: datetime | None = None) -> bool:
    return is_expiry_pending(
        is_expired=req.is_expired,
        expires_at=req.expires_at,
        now=now,
    )


def format_duration_remaining(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    if total_seconds < 60:
        return _("less than a minute")

    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(ngettext("%(count)s day", "%(count)s days", days) % {"count": days})
    if hours:
        parts.append(ngettext("%(count)s hour", "%(count)s hours", hours) % {"count": hours})
    if not days and minutes:
        parts.append(ngettext("%(count)s minute", "%(count)s minutes", minutes) % {"count": minutes})
    return ", ".join(parts[:2]) if parts else _("less than a minute")


def build_share_timeline_notice(
    *,
    is_expired: bool,
    expires_at: datetime,
    purge_grace_days: int,
    now: datetime | None = None,
) -> dict[str, str] | None:
    now = now or _utcnow()
    if is_past_expiry(is_expired=is_expired, expires_at=expires_at, now=now):
        if not is_deletion_pending(
            is_expired=is_expired,
            expires_at=expires_at,
            purge_grace_days=purge_grace_days,
            now=now,
        ):
            return None
        purge_at = purge_cutoff(expires_at, purge_grace_days)
        remaining = purge_at - ensure_utc(now)
        return {
            "variant": "deletion",
            "title": _("Deletion pending"),
            "time_left": format_duration_remaining(remaining),
            "deadline": format_datetime_with_tz(purge_at),
        }

    remaining = ensure_utc(expires_at) - ensure_utc(now)
    if remaining <= timedelta(0):
        return None
    if remaining >= timedelta(days=EXPIRY_NOTICE_WITHIN_DAYS):
        return None
    return {
        "variant": "expiry",
        "title": _("Expiration pending"),
        "time_left": format_duration_remaining(remaining),
        "deadline": format_datetime_with_tz(expires_at),
    }


def share_timeline_notice(
    item: Transfer | FileRequest,
    purge_grace_days: int,
    now: datetime | None = None,
) -> dict[str, str] | None:
    return build_share_timeline_notice(
        is_expired=item.is_expired,
        expires_at=item.expires_at,
        purge_grace_days=purge_grace_days,
        now=now,
    )
