from __future__ import annotations

import logging
import re

from starlette.requests import Request

from app.http.client_ip import get_client_ip
from app.logging_config import SECURITY_LOGGER_NAME

_logger = logging.getLogger(SECURITY_LOGGER_NAME)
_FIELD_RE = re.compile(r"[\s=]")


def _escape_field(value: str) -> str:
    cleaned = _FIELD_RE.sub("_", value.strip())
    return cleaned or "-"


def _log_event(event: str, request: Request | None = None, **fields: str) -> None:
    parts = [f"event={event}"]
    if request is not None:
        ip = get_client_ip(request)
        if ip:
            parts.append(f"ip={_escape_field(ip)}")
        parts.append(f"method={_escape_field(request.method)}")
    for key in sorted(fields):
        value = fields[key]
        if value:
            parts.append(f"{key}={_escape_field(value)}")
    _logger.warning(" ".join(parts))


def log_invalid_login(request: Request, email: str) -> None:
    _log_event("invalid_login", request, email=email.lower())


def log_invalid_totp(request: Request, email: str | None = None) -> None:
    _log_event("invalid_totp", request, email=(email or "").lower())


def log_invalid_transfer_link(request: Request) -> None:
    _log_event("invalid_transfer_link", request)


def log_invalid_request_link(request: Request) -> None:
    _log_event("invalid_request_link", request)


def log_invalid_unlock(request: Request, kind: str) -> None:
    """kind is 'transfer' or 'request' — a wrong password on a public share."""
    _log_event("invalid_unlock", request, kind=kind)
