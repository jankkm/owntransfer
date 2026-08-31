from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from starlette.datastructures import UploadFile as StarletteUploadFile

from app.auth.deps import get_current_admin
from app.auth.passwords import hash_password, is_password_long_enough, is_share_password_valid, share_password_too_short_message
from app.auth.users import normalize_display_name
from app.config.oauth_providers import get_oauth_providers
from app.database import get_db
from app.i18n import _, normalize_locale, SUPPORTED_LOCALES
from app.http.client_ip import get_client_ip
from app.http.uploads import RAW_UPLOAD_FILENAME_HEADER, decode_raw_upload_filename
from app.models import User
from app.services.admin_archive import (
    archived_timeline,
    delete_archived_share,
    get_archived_share,
    list_archived_shares,
    parse_snapshot,
    snapshot_download_logs_display,
    snapshot_uploads_display,
)
from app.services.admin_overview import (
    get_shares_summary,
    get_shares_tab_counts,
    get_user_resource_counts,
    list_all_file_requests,
    list_all_transfers,
)
from app.services.audit import build_system_audit_rows, list_system_audit, log_audit
from app.services.archive import load_request_activity, load_transfer_activity
from app.services.branding import apply_logo_upload, clear_logo, normalize_hex_color
from app.services.datetime_display import parse_expiry_date
from app.services.email_templates import (
    SUBJECT_FIELD_MAP,
    TEMPLATE_FIELD_MAP,
    TEMPLATE_KEYS,
    TEMPLATE_VARIABLES,
    get_builtin_defaults,
    get_builtin_subjects,
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
from app.services.oauth_linking import (
    configured_provider_keys,
    get_grants_for_users,
    set_user_grants,
    unlink_oauth,
)
from app.services.transfer import (
    add_transfer_file,
    add_transfer_file_stream,
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


async def _list_active_users(db: AsyncSession) -> list[User]:
    return list(
        (await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.email))).scalars().all()
    )


def _parse_new_owner_id(value: str) -> uuid.UUID | None:
    value = value.strip()
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_("Invalid owner")) from exc


def _grant_keys_from_form(form, *, prefix: str = "grant_") -> set[str]:
    configured = configured_provider_keys()
    return {key for key in configured if form.get(f"{prefix}{key}")}


def _users_url(**params: str) -> str:
    filtered = {key: str(value) for key, value in params.items() if value}
    if not filtered:
        return "/admin/users"
    return f"/admin/users?{urlencode(filtered)}"


def _users_list_params_from_request(request: Request, form=None) -> dict[str, str]:
    q = ""
    sign_in = ""
    if form is not None:
        q = str(form.get("return_q", "")).strip()
        sign_in = str(form.get("return_sign_in", "")).strip()
    if not q and not sign_in:
        q = request.query_params.get("q", "").strip()
        sign_in = request.query_params.get("sign_in", "").strip()
    referer = request.headers.get("referer", "")
    if referer and not q and not sign_in:
        parsed = urlparse(referer)
        if parsed.path.rstrip("/") == "/admin/users":
            query = parse_qs(parsed.query)
            q = (query.get("q") or [""])[0].strip()
            sign_in = (query.get("sign_in") or [""])[0].strip()
    params: dict[str, str] = {}
    if q:
        params["q"] = q[:200]
    if sign_in and sign_in != "all" and sign_in in ("admin", "local", "sso"):
        params["sign_in"] = sign_in
    return params


def _users_redirect(request: Request, form=None, **flash: str) -> RedirectResponse:
    params = {key: value for key, value in flash.items() if value}
    params.update(_users_list_params_from_request(request, form))
    return RedirectResponse(_users_url(**params), status_code=303)


def _active_users_select(*, q: str = "", sign_in: str = "all"):
    query = select(User).where(User.is_active.is_(True))
    if q:
        term = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(User.email).like(term),
                func.lower(func.coalesce(User.display_name, "")).like(term),
            )
        )
    if sign_in == "admin":
        query = query.where(User.is_admin.is_(True))
    elif sign_in == "local":
        query = query.where(User.password_hash.isnot(None))
    elif sign_in == "sso":
        query = query.where(User.oauth_provider.isnot(None))
    return query.order_by(User.created_at)


@router.get("", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    audit_logs = await list_system_audit(db, limit=50)
    audit_log_rows = await build_system_audit_rows(db, audit_logs)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "app_settings": app_settings,
        "audit_log_rows": audit_log_rows,
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
    error = request.query_params.get("error")
    if error:
        ctx["error"] = error
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    q: str = "",
    sign_in: str = "all",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    search_q = q.strip()[:200]
    sign_in_filter = sign_in if sign_in in ("admin", "local", "sso") else "all"
    total_users = (
        await db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    )
    users = list((await db.execute(_active_users_select(q=search_q, sign_in=sign_in_filter))).scalars().all())
    user_counts = await get_user_resource_counts(db)
    user_oauth_grants = await get_grants_for_users(db, [u.id for u in users])
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "app_settings": app_settings,
        "users": users,
        "user_counts": user_counts,
        "user_oauth_grants": user_oauth_grants,
        "oauth_providers": get_oauth_providers(),
        "active": "users",
        "search_q": search_q,
        "sign_in_filter": sign_in_filter,
        "total_users": total_users,
        "users_filter_active": bool(search_q or sign_in_filter != "all"),
    })
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
    if request.query_params.get("user_display_name_saved"):
        ctx["success"] = _("Name updated.")
    if request.query_params.get("user_totp_removed"):
        ctx["success"] = _("Two-factor authentication removed.")
    if request.query_params.get("oauth_grants_saved"):
        ctx["success"] = _("OAuth link permissions saved.")
    if request.query_params.get("oauth_unlinked"):
        ctx["success"] = _("SSO unlinked.")
    error = request.query_params.get("error")
    if error:
        ctx["error"] = error
    return templates.TemplateResponse(request, "admin_users.html", ctx)


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

    if tab not in ("transfers", "requests", "archive"):
        tab = "transfers"

    filter_users = list(
        (await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.email))).scalars().all()
    )
    transfers = await list_all_transfers(db, creator_id=filter_user_id) if tab != "archive" else []
    file_requests = await list_all_file_requests(db, creator_id=filter_user_id) if tab != "archive" else []
    archived_shares = await list_archived_shares(db, creator_id=filter_user_id) if tab == "archive" else []
    summary = await get_shares_summary(db)
    tab_counts = await get_shares_tab_counts(db, creator_id=filter_user_id)

    ctx = branding_context(app_settings)
    ctx.update({
        "user": admin_user,
        "tab": tab,
        "transfers": transfers,
        "file_requests": file_requests,
        "archived_shares": archived_shares,
        "summary": summary,
        "tab_counts": tab_counts,
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
    elif saved == "deleted_archive":
        ctx["success"] = _("Archive record deleted.")
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
    download_logs, timeline = await load_transfer_activity(db, transfer)
    owner_users = await _list_active_users(db)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": admin_user,
        "transfer": transfer,
        "download_logs": download_logs,
        "timeline": timeline,
        "has_password": bool(transfer.password_hash),
        "admin_edit": True,
        "owner_users": owner_users,
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
    created_by: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    transfer = await get_transfer_for_admin(db, transfer_id)
    expiry = parse_expiry_date(expires_at)
    app_settings = await get_app_settings(db)
    clean_password = password.strip() if password.strip() else None
    if bool(use_password) and clean_password and not is_share_password_valid(
        clean_password, app_settings.share_password_length
    ):
        download_logs, timeline = await load_transfer_activity(db, transfer)
        owner_users = await _list_active_users(db)
        ctx = branding_context(app_settings)
        ctx.update({
            "user": admin,
            "transfer": transfer,
            "download_logs": download_logs,
            "timeline": timeline,
            "has_password": bool(transfer.password_hash),
            "admin_edit": True,
            "owner_users": owner_users,
            "back_url": _shares_url(tab=tab, user=user),
            "form_action": f"/admin/shares/transfers/{transfer_id}/edit",
            "regenerate_action": f"/admin/shares/transfers/{transfer_id}/regenerate-link",
            "files_upload_url": f"/admin/shares/transfers/{transfer_id}/files",
            "files_delete_url_template": f"/admin/shares/transfers/{transfer_id}/files/{{id}}",
            "shares_tab": tab,
            "shares_user": user,
            "now": datetime.now(timezone.utc),
            "error": share_password_too_short_message(app_settings.share_password_length),
        })
        return templates.TemplateResponse(request, "transfers_edit.html", ctx, status_code=400)

    await update_transfer(
        db,
        transfer=transfer,
        user=admin,
        title=title,
        message=message or None,
        password=clean_password,
        remove_password=not bool(use_password),
        expires_at=expiry,
        max_downloads=max_downloads,
        notify_on_download=bool(notify_on_download),
        ip_address=get_client_ip(request),
        enabled=bool(enabled) if has_enabled_field else None,
        app_settings=app_settings,
        new_owner_id=_parse_new_owner_id(created_by),
    )
    return RedirectResponse(_shares_url(tab=tab, user=user, saved="transfer"), status_code=303)


@router.post("/shares/transfers/{transfer_id}/files")
async def admin_add_transfer_file(
    transfer_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    transfer = await get_transfer_for_admin(db, transfer_id)
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if not isinstance(file, StarletteUploadFile):
            raise HTTPException(status_code=400, detail=_("Missing filename"))
        transfer_file = await add_transfer_file(
            db,
            transfer=transfer,
            upload=file,
            app_settings=app_settings,
            user=admin,
            ip_address=get_client_ip(request),
        )
    else:
        try:
            filename = decode_raw_upload_filename(
                request.headers.get(RAW_UPLOAD_FILENAME_HEADER)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=_("Invalid filename")) from exc
        try:
            expected_size = int(request.headers["content-length"])
        except (KeyError, ValueError):
            expected_size = None
        transfer_file = await add_transfer_file_stream(
            db,
            transfer=transfer,
            chunks=request.stream(),
            filename=filename,
            content_type=content_type or None,
            app_settings=app_settings,
            user=admin,
            ip_address=get_client_ip(request),
            expected_size=expected_size,
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


@router.get("/shares/archive/{archive_id}", response_class=HTMLResponse)
async def admin_archive_detail(
    archive_id: uuid.UUID,
    request: Request,
    tab: str = "archive",
    user: str = "",
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    archived = await get_archived_share(db, archive_id)
    if not archived:
        raise HTTPException(status_code=404, detail=_("Archive record not found"))

    snapshot = parse_snapshot(archived)
    timeline = await archived_timeline(db, archived)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": admin_user,
        "archived": archived,
        "snapshot": snapshot,
        "timeline": timeline,
        "back_url": _shares_url(tab=tab, user=user),
        "filter_user_id": user,
        "active": "shares",
    })
    if archived.resource_type == "transfer":
        ctx["download_logs"] = snapshot_download_logs_display(snapshot)
    else:
        ctx["request_uploads"] = snapshot_uploads_display(snapshot)
    return templates.TemplateResponse(request, "admin_archive_detail.html", ctx)


@router.post("/shares/archive/{archive_id}/delete")
async def admin_delete_archive(
    archive_id: uuid.UUID,
    request: Request,
    tab: str = Form("archive"),
    user: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    archived = await get_archived_share(db, archive_id)
    if not archived:
        raise HTTPException(status_code=404, detail=_("Archive record not found"))
    await delete_archived_share(db, archived)
    return RedirectResponse(_shares_url(tab=tab, user=user, saved="deleted_archive"), status_code=303)


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
    request_uploads, timeline = await load_request_activity(db, file_request)
    owner_users = await _list_active_users(db)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": admin_user,
        "file_request": file_request,
        "request_uploads": request_uploads,
        "timeline": timeline,
        "has_password": bool(file_request.password_hash),
        "admin_edit": True,
        "owner_users": owner_users,
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
    created_by: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    file_request = await get_file_request_for_admin(db, request_id)
    expiry = parse_expiry_date(expires_at)
    app_settings = await get_app_settings(db)
    clean_password = password.strip() if password.strip() else None
    if bool(use_password) and clean_password and not is_share_password_valid(
        clean_password, app_settings.share_password_length
    ):
        request_uploads, timeline = await load_request_activity(db, file_request)
        owner_users = await _list_active_users(db)
        ctx = branding_context(app_settings)
        ctx.update({
            "user": admin,
            "file_request": file_request,
            "request_uploads": request_uploads,
            "timeline": timeline,
            "has_password": bool(file_request.password_hash),
            "admin_edit": True,
            "owner_users": owner_users,
            "back_url": _shares_url(tab=tab, user=user),
            "form_action": f"/admin/shares/requests/{request_id}/edit",
            "regenerate_action": f"/admin/shares/requests/{request_id}/regenerate-link",
            "files_delete_url_template": f"/admin/shares/requests/{request_id}/files/{{id}}",
            "files_download_url_prefix": f"/requests/{request_id}/files/",
            "shares_tab": tab,
            "shares_user": user,
            "now": datetime.now(timezone.utc),
            "error": share_password_too_short_message(app_settings.share_password_length),
        })
        return templates.TemplateResponse(request, "requests_edit.html", ctx, status_code=400)

    try:
        await update_file_request(
            db,
            req=file_request,
            user=admin,
            title=title,
            instructions=instructions or None,
            password=clean_password,
            remove_password=not bool(use_password),
            expires_at=expiry,
            max_uploads=max_uploads,
            max_total_bytes=max_total_mb * 1024 * 1024,
            ip_address=get_client_ip(request),
            enabled=bool(enabled) if has_enabled_field else None,
            app_settings=app_settings,
            new_owner_id=_parse_new_owner_id(created_by),
        )
    except HTTPException as exc:
        request_uploads, timeline = await load_request_activity(db, file_request)
        owner_users = await _list_active_users(db)
        ctx = branding_context(app_settings)
        ctx.update({
            "user": admin,
            "file_request": file_request,
            "request_uploads": request_uploads,
            "timeline": timeline,
            "has_password": bool(file_request.password_hash),
            "admin_edit": True,
            "owner_users": owner_users,
            "back_url": _shares_url(tab=tab, user=user),
            "form_action": f"/admin/shares/requests/{request_id}/edit",
            "regenerate_action": f"/admin/shares/requests/{request_id}/regenerate-link",
            "files_delete_url_template": f"/admin/shares/requests/{request_id}/files/{{id}}",
            "files_download_url_prefix": f"/requests/{request_id}/files/",
            "shares_tab": tab,
            "shares_user": user,
            "now": datetime.now(timezone.utc),
            "error": exc.detail if isinstance(exc.detail, str) else _("Could not update file request"),
        })
        return templates.TemplateResponse(request, "requests_edit.html", ctx, status_code=exc.status_code)
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
    validated_color = normalize_hex_color(color_scheme)
    if validated_color is None:
        return RedirectResponse(
            "/admin?error=" + _("Color scheme must be a hex color like #2563eb").replace(" ", "+"),
            status_code=303,
        )
    app_settings.color_scheme = validated_color

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
    max_uploads_default: int = Form(...),
    share_password_length: int = Form(...),
    purge_grace_days: int = Form(...),
    purge_notify_days: int = Form(...),
    archive_retention_days: int = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_admin),
):
    app_settings = await get_app_settings(db)
    app_settings.default_expiry_days = default_expiry_days
    app_settings.max_share_expiry_days = max(1, max_share_expiry_days)
    app_settings.max_downloads_default = max_downloads_default
    app_settings.max_uploads_default = max_uploads_default
    app_settings.share_password_length = min(128, max(8, share_password_length))
    app_settings.purge_grace_days = max(0, purge_grace_days)
    app_settings.purge_notify_days = max(0, purge_notify_days)
    app_settings.archive_retention_days = max(0, archive_retention_days)
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
        "email_defaults_url": "/admin/email/defaults",
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


@router.get("/email/defaults", response_class=JSONResponse)
async def email_template_defaults(
    locale: str = "en",
    user: User = Depends(get_current_admin),
):
    resolved = normalize_locale(locale)
    if not resolved or resolved not in SUPPORTED_LOCALES:
        resolved = "en"
    defaults = get_builtin_defaults(resolved)
    subjects = get_builtin_subjects(resolved)
    return {key: {"subject": subjects[key], "body": defaults[key]} for key in TEMPLATE_KEYS}


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
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    form = await request.form()
    normalized_email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    is_admin = bool(form.get("is_admin"))
    display_name = normalize_display_name(str(form.get("display_name", "")))
    if not normalized_email or not password:
        return _users_redirect(request, form, error=_("Email and password are required"))
    if not is_password_long_enough(password):
        return _users_redirect(request, form, error=_("Password must be at least 8 characters"))

    existing = await db.execute(select(User).where(User.email == normalized_email))
    if existing.scalar_one_or_none():
        return _users_redirect(request, form, error=_("User already exists"))

    user = User(
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=display_name,
        is_admin=bool(is_admin),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    grant_keys = _grant_keys_from_form(form, prefix="create_grant_")
    if grant_keys:
        await set_user_grants(db, user, grant_keys, admin)
        await db.commit()

    await log_audit(
        db,
        action="user.created",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"email": normalized_email, "is_admin": bool(is_admin), "oauth_grants": sorted(grant_keys)},
    )
    return _users_redirect(request, form, user_added="1")


@router.post("/users/{user_id}/display-name")
async def set_user_display_name(
    user_id: uuid.UUID,
    request: Request,
    display_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        return _users_redirect(request)

    user.display_name = normalize_display_name(display_name)
    await db.commit()

    await log_audit(
        db,
        action="user.display_name_updated",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"email": user.email, "display_name": user.display_name},
    )
    return _users_redirect(request, user_display_name_saved="1")


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
    return _users_redirect(request, user_promoted="1")


@router.post("/users/{user_id}/demote")
async def demote_user(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if user_id == admin.id:
        return _users_redirect(request, error=_("You cannot demote your own account"))

    user = await db.get(User, user_id)
    if not user or not user.is_active or not user.is_admin:
        return _users_redirect(request)

    admin_count = await db.scalar(
        select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True))
    )
    if admin_count and admin_count <= 1:
        return _users_redirect(request, error=_("Cannot demote the last admin"))

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
    return _users_redirect(request, user_demoted="1")


@router.post("/users/{user_id}/password")
async def set_user_password(
    user_id: uuid.UUID,
    request: Request,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if not password:
        return _users_redirect(request, error=_("Password is required"))
    if not is_password_long_enough(password):
        return _users_redirect(request, error=_("Password must be at least 8 characters"))

    user = await db.get(User, user_id)
    if not user or not user.is_active:
        return _users_redirect(request)

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
    return _users_redirect(request, user_password_set="1")


@router.post("/users/{user_id}/totp/remove")
async def remove_user_totp(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        return _users_redirect(request)
    if not user.totp_enabled:
        return _users_redirect(request, error=_("Two-factor authentication is not enabled for this user"))

    user.totp_secret = None
    user.totp_enabled = False
    await db.commit()

    await log_audit(
        db,
        action="user.totp_reset",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"email": user.email},
    )
    return _users_redirect(request, user_totp_removed="1")


@router.post("/users/{user_id}/oauth-grants")
async def save_user_oauth_grants(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        return _users_redirect(request)

    form = await request.form()
    grant_keys = _grant_keys_from_form(form)
    try:
        await set_user_grants(db, user, grant_keys, admin)
    except HTTPException as exc:
        return _users_redirect(request, form, error=str(exc.detail))

    await db.commit()
    await log_audit(
        db,
        action="user.oauth_grants_updated",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"providers": sorted(grant_keys)},
    )
    return _users_redirect(request, form, oauth_grants_saved="1")


@router.post("/users/{user_id}/oauth/unlink")
async def unlink_user_oauth(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        return _users_redirect(request)
    if not user.oauth_provider:
        return _users_redirect(request, error=_("This account is not linked to an identity provider"))

    app_settings = await get_app_settings(db)
    if user_id == admin.id and not app_settings.allow_local_login:
        return _users_redirect(
            request,
            error=_("You cannot unlink your own SSO while local login is disabled."),
        )

    previous_provider = unlink_oauth(user)
    await db.commit()
    await log_audit(
        db,
        action="user.oauth_unlinked",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=admin.id,
        ip_address=get_client_ip(request),
        metadata={"provider": previous_provider},
    )
    return _users_redirect(request, oauth_unlinked="1")


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if user_id == admin.id:
        return _users_redirect(request, error=_("You cannot delete your own account"))

    user = await db.get(User, user_id)
    if not user:
        return _users_redirect(request)

    if user.is_admin:
        admin_count = await db.scalar(
            select(func.count()).select_from(User).where(User.is_admin.is_(True), User.is_active.is_(True))
        )
        if admin_count and admin_count <= 1:
            return _users_redirect(request, error=_("Cannot delete the last admin"))

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
    return _users_redirect(request, user_deleted="1")
