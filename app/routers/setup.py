from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.database import get_db
from app.models import AppSettings, User
from app.http.client_ip import get_client_ip
from app.services.audit import log_audit
from app.services.settings import get_app_settings
from app.templating import branding_context, templates

router = APIRouter()


@router.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request, db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    if app_settings.setup_completed:
        return RedirectResponse("/", status_code=303)
    ctx = branding_context(app_settings)
    return templates.TemplateResponse(request, "setup.html", ctx)


@router.post("/setup")
async def setup_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    app_name: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    app_settings = await get_app_settings(db)
    if app_settings.setup_completed:
        return RedirectResponse("/", status_code=303)

    existing = await db.execute(select(User).where(User.email == email.lower()))
    if existing.scalar_one_or_none():
        ctx = branding_context(app_settings)
        ctx["error"] = "User already exists"
        return templates.TemplateResponse(request, "setup.html", ctx, status_code=400)

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
