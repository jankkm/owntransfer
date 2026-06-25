from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.database import get_db
from app.http.client_ip import get_client_ip
from app.i18n import _
from app.limiter import limiter
from app.models import User
from app.services.audit import log_audit
from app.services.settings import get_app_settings
from app.services.setup_token import verify_setup_token
from app.templating import branding_context, templates

router = APIRouter()


def _setup_context(app_settings, *, error: str | None = None) -> dict:
    ctx = branding_context(app_settings)
    if error:
        ctx["error"] = error
    return ctx


@router.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request, db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    if app_settings.setup_completed:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "setup.html", _setup_context(app_settings))


@router.post("/setup")
@limiter.limit("10/minute")
async def setup_post(
    request: Request,
    setup_token: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    app_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    app_settings = await get_app_settings(db)
    if app_settings.setup_completed:
        return RedirectResponse("/", status_code=303)

    if not verify_setup_token(request.app, setup_token):
        return templates.TemplateResponse(
            request,
            "setup.html",
            _setup_context(app_settings, error=_("Invalid setup token")),
            status_code=400,
        )

    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "setup.html",
            _setup_context(app_settings, error=_("Password must be at least 8 characters")),
            status_code=400,
        )

    existing = await db.execute(select(User).where(User.email == email.lower()))
    if existing.scalar_one_or_none():
        return templates.TemplateResponse(
            request,
            "setup.html",
            _setup_context(app_settings, error=_("User already exists")),
            status_code=400,
        )

    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
        is_admin=True,
        is_active=True,
    )
    db.add(user)

    app_settings.app_name = app_name
    app_settings.setup_completed = True
    await db.commit()

    await log_audit(
        db,
        action="setup.completed",
        resource_type="system",
        actor_id=user.id,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse("/auth/login", status_code=303)
