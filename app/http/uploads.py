from __future__ import annotations

import json
import re
import secrets
import uuid
from urllib.parse import unquote_to_bytes

from fastapi import UploadFile

RAW_UPLOAD_FILENAME_HEADER = "x-upload-filename"
UPLOAD_BATCH_HEADER = "x-upload-batch"
MAX_UPLOAD_FILENAME_BYTES = 255
MAX_STAGED_FILES_PER_BATCH = 1000
_UPLOAD_BATCH_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")


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


def new_upload_batch() -> str:
    return secrets.token_urlsafe(24)


def validate_upload_batch(value: str | None) -> str:
    if not value or not _UPLOAD_BATCH_RE.fullmatch(value):
        raise ValueError("Invalid upload batch")
    return value


def decode_staged_file_ids(value: str) -> list[str]:
    try:
        raw_ids = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid staged file selection") from exc
    if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= MAX_STAGED_FILES_PER_BATCH:
        raise ValueError("Invalid staged file selection")

    file_ids: list[str] = []
    for value in raw_ids:
        if not isinstance(value, str):
            raise ValueError("Invalid staged file selection")
        try:
            file_id = str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("Invalid staged file selection") from exc
        file_ids.append(file_id)

    if len(set(file_ids)) != len(file_ids):
        raise ValueError("Invalid staged file selection")
    return file_ids
