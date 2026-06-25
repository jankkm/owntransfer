from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_optional
from app.http.safe_redirect import safe_redirect_target
from app.config import settings
from app.database import get_db
from app.i18n import LOCALE_COOKIE, LOCALE_COOKIE_MAX_AGE, SUPPORTED_LOCALES, normalize_locale
from app.models import User

router = APIRouter(tags=["locale"])


@router.post("/locale")
async def set_locale(
    request: Request,
    locale: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> RedirectResponse:
    resolved = normalize_locale(locale)
    if resolved not in SUPPORTED_LOCALES:
        resolved = "en"
    if user is not None:
        user.locale = resolved
        await db.commit()
    referer = safe_redirect_target(request)
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
