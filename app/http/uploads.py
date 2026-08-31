from __future__ import annotations

import re
from urllib.parse import unquote_to_bytes

from fastapi import UploadFile

RAW_UPLOAD_FILENAME_HEADER = "x-upload-filename"
MAX_UPLOAD_FILENAME_BYTES = 255


def normalize_upload_files(files: UploadFile | list[UploadFile] | None) -> list[UploadFile]:
    if files is None:
        return []
    if isinstance(files, list):
        return files
    return [files]


def decode_raw_upload_filename(value: str | None) -> str:
    if not value or re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("Invalid encoded filename")
    try:
        raw = unquote_to_bytes(value)
        filename = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid encoded filename") from exc
    if not filename or len(raw) > MAX_UPLOAD_FILENAME_BYTES:
        raise ValueError("Invalid encoded filename")
    return filename
