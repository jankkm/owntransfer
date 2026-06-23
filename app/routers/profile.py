from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.auth.passwords import hash_password, verify_password
from app.auth.totp import generate_totp_secret, totp_qr_data_uri, verify_totp
from app.auth.users import uses_local_auth
from app.database import get_db
from app.http.client_ip import get_client_ip
from app.models import User
from app.services.audit import log_audit
from app.services.settings import get_app_settings
from app.templating import branding_context, templates

router = APIRouter(prefix="/auth", tags=["profile"])


def _profile_ctx(request: Request, app_settings, user: User, **extra) -> dict:
    ctx = branding_context(app_settings)
    ctx.update({"user": user, "uses_local_auth": uses_local_auth(user), **extra})
    if request.query_params.get("password_changed"):
        ctx["success"] = "Password updated."
    if request.query_params.get("totp_enabled"):
        ctx["success"] = "Two-factor authentication enabled."
    if request.query_params.get("totp_disabled"):
        ctx["success"] = "Two-factor authentication disabled."
    error = request.query_params.get("error")
    if error:
        ctx["error"] = error.replace("+", " ")
    return ctx


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    totp_pending = bool(user.totp_secret and not user.totp_enabled)
    ctx = _profile_ctx(
        request,
        app_settings,
        user,
        totp_pending=totp_pending,
        totp_qr=None,
    )
    if totp_pending and user.totp_secret:
        ctx["totp_qr"] = totp_qr_data_uri(
            email=user.email,
            secret=user.totp_secret,
            issuer=app_settings.app_name,
        )
    return templates.TemplateResponse(request, "profile.html", ctx)


@router.post("/profile/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not uses_local_auth(user):
        raise HTTPException(status_code=403, detail="OAuth accounts cannot change password here")

    if len(new_password) < 8:
        return RedirectResponse("/auth/profile?error=Password+must+be+at+least+8+characters", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/auth/profile?error=New+passwords+do+not+match", status_code=303)
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse("/auth/profile?error=Current+password+is+incorrect", status_code=303)

    user.password_hash = hash_password(new_password)
    await db.commit()
    await log_audit(
        db,
        action="user.password_changed",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=user.id,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse("/auth/profile?password_changed=1", status_code=303)


@router.post("/profile/totp/start")
async def start_totp_setup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not uses_local_auth(user):
        raise HTTPException(status_code=403, detail="OAuth accounts cannot enable 2FA here")
    if user.totp_enabled:
        return RedirectResponse("/auth/profile", status_code=303)

    user.totp_secret = generate_totp_secret()
    user.totp_enabled = False
    await db.commit()
    return RedirectResponse("/auth/profile", status_code=303)


@router.post("/profile/totp/confirm")
async def confirm_totp_setup(
    request: Request,
    totp_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not uses_local_auth(user) or not user.totp_secret:
        raise HTTPException(status_code=403, detail="2FA setup not available")

    if not verify_totp(user.totp_secret, totp_code):
        return RedirectResponse("/auth/profile?error=Invalid+authentication+code", status_code=303)

    user.totp_enabled = True
    await db.commit()
    await log_audit(
        db,
        action="user.totp_enabled",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=user.id,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse("/auth/profile?totp_enabled=1", status_code=303)


@router.post("/profile/totp/disable")
async def disable_totp(
    request: Request,
    current_password: str = Form(...),
    totp_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not uses_local_auth(user) or not user.totp_enabled:
        raise HTTPException(status_code=403, detail="2FA is not enabled")

    if not verify_password(current_password, user.password_hash):
        return RedirectResponse("/auth/profile?error=Current+password+is+incorrect", status_code=303)
    if not verify_totp(user.totp_secret, totp_code):
        return RedirectResponse("/auth/profile?error=Invalid+authentication+code", status_code=303)

    user.totp_secret = None
    user.totp_enabled = False
    await db.commit()
    await log_audit(
        db,
        action="user.totp_disabled",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=user.id,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse("/auth/profile?totp_disabled=1", status_code=303)


@router.post("/profile/totp/cancel")
async def cancel_totp_setup(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.totp_enabled:
        return RedirectResponse("/auth/profile", status_code=303)
    user.totp_secret = None
    await db.commit()
    return RedirectResponse("/auth/profile", status_code=303)
