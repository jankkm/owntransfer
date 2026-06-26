from __future__ import annotations

import uuid

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_optional
from app.auth.login_redirect import (
    DASHBOARD_URL,
    clear_login_next,
    get_effective_login_next,
    get_login_target,
    store_login_next,
)
from app.auth.passwords import verify_password
from app.auth.users import oauth_display_name
from app.auth.sessions import SESSION_COOKIE, SESSION_MAX_AGE, create_session_token
from app.config import settings
from app.auth.totp import verify_totp
from app.config.oauth_providers import get_oauth_providers
from app.database import get_db
from app.i18n import LOCALE_COOKIE, LOCALE_COOKIE_MAX_AGE, _, normalize_locale
from app.models import User
from app.http.client_ip import get_client_ip
from app.http.external_url import external_url
from app.limiter import limiter
from app.services.audit import log_audit
from app.services.security_log import log_invalid_login, log_invalid_totp
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
    target = get_login_target(request)
    clear_login_next(request)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.cookies_secure,
    )
    saved_locale = normalize_locale(user.locale)
    if saved_locale and not request.cookies.get(LOCALE_COOKIE):
        response.set_cookie(
            LOCALE_COOKIE,
            saved_locale,
            max_age=LOCALE_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=settings.cookies_secure,
        )
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    tab: str = "oauth",
    next: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    login_next = get_effective_login_next(request, next)
    store_login_next(request, login_next)
    if user:
        return RedirectResponse(DASHBOARD_URL, status_code=303)
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
            "login_next": login_next,
        }
    )
    return templates.TemplateResponse(request, "login.html", ctx)


@router.post("/login/local")
@limiter.limit("10/minute")
async def login_local(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    app_settings = await get_app_settings(db)
    if not app_settings.allow_local_login:
        raise HTTPException(status_code=403, detail=_("Local login disabled"))

    result = await db.execute(select(User).where(User.email == email.lower(), User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        log_invalid_login(request, email)
        ctx = branding_context(app_settings)
        ctx.update({"tab": "local", "error": _("Invalid credentials"), "oauth_providers": get_oauth_providers(), "allow_local_login": app_settings.allow_local_login})
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
        return RedirectResponse(DASHBOARD_URL, status_code=303)
    if not request.session.get("pending_totp_user_id"):
        return RedirectResponse("/auth/login", status_code=303)

    app_settings = await get_app_settings(db)
    ctx = branding_context(app_settings)
    ctx.update({"tab": "local", "allow_local_login": app_settings.allow_local_login})
    if request.query_params.get("error"):
        ctx["error"] = request.query_params.get("error", "").replace("+", " ")
    return templates.TemplateResponse(request, "login_totp.html", ctx)


@router.post("/login/totp")
@limiter.limit("10/minute")
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
        log_invalid_totp(request, user.email if user else None)
        return RedirectResponse("/auth/login/totp?error=" + _("Invalid authentication code").replace(" ", "+"), status_code=303)

    request.session.pop("pending_totp_user_id", None)
    await log_audit(db, action="user.login", resource_type="user", actor_id=user.id, ip_address=get_client_ip(request))
    return _login_response(request, user)


@router.get("/oauth/{provider}")
async def oauth_start(provider: str, request: Request):
    client = oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=404, detail=_("Provider not configured"))
    redirect_uri = external_url(f"/auth/oauth/{provider}/callback")
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/oauth/{provider}/callback", name="oauth_callback")
async def oauth_callback(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    client = oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=404, detail=_("Provider not configured"))
    token = await client.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = await client.parse_id_token(request, token)

    display_name = oauth_display_name(userinfo)
    if not display_name:
        try:
            fetched = await client.userinfo(token=token)
            if isinstance(fetched, dict):
                userinfo = {**userinfo, **fetched}
                display_name = oauth_display_name(userinfo)
        except Exception:
            pass

    # Only trust the verified `email` claim. `preferred_username` is not a
    # verified email and must not be used to match accounts.
    email = (userinfo.get("email") or "").strip().lower()
    sub = userinfo.get("sub")
    if not email or not sub:
        raise HTTPException(status_code=400, detail=_("OAuth provider did not return an email address"))

    # Reject only when the provider explicitly says the email is unverified.
    # (Providers such as Entra omit the claim for org-managed accounts.)
    email_verified = userinfo.get("email_verified")
    if email_verified is False or str(email_verified).strip().lower() == "false":
        raise HTTPException(status_code=403, detail=_("Your email address is not verified with the identity provider"))

    # Match on the stable provider + subject identifier first.
    result = await db.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_sub == sub)
    )
    user = result.scalar_one_or_none()
    if user:
        if display_name:
            user.display_name = display_name
        if user.email != email:
            user.email = email
    else:
        result = await db.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            # Never silently link an OAuth identity to a pre-existing local
            # (password) account or an account owned via a different provider —
            # that would allow account takeover by anyone who can assert the email.
            if existing.oauth_provider != provider:
                raise HTTPException(
                    status_code=403,
                    detail=_(
                        "An account with this email already exists. Sign in with your "
                        "existing method, or ask an administrator to link your account."
                    ),
                )
            user = existing
            user.oauth_sub = sub
            if display_name:
                user.display_name = display_name
        else:
            user = User(
                email=email,
                oauth_provider=provider,
                oauth_sub=sub,
                display_name=display_name,
                is_admin=False,
                is_active=True,
            )
            db.add(user)
    await db.commit()
    await db.refresh(user)

    await log_audit(db, action="user.oauth_login", resource_type="user", actor_id=user.id, ip_address=get_client_ip(request), metadata={"provider": provider})
    return _login_response(request, user)


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, samesite="lax", secure=settings.cookies_secure)
    return response
