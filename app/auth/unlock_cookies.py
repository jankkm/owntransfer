from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

TRANSFER_UNLOCK_PREFIX = "unlock_d_"
REQUEST_UNLOCK_PREFIX = "unlock_r_"
UNLOCK_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def transfer_unlock_cookie(token: str) -> str:
    return f"{TRANSFER_UNLOCK_PREFIX}{token}"


def request_unlock_cookie(token: str) -> str:
    return f"{REQUEST_UNLOCK_PREFIX}{token}"


def is_transfer_unlocked(request: Request, token: str, *, password_required: bool) -> bool:
    if not password_required:
        return True
    return request.cookies.get(transfer_unlock_cookie(token)) == "1"


def is_request_unlocked(request: Request, token: str, *, password_required: bool) -> bool:
    if not password_required:
        return True
    return request.cookies.get(request_unlock_cookie(token)) == "1"


def set_transfer_unlock(response: Response, token: str) -> None:
    response.set_cookie(
        transfer_unlock_cookie(token),
        "1",
        httponly=True,
        samesite="lax",
        path="/",
        max_age=UNLOCK_MAX_AGE,
    )


def set_request_unlock(response: Response, token: str) -> None:
    response.set_cookie(
        request_unlock_cookie(token),
        "1",
        httponly=True,
        samesite="lax",
        path="/",
        max_age=UNLOCK_MAX_AGE,
    )
