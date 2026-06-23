from __future__ import annotations

import uuid

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_optional
from app.auth.passwords import verify_password
from app.auth.sessions import SESSION_COOKIE, SESSION_MAX_AGE, create_session_token
from app.auth.totp import verify_totp
from app.config.oauth_providers import get_oauth_providers
from app.database import get_db
from app.models import User
from app.http.client_ip import get_client_ip
from app.services.audit import log_audit
from app.services.security_log import log_invalid_login
from app.services.settings import get_app_settings
from app.templating import branding_context, templates

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
for provider in get_oauth_providers():
    oauth.register(
        name=provider.key,
        client_id=provider.client_id,
        client_secret=provider.client_secret,
        server_metadata_url=provider.server_metadata_url,
        client_kwargs={"scope": provider.scope},
    )


def _login_response(request: Request, user: User) -> RedirectResponse:
    token = create_session_token(user.id, user.is_admin)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    tab: str = "oauth",
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    app_settings = await get_app_settings(db)
    providers = get_oauth_providers()
    if not providers and app_settings.allow_local_login:
        tab = "local"
    elif not app_settings.allow_local_login and providers:
        tab = "oauth"
    ctx = branding_context(app_settings)
    ctx.update(
        {
            "tab": tab,
            "oauth_providers": providers,
            "allow_local_login": app_settings.allow_local_login,
        }
    )
    return templates.TemplateResponse(request, "login.html", ctx)


@router.post("/login/local")
async def login_local(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    app_settings = await get_app_settings(db)
    if not app_settings.allow_local_login:
        raise HTTPException(status_code=403, detail="Local login disabled")

    result = await db.execute(select(User).where(User.email == email.lower(), User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        log_invalid_login(request, email)
        ctx = branding_context(app_settings)
        ctx.update({"tab": "local", "error": "Invalid credentials", "oauth_providers": get_oauth_providers(), "allow_local_login": app_settings.allow_local_login})
        return templates.TemplateResponse(request, "login.html", ctx, status_code=401)

    if user.totp_enabled:
        request.session["pending_totp_user_id"] = str(user.id)
        return RedirectResponse("/auth/login/totp", status_code=303)

    await log_audit(db, action="user.login", resource_type="user", actor_id=user.id, ip_address=get_client_ip(request))
    return _login_response(request, user)


@router.get("/login/totp", response_class=HTMLResponse)
async def login_totp_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    if not request.session.get("pending_totp_user_id"):
        return RedirectResponse("/auth/login", status_code=303)

    app_settings = await get_app_settings(db)
    ctx = branding_context(app_settings)
    ctx.update({"tab": "local", "allow_local_login": app_settings.allow_local_login})
    if request.query_params.get("error"):
        ctx["error"] = request.query_params.get("error", "").replace("+", " ")
    return templates.TemplateResponse(request, "login_totp.html", ctx)


@router.post("/login/totp")
async def login_totp_verify(
    request: Request,
    totp_code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    pending_id = request.session.get("pending_totp_user_id")
    if not pending_id:
        return RedirectResponse("/auth/login", status_code=303)

    try:
        user_id = uuid.UUID(pending_id)
    except ValueError:
        request.session.pop("pending_totp_user_id", None)
        return RedirectResponse("/auth/login", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user or not user.totp_enabled or not verify_totp(user.totp_secret, totp_code):
        return RedirectResponse("/auth/login/totp?error=Invalid+authentication+code", status_code=303)

    request.session.pop("pending_totp_user_id", None)
    await log_audit(db, action="user.login", resource_type="user", actor_id=user.id, ip_address=get_client_ip(request))
    return _login_response(request, user)


@router.get("/oauth/{provider}")
async def oauth_start(provider: str, request: Request):
    client = oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=404, detail="Provider not configured")
    redirect_uri = request.url_for("oauth_callback", provider=provider)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    client = oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=404, detail="Provider not configured")
    token = await client.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await client.parse_id_token(request, token)

    email = (userinfo.get("email") or userinfo.get("preferred_username") or "").lower()
    sub = userinfo.get("sub")
    if not email or not sub:
        raise HTTPException(status_code=400, detail="OAuth provider did not return email")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=email, oauth_provider=provider, oauth_sub=sub, is_admin=False, is_active=True)
        db.add(user)
    else:
        user.oauth_provider = provider
        user.oauth_sub = sub
    await db.commit()
    await db.refresh(user)

    await log_audit(db, action="user.oauth_login", resource_type="user", actor_id=user.id, ip_address=get_client_ip(request), metadata={"provider": provider})
    return _login_response(request, user)


@router.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
