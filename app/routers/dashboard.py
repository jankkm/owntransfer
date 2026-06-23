from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, get_current_user_optional
from app.database import get_db
from app.models import User
from app.services.dashboard import (
    get_user_shares_summary,
    list_recent_user_requests,
    list_recent_user_transfers,
)
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
    tab: str = "transfers",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if tab not in {"transfers", "requests"}:
        tab = "transfers"
    app_settings = await get_app_settings(db)
    summary = await get_user_shares_summary(db, user.id)
    recent_transfers = await list_recent_user_transfers(db, user.id)
    recent_requests = await list_recent_user_requests(db, user.id)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "tab": tab,
        "summary": summary,
        "recent_transfers": recent_transfers,
        "recent_requests": recent_requests,
        "now": datetime.now(timezone.utc),
    })
    return templates.TemplateResponse(request, "dashboard.html", ctx)
