from __future__ import annotations

import uuid
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

SESSION_COOKIE = "owntransfer_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14  # 14 days


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="session")


def create_session_token(user_id: uuid.UUID, is_admin: bool) -> str:
    return _serializer().dumps({"uid": str(user_id), "admin": is_admin})


def load_session_token(token: str) -> dict[str, Any] | None:
    try:
        return _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
