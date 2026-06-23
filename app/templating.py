from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings
from app.models import AppSettings
from app.services.branding import has_custom_logo, logo_url, favicon_type
from app.services.datetime_display import (
    default_expiry_date,
    ensure_expiry_within_limit,
    format_date,
    format_datetime,
    format_datetime_with_tz,
    max_expiry_date,
    today_date,
)
from app.auth.users import uses_local_auth, user_initials
from app.services import share_status
from app.services.download_limits import format_download_limit, format_download_limit_short
from app.services.share_lifecycle import (
    file_request_deletion_pending,
    share_timeline_notice,
    transfer_deletion_pending,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["format_date"] = format_date
templates.env.filters["format_datetime_with_tz"] = format_datetime_with_tz
templates.env.globals["user_uses_local_auth"] = uses_local_auth
templates.env.globals["transfer_is_active"] = share_status.transfer_is_active
templates.env.globals["transfer_can_toggle"] = share_status.transfer_can_toggle
templates.env.globals["transfer_inactive_reason"] = share_status.transfer_inactive_reason
templates.env.globals["file_request_is_active"] = share_status.file_request_is_active
templates.env.globals["file_request_can_toggle"] = share_status.file_request_can_toggle
templates.env.globals["file_request_inactive_reason"] = share_status.file_request_inactive_reason
templates.env.globals["transfer_deletion_pending"] = transfer_deletion_pending
templates.env.globals["file_request_deletion_pending"] = file_request_deletion_pending
templates.env.globals["share_timeline_notice"] = share_timeline_notice
templates.env.filters["format_download_limit"] = format_download_limit
templates.env.filters["format_download_limit_short"] = format_download_limit_short
templates.env.filters["user_initials"] = user_initials


def branding_context(app_settings: AppSettings) -> dict:
    return {
        "app_name": app_settings.app_name,
        "primary_color": app_settings.primary_color,
        "accent_color": app_settings.accent_color,
        "logo_path": logo_url(app_settings),
        "favicon_type": favicon_type(app_settings),
        "has_custom_logo": has_custom_logo(app_settings),
        "base_url": settings.base_url,
        "max_file_size_mb": app_settings.max_file_size_bytes // (1024 * 1024),
        "default_expiry_days": app_settings.default_expiry_days,
        "default_expiry_date": default_expiry_date(app_settings.default_expiry_days),
        "max_expiry_days": app_settings.max_share_expiry_days,
        "max_expiry_date": max_expiry_date(app_settings.max_share_expiry_days),
        "today_date": today_date(),
        "max_downloads_default": app_settings.max_downloads_default,
        "purge_grace_days": app_settings.purge_grace_days,
        "allow_user_share_emails": app_settings.allow_user_share_emails,
        "impressum_enabled": app_settings.impressum_enabled
        and bool((app_settings.impressum_markdown or "").strip()),
        "privacy_policy_enabled": app_settings.privacy_policy_enabled
        and bool((app_settings.privacy_policy_markdown or "").strip()),
        "display_timezone": settings.display_timezone,
    }
