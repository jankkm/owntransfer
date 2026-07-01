from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_user_id
from app.auth.exceptions import NotAuthenticated
from app.auth.login_redirect import dashboard_redirect
from app.database import async_session, get_db
from app.i18n import _
from app.http.client_ip import get_client_ip
from app.limiter import limiter
from app.models import User
from app.services.datetime_display import parse_expiry_date
from app.services.settings import get_app_settings
from app.services.share_list import apply_transfer_list_query, parse_share_list_query
from app.services.staging import (
    StagingLimits,
    add_staged_file,
    discard_staged_paths,
    remove_staged_file,
    restore_staged_files,
    take_staged_files,
)
from app.services.transfer import (
    add_transfer_file,
    create_transfer,
    delete_transfer,
    delete_transfer_file,
    finalize_transfer_files,
    find_user_transfer,
    get_user_transfer,
    list_user_transfers,
    regenerate_transfer_link,
    update_transfer,
)
from app.templating import branding_context, templates

router = APIRouter(prefix="/transfers", tags=["transfers"])


def _transfer_staging_scope(user_id: uuid.UUID) -> str:
    return f"transfer_{user_id}"


@router.post("/staging")
@limiter.limit("30/minute")
async def stage_transfer_file(
    request: Request,
    file: UploadFile = File(...),
    user_id: uuid.UUID = Depends(require_user_id),
):
    async with async_session() as db:
        app_settings = await get_app_settings(db)
        limits = StagingLimits.from_settings(app_settings)
    staged = await add_staged_file(
        _transfer_staging_scope(user_id),
        file,
        limits,
    )
    return JSONResponse(
        {
            "id": staged.id,
            "name": staged.original_name,
            "size_bytes": staged.size_bytes,
        }
    )


@router.delete("/staging/{file_id}")
@limiter.limit("30/minute")
async def delete_staged_transfer_file(
    file_id: str,
    request: Request,
    user_id: uuid.UUID = Depends(require_user_id),
):
    await remove_staged_file(_transfer_staging_scope(user_id), file_id)
    return JSONResponse({"ok": True})


@router.get("", response_class=HTMLResponse)
async def list_transfers(
    request: Request,
    q: str = "",
    status: str = "all",
    sort: str = "created_desc",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    list_query = parse_share_list_query(q=q, status=status, sort=sort)
    now = datetime.now(timezone.utc)
    all_transfers = await list_user_transfers(db, user.id)
    transfers = apply_transfer_list_query(
        all_transfers,
        list_query,
        now=now,
        purge_grace_days=app_settings.purge_grace_days,
    )
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "transfers": transfers,
        "list_query": list_query,
        "now": now,
    })
    if request.query_params.get("updated"):
        ctx["success"] = _("Transfer updated successfully.")
    if request.query_params.get("created"):
        ctx["success"] = _("Transfer created successfully.")
    if request.query_params.get("deleted"):
        ctx["success"] = _("Transfer deleted.")
    return templates.TemplateResponse(request, "transfers_list.html", ctx)


@router.get("/new", response_class=HTMLResponse)
async def new_transfer(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    ctx = branding_context(app_settings)
    ctx["user"] = user
    return templates.TemplateResponse(request, "transfers_new.html", ctx)


@router.post("/new")
async def create_transfer_route(
    request: Request,
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    message: str = Form(""),
    password: str = Form(""),
    use_password: str = Form(""),
    expires_at: str = Form(...),
    max_downloads: int = Form(...),
    notify_on_download: str = Form(""),
    recipient_emails: str = Form(""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    emails = [e.strip() for e in recipient_emails.split(",") if e.strip()]
    if not app_settings.allow_user_share_emails:
        emails = []
    if bool(use_password) and not password.strip():
        ctx = branding_context(app_settings)
        ctx.update({
            "user": user,
            "error": _("Enter a password to enable protection"),
        })
        return templates.TemplateResponse(request, "transfers_new.html", ctx, status_code=400)
    scope = _transfer_staging_scope(user.id)
    staged_files = await take_staged_files(scope)
    clean_password = password.strip() if use_password else None
    try:
        transfer = await create_transfer(
            db,
            user=user,
            title=title,
            message=message or None,
            password=clean_password,
            expires_at=parse_expiry_date(expires_at),
            max_downloads=max_downloads,
            notify_on_download=bool(notify_on_download),
            recipient_emails=emails,
            app_settings=app_settings,
            ip_address=get_client_ip(request),
            staged_files=staged_files,
        )
    except HTTPException as exc:
        await restore_staged_files(scope, staged_files)
        ctx = branding_context(app_settings)
        ctx.update({
            "user": user,
            "error": exc.detail if isinstance(exc.detail, str) else _("Could not create transfer"),
        })
        return templates.TemplateResponse(request, "transfers_new.html", ctx, status_code=exc.status_code)
    if transfer.is_preparing:
        background_tasks.add_task(
            finalize_transfer_files,
            transfer.id,
            staged_files,
            user_id=user.id,
            title=title,
            message=message or None,
            password=clean_password,
            recipient_emails=emails,
            ip_address=get_client_ip(request),
        )
    else:
        await discard_staged_paths(staged_files)
    return RedirectResponse(f"/transfers?created={transfer.public_token}", status_code=303)


@router.get("/{transfer_id}/edit", response_class=HTMLResponse)
async def edit_transfer_page(
    transfer_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    transfer = await find_user_transfer(db, transfer_id, user.id)
    if transfer is None:
        return dashboard_redirect()
    download_logs = sorted(transfer.download_logs, key=lambda log: log.created_at, reverse=True)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "transfer": transfer,
        "download_logs": download_logs,
        "has_password": bool(transfer.password_hash),
        "now": datetime.now(timezone.utc),
        "success": _("Share link regenerated. The old link no longer works.")
        if request.query_params.get("link_regenerated")
        else None,
    })
    return templates.TemplateResponse(request, "transfers_edit.html", ctx)


@router.post("/{transfer_id}/files")
@limiter.limit("30/minute")
async def add_transfer_file_route(
    transfer_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    user_id: uuid.UUID = Depends(require_user_id),
):
    async with async_session() as db:
        app_settings = await get_app_settings(db)
        transfer = await get_user_transfer(db, transfer_id, user_id)
        user = await db.get(User, user_id)
        if user is None:
            raise NotAuthenticated()
        transfer_file = await add_transfer_file(
            db,
            transfer=transfer,
            upload=file,
            app_settings=app_settings,
            user=user,
            ip_address=get_client_ip(request),
        )
    return JSONResponse(
        {
            "id": str(transfer_file.id),
            "name": transfer_file.original_name,
            "size_bytes": transfer_file.size_bytes,
        }
    )


@router.delete("/{transfer_id}/files/{file_id}")
async def delete_transfer_file_route(
    transfer_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    transfer = await get_user_transfer(db, transfer_id, user.id)
    await delete_transfer_file(
        db,
        transfer=transfer,
        file_id=file_id,
        user=user,
        ip_address=get_client_ip(request),
    )
    return JSONResponse({"ok": True})


@router.post("/{transfer_id}/edit")
async def edit_transfer_route(
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    transfer = await find_user_transfer(db, transfer_id, user.id)
    if transfer is None:
        return dashboard_redirect()
    expiry = parse_expiry_date(expires_at)
    app_settings = await get_app_settings(db)

    await update_transfer(
        db,
        transfer=transfer,
        user=user,
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
    return RedirectResponse("/transfers?updated=1", status_code=303)


@router.post("/{transfer_id}/delete")
async def delete_transfer_route(
    transfer_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    transfer = await find_user_transfer(db, transfer_id, user.id)
    if transfer is None:
        return dashboard_redirect()
    await delete_transfer(
        db,
        transfer=transfer,
        user=user,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse("/transfers?deleted=1", status_code=303)


@router.post("/{transfer_id}/regenerate-link")
async def regenerate_link_route(
    transfer_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    transfer = await find_user_transfer(db, transfer_id, user.id)
    if transfer is None:
        return dashboard_redirect()
    await regenerate_transfer_link(
        db,
        transfer=transfer,
        user=user,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse(f"/transfers/{transfer_id}/edit?link_regenerated=1", status_code=303)
