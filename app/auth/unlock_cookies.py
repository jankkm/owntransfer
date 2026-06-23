from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

TRANSFER_UNLOCK_PREFIX = "unlock_d_"
REQUEST_UNLOCK_PREFIX = "unlock_r_"
UNLOCK_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="unlock")


def _sign(token: str) -> str:
    # Bind the signature to the specific share token so a value minted for one
    # link cannot be replayed against another.
    return _serializer().dumps({"t": token})


def _is_valid(value: str | None, token: str) -> bool:
    if not value:
        return False
    try:
        payload = _serializer().loads(value, max_age=UNLOCK_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(payload, dict) and payload.get("t") == token


def transfer_unlock_cookie(token: str) -> str:
    return f"{TRANSFER_UNLOCK_PREFIX}{token}"


def request_unlock_cookie(token: str) -> str:
    return f"{REQUEST_UNLOCK_PREFIX}{token}"


def is_transfer_unlocked(request: Request, token: str, *, password_required: bool) -> bool:
    if not password_required:
        return True
    return _is_valid(request.cookies.get(transfer_unlock_cookie(token)), token)


def is_request_unlocked(request: Request, token: str, *, password_required: bool) -> bool:
    if not password_required:
        return True
    return _is_valid(request.cookies.get(request_unlock_cookie(token)), token)


def _set_unlock(response: Response, cookie_name: str, token: str) -> None:
    response.set_cookie(
        cookie_name,
        _sign(token),
        httponly=True,
        samesite="lax",
        secure=settings.cookies_secure,
        path="/",
        max_age=UNLOCK_MAX_AGE,
    )


def set_transfer_unlock(response: Response, token: str) -> None:
    _set_unlock(response, transfer_unlock_cookie(token), token)


def set_request_unlock(response: Response, token: str) -> None:
    _set_unlock(response, request_unlock_cookie(token), token)
