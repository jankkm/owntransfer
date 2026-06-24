from __future__ import annotations

import uuid

from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.auth.sessions import SESSION_COOKIE, load_session_token
from app.database import async_session
from app.i18n import activate, normalize_locale, resolve_locale
from app.models import User


async def _saved_locale_for_request(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    data = load_session_token(token)
    if not data:
        return None
    user_id = uuid.UUID(data["uid"])
    async with async_session() as db:
        result = await db.execute(
            select(User.locale).where(User.id == user_id, User.is_active.is_(True))
        )
        locale = result.scalar_one_or_none()
    return normalize_locale(locale)


class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        saved_locale = await _saved_locale_for_request(request)
        locale = activate(resolve_locale(request, saved_locale=saved_locale))
        request.state.locale = locale
        return await call_next(request)
