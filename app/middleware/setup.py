from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.database import async_session
from app.services.settings import is_setup_complete

SETUP_ALLOWLIST = {"/setup", "/health", "/ready", "/static", "/branding"}


class SetupMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path == allowed or path.startswith(allowed + "/") for allowed in SETUP_ALLOWLIST):
            return await call_next(request)

        async with async_session() as db:
            if not await is_setup_complete(db):
                return RedirectResponse("/setup", status_code=303)

        return await call_next(request)
