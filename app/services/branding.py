from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.i18n import _
from app.models import AppSettings
from app.services.svg_sanitize import sanitize_svg

ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
MAX_LOGO_BYTES = 2 * 1024 * 1024
DEFAULT_LOGO_URL = "/static/logo.svg"
CUSTOM_LOGO_URL = "/branding/logo"

_EXTENSION_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_hex_color(value: str) -> str | None:
    normalized = value.strip()
    if not _HEX_COLOR.fullmatch(normalized):
        return None
    return normalized.lower()


def has_custom_logo(app_settings: AppSettings) -> bool:
    return bool(app_settings.logo_data)


def logo_url(app_settings: AppSettings) -> str:
    if has_custom_logo(app_settings):
        version = hashlib.sha256(app_settings.logo_data).hexdigest()[:12]
        return f"{CUSTOM_LOGO_URL}?v={version}"
    return DEFAULT_LOGO_URL


def favicon_type(app_settings: AppSettings) -> str:
    if has_custom_logo(app_settings):
        content_type = app_settings.logo_content_type or ""
        if content_type.startswith("image/"):
            return content_type
        return "image/png"
    return "image/svg+xml"


def _validate_logo(upload: UploadFile) -> str:
    if not upload.filename:
        raise HTTPException(status_code=400, detail=_("No file selected"))
    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=_("Unsupported logo format. Allowed: %(extensions)s")
            % {"extensions": ", ".join(sorted(ALLOWED_LOGO_EXTENSIONS))},
        )
    return ext


async def apply_logo_upload(app_settings: AppSettings, upload: UploadFile) -> None:
    ext = _validate_logo(upload)
    content = await upload.read()
    if len(content) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail=_("Logo must be 2 MB or smaller"))
    if not content:
        raise HTTPException(status_code=400, detail=_("Uploaded logo file is empty"))

    if ext == ".svg":
        content = sanitize_svg(content)

    content_type = _EXTENSION_TO_MIME.get(ext) or mimetypes.guess_type(upload.filename)[0]
    app_settings.logo_data = content
    app_settings.logo_content_type = content_type or "application/octet-stream"


def clear_logo(app_settings: AppSettings) -> None:
    app_settings.logo_data = None
    app_settings.logo_content_type = None
