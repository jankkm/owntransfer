from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_admin
from app.auth.passwords import hash_password
from app.database import get_db
from app.i18n import _
from app.http.client_ip import get_client_ip
from app.models import AuditLog, User
from app.services.admin_overview import (
    get_shares_summary,
    get_user_resource_counts,
    list_all_file_requests,
    list_all_transfers,
)
from app.services.audit import log_audit
from app.services.branding import apply_logo_upload, clear_logo
from app.services.datetime_display import parse_expiry_date
from app.services.email_templates import (
    SUBJECT_FIELD_MAP,
    TEMPLATE_FIELD_MAP,
    TEMPLATE_KEYS,
    TEMPLATE_VARIABLES,
    subjects_for_admin,
    templates_for_admin,
)
from app.services.file_request import (
    delete_file_request,
    delete_request_upload_file,
    get_file_request_for_admin,
    regenerate_file_request_link,
    update_file_request,
)
from app.services.email import send_smtp_test_email
from app.services.settings import get_app_settings
from app.services.transfer import (
    add_transfer_file,
    delete_transfer,
    delete_transfer_file,
    get_transfer_for_admin,
    regenerate_transfer_link,
    update_transfer,
)
from app.templating import branding_context, templates

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


def _shares_url(*, tab: str = "transfers", user: str = "", **extra: str) -> str:
    parts = [f"tab={tab}"]
    if user:
        parts.append(f"user={user}")
    for key, value in extra.items():
        if value:
            parts.append(f"{key}={value}")
    return "/admin/shares?" + "&".join(parts)


@router.get("", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    users = list(
        (await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.created_at))).scalars().all()
    )
    user_counts = await get_user_resource_counts(db)
    audit_logs = list(
        (await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(50))).scalars().all()
    )
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "app_settings": app_settings,
        "users": users,
        "user_counts": user_counts,
        "audit_logs": audit_logs,
        "active": "settings",
    })
    if request.query_params.get("branding_saved"):
        ctx["success"] = _("Branding saved.")
    if request.query_params.get("uploads_saved"):
        ctx["success"] = _("File upload settings saved.")
    if request.query_params.get("shares_saved"):
        ctx["success"] = _("Share settings saved.")
    if request.query_params.get("access_saved"):
        ctx["success"] = _("Access settings saved.")
    if request.query_params.get("impressum_saved") or request.query_params.get("legal_saved"):
        ctx["success"] = _("Legal pages saved.")
    if request.query_params.get("user_added"):
        ctx["success"] = _("User added.")
    if request.query_params.get("user_deleted"):
        ctx["success"] = _("User deleted.")
    if request.query_params.get("user_promoted"):
        ctx["success"] = _("User promoted to admin.")
    if request.query_params.get("user_demoted"):
        ctx["success"] = _("User demoted from admin.")
    if request.query_params.get("user_password_set"):
        ctx["success"] = _("Password updated.")
    error = request.query_params.get("error")
    if error:
        ctx["error"] = error
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.get("/shares", response_class=HTMLResponse)
async def admin_shares(
    request: Request,
    tab: str = "transfers",
    user: str = "",
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    filter_user_id: uuid.UUID | None = None
    if user:
        try:
            filter_user_id = uuid.UUID(user)
        except ValueError:
            filter_user_id = None

    if tab not in ("transfers", "requests"):
        tab = "transfers"

    filter_users = list(
        (await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.email))).scalars().all()
    )
    transfers = await list_all_transfers(db, creator_id=filter_user_id)
    file_requests = await list_all_file_requests(db, creator_id=filter_user_id)
    summary = await get_shares_summary(db)

    ctx = branding_context(app_settings)
    ctx.update({
        "user": admin_user,
        "tab": tab,
        "transfers": transfers,
        "file_requests": file_requests,
        "summary": summary,
        "filter_users": filter_users,
        "filter_user_id": user if filter_user_id else "",
        "now": datetime.now(timezone.utc),
        "active": "shares",
    })
    saved = request.query_params.get("saved")
    if saved == "transfer":
        ctx["success"] = _("Transfer updated.")
    elif saved == "request":
        ctx["success"] = _("File request updated.")
    elif saved == "deleted_transfer":
        ctx["success"] = _("Transfer deleted.")
    elif saved == "deleted_request":
        ctx["success"] = _("File request deleted.")
    error = request.query_params.get("error")
    if error:
        ctx["error"] = error.replace("+", " ")
    return templates.TemplateResponse(request, "admin_shares.html", ctx)


@router.get("/shares/transfers/{transfer_id}/edit", response_class=HTMLResponse)
async def admin_edit_transfer_page(
    transfer_id: uuid.UUID,
    request: Request,
    tab: str = "transfers",
    user: str = "",
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    transfer = await get_transfer_for_admin(db, transfer_id)
    download_logs = sorted(transfer.download_logs, key=lambda log: log.created_at, reverse=True)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": admin_user,
        "transfer": transfer,
        "download_logs": download_logs,
        "has_password": bool(transfer.password_hash),
        "admin_edit": True,
        "back_url": _shares_url(tab=tab, user=user),
        "form_action": f"/admin/shares/transfers/{transfer_id}/edit",
        "regenerate_action": f"/admin/shares/transfers/{transfer_id}/regenerate-link",
        "files_upload_url": f"/admin/shares/transfers/{transfer_id}/files",
        "files_delete_url_template": f"/admin/shares/transfers/{transfer_id}/files/{{id}}",
        "shares_tab": tab,
        "shares_user": user,
        "now": datetime.now(timezone.utc),
        "success": _("Share link regenerated. The old link no longer works.")
        if request.query_params.get("link_regenerated")
        else None,
    })
    return templates.TemplateResponse(request, "transfers_edit.html", ctx)


@router.post("/shares/transfers/{transfer_id}/edit")
async def admin_edit_transfer_route(
    transfer_id: uuid.UUID,
    request: Request,
    title: str = Form(...),
    message: str = Form(""),
    password: str = Form(""),
    use_password: str = Form(""),
    expires_at: str = Form(...),
    max_downloads: int = Form(...),
    notify_on_download: str = Form(""),
    has_enabled_field: str = Form(""),
    enabled: str = Form(""),
    tab: str = Form("transfers"),
    user: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    transfer = await get_transfer_for_admin(db, transfer_id)
    expiry = parse_expiry_date(expires_at)
    app_settings = await get_app_settings(db)

    await update_transfer(
        db,
        transfer=transfer,
        user=admin,
        title=title,
        message=message or None,
        password=password or None,
        remove_password=not bool(use_password),
        expires_at=expiry,
        max_downloads=max_downloads,
        notify_on_download=bool(notify_on_download),
        ip_address=get_client_ip(request),
        enabled=bool(enabled) if has_enabled_field else None,
        app_settings=app_settings,
    )
    return RedirectResponse(_shares_url(tab=tab, user=user, saved="transfer"), status_code=303)


@router.post("/shares/transfers/{transfer_id}/files")
async def admin_add_transfer_file(
    transfer_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    transfer = await get_transfer_for_admin(db, transfer_id)
    transfer_file = await add_transfer_file(
        db,
        transfer=transfer,
        upload=file,
        app_settings=app_settings,
        user=admin,
        ip_address=get_client_ip(request),
    )
    return JSONResponse(
        {
            "id": str(transfer_file.id),
            "name": transfer_file.original_name,
            "size_bytes": transfer_file.size_bytes,
        }
    )


@router.delete("/shares/transfers/{transfer_id}/files/{file_id}")
async def admin_delete_transfer_file(
    transfer_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    transfer = await get_transfer_for_admin(db, transfer_id)
    await delete_transfer_file(
        db,
        transfer=transfer,
        file_id=file_id,
        user=admin,
        ip_address=get_client_ip(request),
    )
    return JSONResponse({"ok": True})


@router.post("/shares/transfers/{transfer_id}/regenerate-link")
async def admin_regenerate_transfer_link(
    transfer_id: uuid.UUID,
    request: Request,
    tab: str = Form("transfers"),
    user: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    transfer = await get_transfer_for_admin(db, transfer_id)
    await regenerate_transfer_link(
        db,
        transfer=transfer,
        user=admin,
        ip_address=get_client_ip(request),
    )
    edit_url = f"/admin/shares/transfers/{transfer_id}/edit?tab={tab}"
    if user:
        edit_url += f"&user={user}"
    return RedirectResponse(f"{edit_url}&link_regenerated=1", status_code=303)


@router.post("/shares/transfers/{transfer_id}/delete")
async def admin_delete_transfer(
    transfer_id: uuid.UUID,
    request: Request,
    tab: str = Form("transfers"),
    user: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    transfer = await get_transfer_for_admin(db, transfer_id)
    await delete_transfer(
        db,
        transfer=transfer,
        user=admin,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse(_shares_url(tab=tab, user=user, saved="deleted_transfer"), status_code=303)


@router.get("/shares/requests/{request_id}/edit", response_class=HTMLResponse)
async def admin_edit_request_page(
    request_id: uuid.UUID,
    request: Request,
    tab: str = "requests",
    user: str = "",
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    file_request = await get_file_request_for_admin(db, request_id)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": admin_user,
        "file_request": file_request,
        "has_password": bool(file_request.password_hash),
        "admin_edit": True,
        "back_url": _shares_url(tab=tab, user=user),
        "form_action": f"/admin/shares/requests/{request_id}/edit",
        "regenerate_action": f"/admin/shares/requests/{request_id}/regenerate-link",
        "files_delete_url_template": f"/admin/shares/requests/{request_id}/files/{{id}}",
        "files_download_url_prefix": f"/requests/{request_id}/files/",
        "shares_tab": tab,
        "shares_user": user,
        "now": datetime.now(timezone.utc),
        "success": _("Share link regenerated. The old link no longer works.")
        if request.query_params.get("link_regenerated")
        else None,
    })
    return templates.TemplateResponse(request, "requests_edit.html", ctx)


@router.post("/shares/requests/{request_id}/edit")
async def admin_edit_request_route(
    request_id: uuid.UUID,
    request: Request,
    title: str = Form(...),
    instructions: str = Form(""),
    password: str = Form(""),
    use_password: str = Form(""),
    expires_at: str = Form(...),
    max_uploads: int = Form(...),
    max_total_mb: int = Form(...),
    has_enabled_field: str = Form(""),
    enabled: str = Form(""),
    tab: str = Form("requests"),
    user: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    file_request = await get_file_request_for_admin(db, request_id)
    expiry = parse_expiry_date(expires_at)
    app_settings = await get_app_settings(db)

    await update_file_request(
        db,
        req=file_request,
        user=admin,
        title=title,
        instructions=instructions or None,
        password=password or None,
        remove_password=not bool(use_password),
        expires_at=expiry,
        max_uploads=max_uploads,
        max_total_bytes=max_total_mb * 1024 * 1024,
        ip_address=get_client_ip(request),
        enabled=bool(enabled) if has_enabled_field else None,
        app_settings=app_settings,
    )
    return RedirectResponse(_shares_url(tab=tab, user=user, saved="request"), status_code=303)


@router.delete("/shares/requests/{request_id}/files/{file_id}")
async def admin_delete_request_file(
    request_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    file_request = await get_file_request_for_admin(db, request_id)
    await delete_request_upload_file(
        db,
        req=file_request,
        file_id=file_id,
        user=admin,
        ip_address=get_client_ip(request),
    )
    return JSONResponse({"ok": True})


@router.post("/shares/requests/{request_id}/regenerate-link")
async def admin_regenerate_request_link(
    request_id: uuid.UUID,
    request: Request,
    tab: str = Form("requests"),
    user: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    file_request = await get_file_request_for_admin(db, request_id)
    await regenerate_file_request_link(
        db,
        req=file_request,
        user=admin,
        ip_address=get_client_ip(request),
    )
    edit_url = f"/admin/shares/requests/{request_id}/edit?tab={tab}"
    if user:
        edit_url += f"&user={user}"
    return RedirectResponse(f"{edit_url}&link_regenerated=1", status_code=303)


@router.post("/shares/requests/{request_id}/delete")
async def admin_delete_request(
    request_id: uuid.UUID,
    request: Request,
    tab: str = Form("requests"),
    user: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    file_request = await get_file_request_for_admin(db, request_id)
    await delete_file_request(
        db,
        req=file_request,
        user=admin,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse(_shares_url(tab=tab, user=user, saved="deleted_request"), status_code=303)


@router.post("/branding")
async def save_branding(
    app_name: str = Form(...),
    color_scheme: str = Form(...),
    logo: UploadFile | None = File(None),
    remove_logo: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    app_settings.app_name = app_name
    app_settings.color_scheme = color_scheme

    if remove_logo:
        clear_logo(app_settings)
    elif logo and logo.filename:
        await apply_logo_upload(app_settings, logo)

    await db.commit()
    return RedirectResponse("/admin?branding_saved=1", status_code=303)


@router.post("/limits/uploads")
async def save_upload_settings(
    max_file_size_mb: int = Form(...),
    upload_concurrency: int = Form(...),
    file_type_blocklist: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    app_settings.max_file_size_bytes = max_file_size_mb * 1024 * 1024
    app_settings.upload_concurrency = min(50, max(1, upload_concurrency))
    app_settings.file_type_blocklist = file_type_blocklist or None
    await db.commit()
    return RedirectResponse("/admin?uploads_saved=1", status_code=303)


@router.post("/limits/shares")
async def save_share_settings(
    default_expiry_days: int = Form(...),
    max_share_expiry_days: int = Form(...),
    max_downloads_default: int = Form(...),
    purge_grace_days: int = Form(...),
    purge_notify_days: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    app_settings.default_expiry_days = default_expiry_days
    app_settings.max_share_expiry_days = max(1, max_share_expiry_days)
    app_settings.max_downloads_default = max_downloads_default
    app_settings.purge_grace_days = max(0, purge_grace_days)
    app_settings.purge_notify_days = max(0, purge_notify_days)
    await db.commit()
    return RedirectResponse("/admin?shares_saved=1", status_code=303)


@router.post("/limits/access")
async def save_access_settings(
    allow_local_login: str = Form(""),
    allow_user_share_emails: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    app_settings.allow_local_login = bool(allow_local_login)
    app_settings.allow_user_share_emails = bool(allow_user_share_emails)
    await db.commit()
    return RedirectResponse("/admin?access_saved=1", status_code=303)


@router.get("/email", response_class=HTMLResponse)
async def admin_email_templates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    template_sections = [
        (key, label, TEMPLATE_VARIABLES[key])
        for key, label in (
            ("share", _("Share link (outbound transfer)")),
            ("request", _("File request link")),
            ("upload_notify", _("Upload received notification")),
            ("download_notify", _("Download notification")),
            ("expired_unused", _("Expired without activity")),
            ("purge_reminder", _("Deletion reminder before purge")),
        )
    ]
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "active": "email",
        "templates": templates_for_admin(app_settings),
        "subjects": subjects_for_admin(app_settings),
        "template_sections": template_sections,
    })
    if request.query_params.get("saved"):
        ctx["success"] = _("Email templates saved.")
    return templates.TemplateResponse(request, "admin_email.html", ctx)


@router.post("/email")
async def save_email_templates(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    form = await request.form()
    app_settings = await get_app_settings(db)
    for key in TEMPLATE_KEYS:
        tpl_field = TEMPLATE_FIELD_MAP[key]
        subj_field = SUBJECT_FIELD_MAP[key]
        tpl_value = form.get(f"tpl_{key}", "")
        subj_value = form.get(f"subj_{key}", "")
        setattr(app_settings, tpl_field, str(tpl_value).strip() or None)
        setattr(app_settings, subj_field, str(subj_value).strip() or None)
    await db.commit()
    return RedirectResponse("/admin/email?saved=1", status_code=303)


@router.post("/smtp")
async def save_smtp(
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    app_settings.smtp_host = smtp_host or None
    app_settings.smtp_port = smtp_port
    app_settings.smtp_user = smtp_user or None
    if smtp_password:
        app_settings.smtp_password = smtp_password
    app_settings.smtp_from = smtp_from or None
    await db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/smtp/test")
async def test_smtp(
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    try:
        await send_smtp_test_email(
            app_settings,
            to=user.email,
            overrides={
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
                "smtp_password": smtp_password,
                "smtp_from": smtp_from,
            },
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("SMTP test failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "message": _("Test email sent to %(email)s.") % {"email": user.email},
        }
    )


@router.post("/legal")
async def save_legal_pages(
    impressum_enabled: str = Form(""),
    impressum_markdown: str = Form(""),
    privacy_policy_enabled: str = Form(""),
    privacy_policy_markdown: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    app_settings.impressum_enabled = bool(impressum_enabled)
    app_settings.impressum_markdown = impressum_markdown or None
    app_settings.privacy_policy_enabled = bool(privacy_policy_enabled)
    app_settings.privacy_policy_markdown = privacy_policy_markdown or None
    await db.commit()
    return RedirectResponse("/admin?legal_saved=1", status_code=303)


@router.post("/users")
async def create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    is_admin: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    normalized_email = email.strip().lower()
    if not normalized_email or not password:
        return RedirectResponse("/admin?error=" + _("Email and password are required").replace(" ", "+"), status_code=303)

    existing = await db.execute(select(User).where(User.email == normalized_email))
    if existing.scalar_one_or_none():
        return RedirectResponse("/admin?error=" + _("User already exists").replace(" ", "+"), status_code=303)

    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        is_admin=bool(is_admin),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    await log_audit(
        db,
        action="user.created",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"email": normalized_email, "is_admin": bool(is_admin)},
    )
    return RedirectResponse("/admin?user_added=1", status_code=303)


@router.post("/users/{user_id}/promote")
async def promote_user(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if user and not user.is_admin:
        user.is_admin = True
        await db.commit()
        await log_audit(
            db,
            action="user.promoted",
            resource_type="user",
            resource_id=str(user.id),
            actor_id=admin.id,
            ip_address=get_client_ip(request),
        )
    return RedirectResponse("/admin?user_promoted=1", status_code=303)


@router.post("/users/{user_id}/demote")
async def demote_user(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if user_id == admin.id:
        return RedirectResponse("/admin?error=" + _("You cannot demote your own account").replace(" ", "+"), status_code=303)

    user = await db.get(User, user_id)
    if not user or not user.is_active or not user.is_admin:
        return RedirectResponse("/admin", status_code=303)

    admin_count = await db.scalar(
        select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    )
    if admin_count and admin_count <= 1:
        return RedirectResponse("/admin?error=" + _("Cannot demote the last admin").replace(" ", "+"), status_code=303)

    user.is_admin = False
    await db.commit()
    await log_audit(
        db,
        action="user.demoted",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"email": user.email},
    )
    return RedirectResponse("/admin?user_demoted=1", status_code=303)


@router.post("/users/{user_id}/password")
async def set_user_password(
    user_id: uuid.UUID,
    request: Request,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if not password:
        return RedirectResponse("/admin?error=" + _("Password is required").replace(" ", "+"), status_code=303)

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        return RedirectResponse("/admin", status_code=303)

    user.password_hash = hash_password(password)
    await db.commit()

    await log_audit(
        db,
        action="user.password_reset",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"email": user.email},
    )
    return RedirectResponse("/admin?user_password_set=1", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if user_id == admin.id:
        return RedirectResponse("/admin?error=" + _("You cannot delete your own account").replace(" ", "+"), status_code=303)

    user = await db.get(User, user_id)
    if not user:
        return RedirectResponse("/admin", status_code=303)

    if user.is_admin:
        admin_count = await db.scalar(
            select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True))
        )
        if admin_count and admin_count <= 1:
            return RedirectResponse("/admin?error=" + _("Cannot delete the last admin").replace(" ", "+"), status_code=303)

    user_email = user.email
    user_id_str = str(user.id)
    user.is_active = False
    await db.commit()

    await log_audit(
        db,
        action="user.deleted",
        resource_type="user",
        resource_id=user_id_str,
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"email": user_email},
    )
    return RedirectResponse("/admin?user_deleted=1", status_code=303)
