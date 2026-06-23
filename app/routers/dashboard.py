from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, get_current_user_optional
from app.database import get_db
from app.models import User
from app.services.settings import get_app_settings
from app.templating import branding_context, templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(
    user: User | None = Depends(get_current_user_optional),
):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/auth/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    ctx = branding_context(app_settings)
    ctx["user"] = user
    return templates.TemplateResponse(request, "dashboard.html", ctx)
