from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.i18n import _
from app.services.branding import DEFAULT_LOGO_URL
from app.services.markdown_render import render_markdown
from app.services.settings import get_app_settings
from app.templating import branding_context, templates

router = APIRouter(tags=["branding"])


@router.get("/branding/logo")
async def serve_logo(db: AsyncSession = Depends(get_db)) -> Response:
    app_settings = await get_app_settings(db)
    if not app_settings.logo_data:
        return Response(status_code=307, headers={"Location": DEFAULT_LOGO_URL})

    return Response(
        content=app_settings.logo_data,
        media_type=app_settings.logo_content_type or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=300",
            # Defense in depth for SVG logos: even if active content survives
            # sanitization, the sandbox + locked-down CSP prevents script
            # execution when the asset is opened directly, and nosniff stops
            # content-type confusion.
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/impressum", response_class=HTMLResponse)
async def impressum_page(request: Request, db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    markdown = (app_settings.impressum_markdown or "").strip()
    if not app_settings.impressum_enabled or not markdown:
        return RedirectResponse("/", status_code=303)

    ctx = branding_context(app_settings)
    ctx["page_title"] = _("Impressum")
    ctx["content_html"] = render_markdown(markdown)
    return templates.TemplateResponse(request, "legal_page.html", ctx)


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy_page(request: Request, db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    markdown = (app_settings.privacy_policy_markdown or "").strip()
    if not app_settings.privacy_policy_enabled or not markdown:
        return RedirectResponse("/", status_code=303)

    ctx = branding_context(app_settings)
    ctx["page_title"] = _("Privacy policy")
    ctx["content_html"] = render_markdown(markdown)
    return templates.TemplateResponse(request, "legal_page.html", ctx)
