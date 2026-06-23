from __future__ import annotations

from app.i18n import _
from app.models import Transfer


def downloads_unlimited(max_downloads: int) -> bool:
    return max_downloads <= 0


def transfer_download_limit_reached(transfer: Transfer) -> bool:
    if downloads_unlimited(transfer.max_downloads):
        return False
    return transfer.download_count >= transfer.max_downloads


def format_download_limit(download_count: int, max_downloads: int) -> str:
    if downloads_unlimited(max_downloads):
        return _("%(count)s / ∞ downloads") % {"count": download_count}
    return _("%(count)s/%(max)s downloads") % {"count": download_count, "max": max_downloads}


def format_download_limit_short(download_count: int, max_downloads: int) -> str:
    if downloads_unlimited(max_downloads):
        return _("%(count)s / ∞") % {"count": download_count}
    return _("%(count)s/%(max)s") % {"count": download_count, "max": max_downloads}
