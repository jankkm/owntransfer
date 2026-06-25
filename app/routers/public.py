from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.download_grants import (
    grant_transfer_download,
    has_transfer_download_grant,
    mark_transfer_download_counted,
    mark_transfer_download_notified,
)
from app.auth.unlock_cookies import (
    is_request_unlocked,
    is_transfer_unlocked,
    set_request_unlock,
    set_transfer_unlock,
)
from app.database import async_session, get_db
from app.i18n import _, ngettext
from app.http.client_ip import get_client_ip
from app.limiter import limiter
from app.models import FileRequest, Transfer, User
from app.services.file_request import (
    ACCESS_DISABLED,
    ACCESS_EXPIRED,
    ACCESS_UPLOAD_LIMIT,
    finalize_request_upload,
    lookup_request_by_token,
    request_access_issue,
    verify_request_password,
)
from app.services.security_log import (
    log_invalid_request_link,
    log_invalid_transfer_link,
    log_invalid_unlock,
)
from app.services.settings import get_app_settings
from app.services.download_limits import try_reserve_download_slot
from app.services.staging import (
    StagingLimits,
    add_staged_file,
    discard_staged_paths,
    remove_staged_file,
    restore_staged_files,
    take_staged_files,
)
from app.services.transfer import (
    ACCESS_DISABLED,
    ACCESS_DOWNLOAD_LIMIT,
    ACCESS_EXPIRED,
    get_transfer_file,
    iter_transfer_file,
    log_transfer_download,
    lookup_transfer_by_token,
    record_download,
    transfer_access_issue,
    transfer_zip_entries,
    verify_transfer_password,
)
from app.services.unlock_lockout import (
    is_unlock_locked,
    record_failed_unlock,
    reset_unlock_lockout,
)
from app.services.zip_stream import stream_zip
from app.templating import branding_context, templates

router = APIRouter(tags=["public"])

_LOGIN_URL = "/auth/login"


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(_LOGIN_URL, status_code=303)


async def _unlock_lockout_error(kind: str, token: str) -> str | None:
    if await is_unlock_locked(kind, token):
        return _("Too many failed password attempts. Try again later.")
    return None


def _invalid_transfer_redirect(request: Request) -> RedirectResponse:
    log_invalid_transfer_link(request)
    return _login_redirect()


def _invalid_request_redirect(request: Request) -> RedirectResponse:
    log_invalid_request_link(request)
    return _login_redirect()


def _resolve_transfer(transfer: Transfer | None, request: Request) -> Transfer | RedirectResponse:
    if transfer is None:
        return _invalid_transfer_redirect(request)
    return transfer


def _resolve_request(file_request: FileRequest | None, request: Request) -> FileRequest | RedirectResponse:
    if file_request is None:
        return _invalid_request_redirect(request)
    return file_request


def _request_staging_scope(token: str) -> str:
    return f"request_{token}"


def _require_request_unlock(request: Request, token: str, *, password_required: bool) -> None:
    if not is_request_unlocked(request, token, password_required=password_required):
        raise HTTPException(status_code=403, detail=_("Unlock this file request before uploading"))


def _require_download_grant(request: Request, token: str) -> None:
    if not has_transfer_download_grant(request.session, token):
        raise HTTPException(
            status_code=403,
            detail=_("Open the transfer page in this browser before downloading."),
        )


def _download_limit_warning(transfer: Transfer) -> str | None:
    if transfer.max_downloads <= 0:
        return None
    return ngettext(
        "This transfer allows %(max)s download. Unlocking uses one download; "
        "you can then download all files in this browser session.",
        "This transfer allows %(max)s downloads. Unlocking uses one download; "
        "you can then download all files in this browser session.",
        transfer.max_downloads,
    ) % {"max": transfer.max_downloads}


def _access_blocked_copy(issue: str, *, kind: str = "transfer") -> dict[str, str]:
    if issue == ACCESS_DOWNLOAD_LIMIT:
        return {
            "access_blocked_title": _("Download limit reached"),
            "access_blocked_message": _(
                "All available downloads for this transfer have been used. Contact the person who shared these files if you still need them."
            ),
        }
    if issue == ACCESS_UPLOAD_LIMIT:
        return {
            "access_blocked_title": _("Upload limit reached"),
            "access_blocked_message": _(
                "All available uploads for this request have been used. Contact the person who requested these files if you still need to upload."
            ),
        }
    if issue == ACCESS_EXPIRED:
        if kind == "request":
            return {
                "access_blocked_title": _("This link has expired"),
                "access_blocked_message": _(
                    "This file request is no longer available because it has expired."
                ),
            }
        return {
            "access_blocked_title": _("This link has expired"),
            "access_blocked_message": _(
                "This transfer is no longer available because it has expired."
            ),
        }
    if issue == ACCESS_DISABLED:
        if kind == "request":
            return {
                "access_blocked_title": _("This link has been disabled"),
                "access_blocked_message": _("This file request is no longer available."),
            }
        return {
            "access_blocked_title": _("This link has been disabled"),
            "access_blocked_message": _("This transfer is no longer available."),
        }
    return {
        "access_blocked_title": _("Unavailable"),
        "access_blocked_message": _("This link is no longer available."),
    }


def _access_blocked_status(issue: str) -> int:
    if issue == ACCESS_DISABLED:
        return 403
    return 410


def _download_page_context(
    request: Request,
    transfer: Transfer,
    *,
    error: str | None = None,
    access_issue: str | None = None,
) -> dict:
    token = transfer.public_token
    password_required = bool(transfer.password_hash)
    unlocked = is_transfer_unlocked(request, token, password_required=password_required)
    has_grant = has_transfer_download_grant(request.session, token)
    access_blocked = access_issue is not None
    needs_password = not access_blocked and password_required and not unlocked and not has_grant
    needs_unlock = not access_blocked and not has_grant and not needs_password
    can_download = not access_blocked and has_grant
    limit_warning = _download_limit_warning(transfer) if not has_grant and not access_blocked else None
    ctx: dict = {
        "transfer": transfer,
        "needs_password": needs_password,
        "needs_unlock": needs_unlock,
        "can_download": can_download,
        "limit_warning": limit_warning,
        "error": error,
        "access_blocked": access_blocked,
    }
    if access_blocked:
        ctx.update(_access_blocked_copy(access_issue, kind="transfer"))
    return ctx


def _upload_page_context(
    request: Request,
    file_request: FileRequest,
    token: str,
    *,
    error: str | None = None,
    access_issue: str | None = None,
    success: str | None = None,
) -> dict:
    password_required = bool(file_request.password_hash)
    access_blocked = access_issue is not None
    needs_password = (
        not access_blocked
        and password_required
        and not is_request_unlocked(request, token, password_required=password_required)
    )
    ctx: dict = {
        "file_request": file_request,
        "needs_password": needs_password,
        "error": error,
        "success": success,
        "access_blocked": access_blocked,
    }
    if access_blocked:
        ctx.update(_access_blocked_copy(access_issue, kind="request"))
    return ctx


def _render_upload_page(
    request: Request,
    file_request: FileRequest,
    token: str,
    app_settings,
    *,
    status_code: int | None = None,
    **context_kwargs,
) -> HTMLResponse:
    issue = request_access_issue(file_request)
    if issue:
        context_kwargs["access_issue"] = issue
        if status_code is None:
            status_code = _access_blocked_status(issue)
    elif status_code is None:
        status_code = 200
    ctx = branding_context(app_settings)
    ctx.update(_upload_page_context(request, file_request, token, **context_kwargs))
    return templates.TemplateResponse(
        request,
        "public_upload.html",
        ctx,
        status_code=status_code,
    )


def _render_download_page(
    request: Request,
    transfer: Transfer,
    app_settings,
    *,
    status_code: int | None = None,
    **context_kwargs,
) -> HTMLResponse:
    issue = transfer_access_issue(
        transfer,
        session=request.session,
        public_token=transfer.public_token,
    )
    if issue:
        context_kwargs["access_issue"] = issue
        if status_code is None:
            status_code = _access_blocked_status(issue)
    elif status_code is None:
        status_code = 200
    ctx = branding_context(app_settings)
    ctx.update(_download_page_context(request, transfer, **context_kwargs))
    return templates.TemplateResponse(
        request,
        "public_download.html",
        ctx,
        status_code=status_code,
    )


async def _grant_transfer_session(
    request: Request,
    db: AsyncSession,
    transfer: Transfer,
) -> bool:
    token = transfer.public_token
    if has_transfer_download_grant(request.session, token):
        return True
    if not mark_transfer_download_counted(request.session, token):
        grant_transfer_download(request.session, token)
        return True

    if not await try_reserve_download_slot(db, transfer.id, max_downloads=transfer.max_downloads):
        return False

    await db.refresh(transfer)
    grant_transfer_download(request.session, token)
    return True


async def _handle_download_event(
    request: Request,
    db: AsyncSession,
    transfer: Transfer,
    app_settings,
    creator: User | None,
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
    if mark_transfer_download_notified(request.session, transfer.public_token):
        await db.refresh(transfer)
        await record_download(db, transfer, app_settings, creator)


@router.get("/d/{token}", response_class=HTMLResponse)
@limiter.limit("30/minute")
async def download_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    transfer = await lookup_transfer_by_token(db, token)
    resolved = _resolve_transfer(transfer, request)
    if isinstance(resolved, RedirectResponse):
        return resolved
    transfer = resolved
    return _render_download_page(request, transfer, app_settings)


@router.post("/d/{token}", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def download_unlock(token: str, request: Request, password: str = Form(""), db: AsyncSession = Depends(get_db)):
    app_settings = await get_app_settings(db)
    transfer = await lookup_transfer_by_token(db, token)
    resolved = _resolve_transfer(transfer, request)
    if isinstance(resolved, RedirectResponse):
        return resolved
    transfer = resolved
    issue = transfer_access_issue(transfer)
    if issue:
        return _render_download_page(request, transfer, app_settings, access_issue=issue)
    lockout_error = await _unlock_lockout_error("transfer", token)
    if lockout_error:
        return _render_download_page(
            request,
            transfer,
            app_settings,
            status_code=429,
            error=lockout_error,
        )
    if not verify_transfer_password(transfer, password):
        await record_failed_unlock("transfer", token)
        log_invalid_unlock(request, "transfer")
        return _render_download_page(
            request,
            transfer,
            app_settings,
            status_code=401,
            error=_("Invalid password"),
        )

    await reset_unlock_lockout("transfer", token)

    password_required = bool(transfer.password_hash)
    granted = await _grant_transfer_session(
        request,
        db,
        transfer,
    )
    if not granted:
        return _render_download_page(
            request,
            transfer,
            app_settings,
            access_issue=ACCESS_DOWNLOAD_LIMIT,
        )

    response = RedirectResponse(f"/d/{token}", status_code=303)
    if password_required:
        set_transfer_unlock(response, token)
    return response


@router.get("/d/{token}/download")
@limiter.limit("30/minute")
async def download_files_zip(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_download_grant(request, token)
    app_settings = await get_app_settings(db)
    transfer = await lookup_transfer_by_token(db, token)
    resolved = _resolve_transfer(transfer, request)
    if isinstance(resolved, RedirectResponse):
        return resolved
    transfer = resolved
    issue = transfer_access_issue(
        transfer,
        session=request.session,
        public_token=token,
    )
    if issue:
        return _render_download_page(request, transfer, app_settings, access_issue=issue)
    if not is_transfer_unlocked(request, token, password_required=bool(transfer.password_hash)):
        raise HTTPException(status_code=403, detail=_("Password required"))

    creator = await db.get(User, transfer.created_by)
    await _handle_download_event(
        request,
        db,
        transfer,
        app_settings,
        creator,
        download_type="zip",
        file_name=f"{transfer.title or 'transfer'}.zip",
    )
    entries = transfer_zip_entries(transfer)
    filename = f"{transfer.title or 'transfer'}.zip".replace("/", "-").replace('"', "")
    return StreamingResponse(
        stream_zip(entries),
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
    resolved = _resolve_transfer(transfer, request)
    if isinstance(resolved, RedirectResponse):
        return resolved
    transfer = resolved
    issue = transfer_access_issue(
        transfer,
        session=request.session,
        public_token=token,
    )
    if issue:
        return _render_download_page(request, transfer, app_settings, access_issue=issue)
    if not is_transfer_unlocked(request, token, password_required=bool(transfer.password_hash)):
        raise HTTPException(status_code=403, detail=_("Password required"))
    transfer_file = await get_transfer_file(transfer, file_id)
    creator = await db.get(User, transfer.created_by)

    await _handle_download_event(
        request,
        db,
        transfer,
        app_settings,
        creator,
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
    resolved = _resolve_request(file_request, request)
    if isinstance(resolved, RedirectResponse):
        return resolved
    file_request = resolved
    return _render_upload_page(request, file_request, token, app_settings)


@router.post("/r/{token}/staging")
@limiter.limit("60/minute")
async def stage_request_file(
    token: str,
    request: Request,
    file: UploadFile = File(...),
):
    async with async_session() as db:
        app_settings = await get_app_settings(db)
        file_request = await lookup_request_by_token(db, token)
        resolved = _resolve_request(file_request, request)
        if isinstance(resolved, RedirectResponse):
            return resolved
        file_request = resolved
        issue = request_access_issue(file_request)
        if issue:
            raise HTTPException(
                status_code=_access_blocked_status(issue),
                detail=_access_blocked_copy(issue, kind="request")["access_blocked_title"],
            )
        limits = StagingLimits.from_settings(app_settings)
        max_total_bytes = file_request.max_total_bytes
        scope = _request_staging_scope(token)
    _require_request_unlock(request, token, password_required=bool(file_request.password_hash))
    staged = await add_staged_file(
        scope,
        file,
        limits,
        max_total_bytes=max_total_bytes,
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
    resolved = _resolve_request(file_request, request)
    if isinstance(resolved, RedirectResponse):
        return resolved
    file_request = resolved
    issue = request_access_issue(file_request)
    if issue:
        raise HTTPException(
            status_code=_access_blocked_status(issue),
            detail=_access_blocked_copy(issue, kind="request")["access_blocked_title"],
        )
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
    resolved = _resolve_request(file_request, request)
    if isinstance(resolved, RedirectResponse):
        return resolved
    file_request = resolved
    issue = request_access_issue(file_request)
    if issue:
        return _render_upload_page(request, file_request, token, app_settings, access_issue=issue)

    if unlock:
        lockout_error = await _unlock_lockout_error("request", token)
        if lockout_error:
            return _render_upload_page(
                request,
                file_request,
                token,
                app_settings,
                status_code=429,
                error=lockout_error,
            )
        if not verify_request_password(file_request, password):
            await record_failed_unlock("request", token)
            log_invalid_unlock(request, "request")
            return _render_upload_page(
                request,
                file_request,
                token,
                app_settings,
                status_code=401,
                error=_("Invalid password"),
            )
        await reset_unlock_lockout("request", token)
        response = RedirectResponse(f"/r/{token}", status_code=303)
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
        return _render_upload_page(
            request,
            file_request,
            token,
            app_settings,
            status_code=exc.status_code,
            error=exc.detail if isinstance(exc.detail, str) else _("Upload failed"),
        )
    await discard_staged_paths(staged_files)

    return _render_upload_page(
        request,
        file_request,
        token,
        app_settings,
        success=_("Upload successful. Thank you!"),
    )
