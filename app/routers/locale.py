from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.i18n import LOCALE_COOKIE, LOCALE_COOKIE_MAX_AGE, SUPPORTED_LOCALES, normalize_locale

router = APIRouter(tags=["locale"])


@router.post("/locale")
async def set_locale(request: Request, locale: str = Form(...)) -> RedirectResponse:
    resolved = normalize_locale(locale)
    if resolved not in SUPPORTED_LOCALES:
        resolved = "en"
    referer = request.headers.get("referer") or "/"
    response = RedirectResponse(referer, status_code=303)
    response.set_cookie(
        LOCALE_COOKIE,
        resolved,
        max_age=LOCALE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookies_secure,
    )
    return response
