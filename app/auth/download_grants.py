from __future__ import annotations

DOWNLOAD_GRANT_PREFIX = "transfer_grant_"
DOWNLOAD_COUNTED_PREFIX = "transfer_counted_"
DOWNLOAD_NOTIFY_PREFIX = "transfer_notify_"


def _grant_key(public_token: str) -> str:
    return f"{DOWNLOAD_GRANT_PREFIX}{public_token}"


def _counted_key(public_token: str) -> str:
    return f"{DOWNLOAD_COUNTED_PREFIX}{public_token}"


def _notify_key(public_token: str) -> str:
    return f"{DOWNLOAD_NOTIFY_PREFIX}{public_token}"


def grant_transfer_download(session: dict, public_token: str) -> None:
    session[_grant_key(public_token)] = True


def has_transfer_download_grant(session: dict, public_token: str) -> bool:
    return bool(session.get(_grant_key(public_token)))


def mark_transfer_download_counted(session: dict, public_token: str) -> bool:
    """Return True if this is the first download in the current browser session."""
    key = _counted_key(public_token)
    if session.get(key):
        return False
    session[key] = True
    return True


def mark_transfer_download_notified(session: dict, public_token: str) -> bool:
    """Return True if the creator has not yet been notified this browser session."""
    key = _notify_key(public_token)
    if session.get(key):
        return False
    session[key] = True
    return True
