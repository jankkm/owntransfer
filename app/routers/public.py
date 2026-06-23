from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.download_grants import (
    grant_transfer_download,
    has_transfer_download_grant,
    mark_transfer_download_counted,
)
from app.auth.unlock_cookies import (
    is_request_unlocked,
    is_transfer_unlocked,
    set_request_unlock,
    set_transfer_unlock,
)
from app.database import get_db
from app.http.client_ip import get_client_ip
from app.limiter import limiter
from app.models import User
from app.services.file_request import (
    ensure_request_accessible,
    finalize_request_upload,
    lookup_request_by_token,
    verify_request_password,
)
from app.services.security_log import log_invalid_request_link, log_invalid_transfer_link
from app.services.settings import get_app_settings
from app.services.staging import (
    add_staged_file,
    discard_staged_paths,
    remove_staged_file,
    restore_staged_files,
    take_staged_files,
)
from app.services.transfer import (
    ensure_transfer_accessible,
    get_transfer_file,
    iter_transfer_file,
    log_transfer_download,
    lookup_transfer_by_token,
    record_download,
    stream_transfer_zip,
    verify_transfer_password,
)
from app.templating import branding_context, templates

router = APIRouter(tags=["public"])

_LOGIN_URL = "/auth/login"


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(_LOGIN_URL, status_code=303)


def _invalid_transfer_redirect(request: Request) -> RedirectResponse:
    log_invalid_transfer_link(request)
    return _login_redirect()


def _invalid_request_redirect(request: Request) -> RedirectResponse:
    log_invalid_request_link(request)
    return _login_redirect()


def _request_staging_scope(token: str) -> str:
    return f"request_{token}"


def _require_request_unlock(request: Request, token: str, *, password_required: bool) -> None:
    if not is_request_unlocked(request, token, password_required=password_required):
        raise HTTPException(status_code=403, detail="Unlock this file request before uploading")


def _require_download_grant(request: Request, token: str) -> None:
    if not has_transfer_download_grant(request.session, token):
        raise HTTPException(
            status_code=403,
            detail="Open the transfer page in this browser before downloading.",
        )


def _grant_response(
    request: Request,
    token: str,
    response: HTMLResponse,
) -> HTMLResponse:
    grant_transfer_download(request.session, token)
    return response


async def _handle_download_event(
    request: Request,
    db: AsyncSession,
    transfer,
    app_settings,
    creator_email: str | None,
    *,
    download_type: str,
    file_name: str | None = None,
) -> None:
    await log_transfer_download(
        db,
        transfer_id=transfer.id,
        ip_address=get_client_ip(request),
        download_type=download_type,
        file_name=file_name,
    )
    if mark_transfer_download_counted(request.session, transfer.public_token):
        await record_download(db, transfer, app_settings, creator_email)


@router.get("/d/{token}", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def download_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    transfer = await lookup_transfer_by_token(db, token)
    if not transfer:
        return _invalid_transfer_redirect(request)
    ensure_transfer_accessible(transfer)

    password_required = bool(transfer.password_hash)
    unlocked = is_transfer_unlocked(request, token, password_required=password_required)
    needs_password = password_required and not unlocked

    if unlocked and not has_transfer_download_grant(request.session, token):
        grant_transfer_download(request.session, token)

    can_download = unlocked and has_transfer_download_grant(request.session, token)

    ctx = branding_context(app_settings)
    ctx.update({"transfer": transfer, "needs_password": needs_password, "can_download": can_download})
    return templates.TemplateResponse(request, "public_download.html", ctx)


@router.post("/d/{token}", response_class=HTMLResponse)
async def download_unlock(token: str, request: Request, password: str = Form(""), db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    transfer = await lookup_transfer_by_token(db, token)
    if not transfer:
        return _invalid_transfer_redirect(request)
    ensure_transfer_accessible(transfer)
    if not verify_transfer_password(transfer, password):
        ctx = branding_context(app_settings)
        ctx.update({"transfer": transfer, "needs_password": True, "can_download": False, "error": "Invalid password"})
        return templates.TemplateResponse(request, "public_download.html", ctx, status_code=401)

    response = templates.TemplateResponse(
        request,
        "public_download.html",
        {
            **branding_context(app_settings),
            "transfer": transfer,
            "needs_password": False,
            "can_download": True,
        },
    )
    set_transfer_unlock(response, token)
    return _grant_response(request, token, response)


@router.get("/d/{token}/download")
@limiter.limit("30/minute")
async def download_files_zip(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_download_grant(request, token)
    app_settings = await get_app_settings(db)
    transfer = await lookup_transfer_by_token(db, token)
    if not transfer:
        return _invalid_transfer_redirect(request)
    ensure_transfer_accessible(transfer)
    if not is_transfer_unlocked(request, token, password_required=bool(transfer.password_hash)):
        raise HTTPException(status_code=403, detail="Password required")

    creator = await db.get(User, transfer.created_by)
    await _handle_download_event(
        request,
        db,
        transfer,
        app_settings,
        creator.email if creator else None,
        download_type="zip",
        file_name=f"{transfer.title or 'transfer'}.zip",
    )
    zip_data = await stream_transfer_zip(transfer)
    filename = f"{transfer.title or 'transfer'}.zip".replace("/", "-")
    return Response(
        content=zip_data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/d/{token}/files/{file_id}")
@limiter.limit("30/minute")
async def download_single_file(
    token: str,
    file_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_download_grant(request, token)
    app_settings = await get_app_settings(db)
    transfer = await lookup_transfer_by_token(db, token)
    if not transfer:
        return _invalid_transfer_redirect(request)
    ensure_transfer_accessible(transfer)
    if not is_transfer_unlocked(request, token, password_required=bool(transfer.password_hash)):
        raise HTTPException(status_code=403, detail="Password required")
    transfer_file = await get_transfer_file(transfer, file_id)

    creator = await db.get(User, transfer.created_by)
    await _handle_download_event(
        request,
        db,
        transfer,
        app_settings,
        creator.email if creator else None,
        download_type="file",
        file_name=transfer_file.original_name,
    )

    media_type = transfer_file.content_type or "application/octet-stream"
    filename = transfer_file.original_name.replace('"', "")
    return StreamingResponse(
        iter_transfer_file(transfer_file),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/r/{token}", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def upload_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    file_request = await lookup_request_by_token(db, token)
    if not file_request:
        return _invalid_request_redirect(request)
    ensure_request_accessible(file_request)
    password_required = bool(file_request.password_hash)
    needs_password = password_required and not is_request_unlocked(
        request, token, password_required=password_required
    )
    ctx = branding_context(app_settings)
    ctx.update({"file_request": file_request, "needs_password": needs_password})
    return templates.TemplateResponse(request, "public_upload.html", ctx)


@router.post("/r/{token}/staging")
@limiter.limit("60/minute")
async def stage_request_file(
    token: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    app_settings = await get_app_settings(db)
    file_request = await lookup_request_by_token(db, token)
    if not file_request:
        return _invalid_request_redirect(request)
    ensure_request_accessible(file_request)
    _require_request_unlock(request, token, password_required=bool(file_request.password_hash))
    staged = await add_staged_file(
        _request_staging_scope(token),
        file,
        app_settings,
        max_total_bytes=file_request.max_total_bytes,
    )
    return JSONResponse(
        {
            "id": staged.id,
            "name": staged.original_name,
            "size_bytes": staged.size_bytes,
        }
    )


@router.delete("/r/{token}/staging/{file_id}")
@limiter.limit("60/minute")
async def delete_staged_request_file(
    token: str,
    file_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    file_request = await lookup_request_by_token(db, token)
    if not file_request:
        return _invalid_request_redirect(request)
    ensure_request_accessible(file_request)
    _require_request_unlock(request, token, password_required=bool(file_request.password_hash))
    await remove_staged_file(_request_staging_scope(token), file_id)
    return JSONResponse({"ok": True})


@router.post("/r/{token}", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def upload_handler(
    token: str,
    request: Request,
    password: str = Form(""),
    unlock: str = Form(""),
    uploader_name: str = Form(""),
    uploader_email: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    app_settings = await get_app_settings(db)
    file_request = await lookup_request_by_token(db, token)
    if not file_request:
        return _invalid_request_redirect(request)
    ensure_request_accessible(file_request)

    if unlock:
        if not verify_request_password(file_request, password):
            ctx = branding_context(app_settings)
            ctx.update({"file_request": file_request, "needs_password": True, "error": "Invalid password"})
            return templates.TemplateResponse(request, "public_upload.html", ctx, status_code=401)
        response = templates.TemplateResponse(
            request,
            "public_upload.html",
            {**branding_context(app_settings), "file_request": file_request, "needs_password": False},
        )
        set_request_unlock(response, token)
        return response

    password_required = bool(file_request.password_hash)
    if not is_request_unlocked(request, token, password_required=password_required):
        return RedirectResponse(f"/r/{token}", status_code=303)

    creator = await db.get(User, file_request.created_by)
    if not creator:
        return RedirectResponse(f"/r/{token}", status_code=303)

    scope = _request_staging_scope(token)
    staged_files = await take_staged_files(scope)
    try:
        await finalize_request_upload(
            db,
            req=file_request,
            staged_files=staged_files,
            uploader_name=uploader_name or None,
            uploader_email=uploader_email or None,
            app_settings=app_settings,
            creator=creator,
            ip_address=get_client_ip(request),
        )
    except HTTPException as exc:
        await restore_staged_files(scope, staged_files)
        ctx = branding_context(app_settings)
        ctx.update({
            "file_request": file_request,
            "needs_password": False,
            "error": exc.detail if isinstance(exc.detail, str) else "Upload failed",
        })
        return templates.TemplateResponse(request, "public_upload.html", ctx, status_code=exc.status_code)
    await discard_staged_paths(staged_files)

    ctx = branding_context(app_settings)
    ctx.update({"file_request": file_request, "needs_password": False, "success": "Upload successful. Thank you!"})
    return templates.TemplateResponse(request, "public_upload.html", ctx)
