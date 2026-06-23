from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.database import get_db
from app.http.client_ip import get_client_ip
from app.models import User
from app.services.file_request import (
    create_file_request,
    delete_file_request,
    delete_request_upload_file,
    get_request_upload_file,
    get_user_request,
    iter_upload_file,
    list_user_requests,
    regenerate_file_request_link,
    stream_file_request_zip,
    update_file_request,
)
from app.services.datetime_display import parse_expiry_date
from app.services.settings import get_app_settings
from app.services.share_list import apply_request_list_query, parse_share_list_query
from app.templating import branding_context, templates

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("", response_class=HTMLResponse)
async def list_requests(
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
    all_requests = await list_user_requests(db, user.id)
    requests_list = apply_request_list_query(
        all_requests,
        list_query,
        now=now,
        purge_grace_days=app_settings.purge_grace_days,
    )
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "requests": requests_list,
        "list_query": list_query,
        "now": now,
    })
    if request.query_params.get("updated"):
        ctx["success"] = "File request updated successfully."
    if request.query_params.get("created"):
        ctx["success"] = "File request created successfully."
    if request.query_params.get("deleted"):
        ctx["success"] = "File request deleted."
    return templates.TemplateResponse(request, "requests_list.html", ctx)


@router.get("/new", response_class=HTMLResponse)
async def new_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    ctx = branding_context(app_settings)
    ctx["user"] = user
    return templates.TemplateResponse(request, "requests_new.html", ctx)


@router.post("/new")
async def create_request_route(
    request: Request,
    title: str = Form(...),
    instructions: str = Form(""),
    password: str = Form(""),
    use_password: str = Form(""),
    expires_at: str = Form(...),
    max_uploads: int = Form(10),
    max_total_mb: int = Form(2048),
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
            "error": "Enter a password to enable protection",
        })
        return templates.TemplateResponse(request, "requests_new.html", ctx, status_code=400)
    await create_file_request(
        db,
        user=user,
        title=title,
        instructions=instructions or None,
        password=password.strip() if use_password else None,
        expires_at=parse_expiry_date(expires_at),
        max_uploads=max_uploads,
        max_total_bytes=max_total_mb * 1024 * 1024,
        recipient_emails=emails,
        app_settings=app_settings,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse("/requests?created=1", status_code=303)


@router.get("/{request_id}/edit", response_class=HTMLResponse)
async def edit_request_page(
    request_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    app_settings = await get_app_settings(db)
    file_request = await get_user_request(db, request_id, user.id)
    ctx = branding_context(app_settings)
    ctx.update({
        "user": user,
        "file_request": file_request,
        "has_password": bool(file_request.password_hash),
        "now": datetime.now(timezone.utc),
        "success": "Share link regenerated. The old link no longer works."
        if request.query_params.get("link_regenerated")
        else None,
    })
    return templates.TemplateResponse(request, "requests_edit.html", ctx)


@router.delete("/{request_id}/files/{file_id}")
async def delete_request_file_route(
    request_id: uuid.UUID,
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_request = await get_user_request(db, request_id, user.id)
    await delete_request_upload_file(
        db,
        req=file_request,
        file_id=file_id,
        user=user,
        ip_address=get_client_ip(request),
    )
    return JSONResponse({"ok": True})


@router.post("/{request_id}/edit")
async def edit_request_route(
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_request = await get_user_request(db, request_id, user.id)
    expiry = parse_expiry_date(expires_at)
    app_settings = await get_app_settings(db)

    await update_file_request(
        db,
        req=file_request,
        user=user,
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
    return RedirectResponse("/requests?updated=1", status_code=303)


@router.post("/{request_id}/delete")
async def delete_request_route(
    request_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_request = await get_user_request(db, request_id, user.id)
    await delete_file_request(
        db,
        req=file_request,
        user=user,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse("/requests?deleted=1", status_code=303)


@router.post("/{request_id}/regenerate-link")
async def regenerate_link_route(
    request_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_request = await get_user_request(db, request_id, user.id)
    await regenerate_file_request_link(
        db,
        req=file_request,
        user=user,
        ip_address=get_client_ip(request),
    )
    return RedirectResponse(f"/requests/{request_id}/edit?link_regenerated=1", status_code=303)


@router.get("/{request_id}/download")
async def download_request_zip(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    file_request = await get_user_request(db, request_id, user.id)
    zip_data = await stream_file_request_zip(file_request)
    filename = f"{file_request.title or 'file-request'}.zip".replace("/", "-")
    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{request_id}/files/{file_id}")
async def download_request_file(
    request_id: uuid.UUID,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    upload_file = await get_request_upload_file(db, request_id, file_id, user.id)
    media_type = upload_file.content_type or "application/octet-stream"
    filename = upload_file.original_name.replace('"', "")
    return StreamingResponse(
        iter_upload_file(upload_file),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
