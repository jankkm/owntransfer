from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.auth.deps import get_current_user, require_user_id
from app.auth.exceptions import NotAuthenticated
from app.auth.login_redirect import dashboard_redirect
from app.auth.passwords import is_share_password_valid, share_password_too_short_message
from app.database import async_session, get_db
from app.i18n import _
from app.http.client_ip import get_client_ip
from app.http.uploads import (
    RAW_UPLOAD_FILENAME_HEADER,
    UPLOAD_BATCH_HEADER,
    decode_raw_upload_filename,
    decode_staged_file_ids,
    new_upload_batch,
    validate_upload_batch,
)
from app.limiter import limiter
from app.models import User
from app.services.archive import load_transfer_activity
from app.services.datetime_display import parse_expiry_date
from app.services.settings import get_app_settings
from app.services.share_list import apply_transfer_list_query, parse_share_list_query
from app.services.staging import (
    StagingLimits,
    add_staged_file,
    add_staged_stream,
    clear_staged_files,
    discard_staged_paths,
    get_staged_files,
    remove_staged_file,
    restore_staged_files,
    take_selected_staged_files,
    take_staged_files,
)
from app.services.transfer import (
    add_transfer_file,
    add_transfer_file_stream,
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


def _transfer_staging_scope(user_id: uuid.UUID, batch: str) -> str:
    return f"transfer_{user_id}_{batch}"


def _request_upload_batch(request: Request) -> str:
    try:
        return validate_upload_batch(request.headers.get(UPLOAD_BATCH_HEADER))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_("Invalid upload batch")) from exc


@router.get("/staging")
@limiter.limit("30/minute")
async def list_staged_transfer_files(
    request: Request,
    user_id: uuid.UUID = Depends(require_user_id),
):
    batch = _request_upload_batch(request)
    staged = get_staged_files(_transfer_staging_scope(user_id, batch))
    return JSONResponse(
        [
            {
                "id": f.id,
                "name": f.original_name,
                "size_bytes": f.size_bytes,
            }
            for f in staged
        ]
    )


@router.delete("/staging")
@limiter.limit("30/minute")
async def clear_staged_transfer_files(
    request: Request,
    user_id: uuid.UUID = Depends(require_user_id),
):
    batch = _request_upload_batch(request)
    await clear_staged_files(_transfer_staging_scope(user_id, batch))
    return JSONResponse({"ok": True})


@router.post("/staging")
@limiter.limit("30/minute")
async def stage_transfer_file(
    request: Request,
    user_id: uuid.UUID = Depends(require_user_id),
):
    async with async_session() as db:
        app_settings = await get_app_settings(db)
        limits = StagingLimits.from_settings(app_settings)
    batch = _request_upload_batch(request)
    scope = _transfer_staging_scope(user_id, batch)
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if not isinstance(file, StarletteUploadFile):
            raise HTTPException(status_code=400, detail=_("Missing filename"))
        staged = await add_staged_file(scope, file, limits)
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
        staged = await add_staged_stream(
            scope,
            request.stream(),
            filename,
            content_type or None,
            limits,
            expected_size=expected_size,
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
    batch = _request_upload_batch(request)
    await remove_staged_file(_transfer_staging_scope(user_id, batch), file_id)
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
    batch: str = "",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        batch = validate_upload_batch(batch)
    except ValueError:
        batch = new_upload_batch()

    legacy_scope = f"transfer_{user.id}"
    legacy_files = await take_staged_files(legacy_scope)
    if legacy_files:
        await restore_staged_files(_transfer_staging_scope(user.id, batch), legacy_files)

    app_settings = await get_app_settings(db)
    ctx = branding_context(app_settings)
    ctx.update({"user": user, "staging_batch": batch})
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
    staging_batch: str = Form(...),
    staged_file_ids: str = Form(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    try:
        staging_batch = validate_upload_batch(staging_batch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_("Invalid upload batch")) from exc

    emails = [e.strip() for e in recipient_emails.split(",") if e.strip()]
    if not app_settings.allow_user_share_emails:
        emails = []
    if bool(use_password) and not password.strip():
        ctx = branding_context(app_settings)
        ctx.update({
            "user": user,
            "staging_batch": staging_batch,
            "error": _("Enter a password to enable protection"),
        })
        return templates.TemplateResponse(request, "transfers_new.html", ctx, status_code=400)
    clean_password = password.strip() if use_password else None
    if clean_password and not is_share_password_valid(clean_password, app_settings.share_password_length):
        ctx = branding_context(app_settings)
        ctx.update({
            "user": user,
            "staging_batch": staging_batch,
            "error": share_password_too_short_message(app_settings.share_password_length),
        })
        return templates.TemplateResponse(request, "transfers_new.html", ctx, status_code=400)
    scope = _transfer_staging_scope(user.id, staging_batch)
    try:
        selected_ids = decode_staged_file_ids(staged_file_ids)
        staged_files = await take_selected_staged_files(scope, selected_ids)
    except (ValueError, HTTPException) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else _("Invalid staged file selection")
        ctx = branding_context(app_settings)
        ctx.update({
            "user": user,
            "staging_batch": staging_batch,
            "error": detail,
        })
        return templates.TemplateResponse(request, "transfers_new.html", ctx, status_code=400)
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
            "staging_batch": staging_batch,
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
    download_logs, timeline = await load_transfer_activity(db, transfer)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "transfer": transfer,
        "download_logs": download_logs,
        "timeline": timeline,
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
    user_id: uuid.UUID = Depends(require_user_id),
):
    async with async_session() as db:
        app_settings = await get_app_settings(db)
        transfer = await get_user_transfer(db, transfer_id, user_id)
        user = await db.get(User, user_id)
        if user is None:
            raise NotAuthenticated()
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
                user=user,
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
                user=user,
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
    clean_password = password.strip() if password.strip() else None
    if bool(use_password) and clean_password and not is_share_password_valid(
        clean_password, app_settings.share_password_length
    ):
        download_logs, timeline = await load_transfer_activity(db, transfer)
        ctx = branding_context(app_settings)
        ctx.update({
            "user": user,
            "transfer": transfer,
            "download_logs": download_logs,
            "timeline": timeline,
            "has_password": bool(transfer.password_hash),
            "now": datetime.now(timezone.utc),
            "error": share_password_too_short_message(app_settings.share_password_length),
        })
        return templates.TemplateResponse(request, "transfers_edit.html", ctx, status_code=400)

    await update_transfer(
        db,
        transfer=transfer,
        user=user,
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
