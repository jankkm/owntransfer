from __future__ import annotations

import asyncio
import fcntl
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from fastapi import HTTPException, UploadFile

from app.i18n import _
from app.models import AppSettings
from app.services.settings import is_extension_blocked, parse_blocklist
from app.services.storage import get_storage

MANIFEST_FILENAME = "manifest.json"
_CHUNK_SIZE = 1024 * 1024
T = TypeVar("T")


@dataclass
class StagedFile:
    id: str
    original_name: str
    storage_path: str
    size_bytes: int
    content_type: str | None


@dataclass(frozen=True)
class StagingLimits:
    max_file_size_bytes: int
    file_type_blocklist: str | None = None

    @classmethod
    def from_settings(cls, app_settings: AppSettings) -> StagingLimits:
        return cls(
            max_file_size_bytes=app_settings.max_file_size_bytes,
            file_type_blocklist=app_settings.file_type_blocklist,
        )


def _safe_filename(name: str) -> str:
    base = name.replace("\\", "/").split("/")[-1].strip()
    return re.sub(r"[^\w.\- ()]", "_", base) or "file"


def _manifest_rel_path(scope: str) -> str:
    return f"staging/{scope}/{MANIFEST_FILENAME}"


def _lock_path(scope: str) -> Path:
    storage = get_storage()
    path = storage.absolute_path(f"staging/{scope}/.manifest.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_manifest(scope: str) -> list[StagedFile]:
    storage = get_storage()
    path = storage.absolute_path(_manifest_rel_path(scope))
    if not path.exists():
        return []
    return _deserialize(path.read_bytes())


def _deserialize(raw: bytes) -> list[StagedFile]:
    if not raw:
        return []
    return [StagedFile(**item) for item in json.loads(raw.decode("utf-8"))]


def _serialize(files: list[StagedFile]) -> bytes:
    payload = [
        {
            "id": f.id,
            "original_name": f.original_name,
            "storage_path": f.storage_path,
            "size_bytes": f.size_bytes,
            "content_type": f.content_type,
        }
        for f in files
    ]
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _write_manifest(scope: str, files: list[StagedFile]) -> None:
    storage = get_storage()
    path = storage.absolute_path(_manifest_rel_path(scope))
    path.parent.mkdir(parents=True, exist_ok=True)
    if files:
        path.write_bytes(_serialize(files))
    elif path.exists():
        path.unlink()


def _with_manifest_lock_sync(
    scope: str,
    fn: Callable[[list[StagedFile]], tuple[list[StagedFile], T]],
    *,
    exclusive: bool = True,
) -> T:
    lock_path = _lock_path(scope)
    with open(lock_path, "a+b") as lock_file:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
        )
        try:
            staged = _read_manifest(scope)
            new_staged, result = fn(staged)
            if exclusive:
                _write_manifest(scope, new_staged)
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def _with_manifest_lock(
    scope: str,
    fn: Callable[[list[StagedFile]], tuple[list[StagedFile], T]],
) -> T:
    return await asyncio.to_thread(_with_manifest_lock_sync, scope, fn, exclusive=True)


def get_staged_files(scope: str) -> list[StagedFile]:
    return _with_manifest_lock_sync(
        scope,
        lambda staged: (staged, list(staged)),
        exclusive=False,
    )


class _StagingUploadError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail


def _save_upload_sync(relative_path: str, upload: UploadFile, max_file_size_bytes: int) -> int:
    storage = get_storage()
    path = storage.absolute_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    source = upload.file
    if source is None:
        raise _StagingUploadError(400, _("Missing filename"))
    source.seek(0)
    size = 0
    try:
        with open(path, "wb") as out:
            while True:
                chunk = source.read(_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_file_size_bytes:
                    raise _StagingUploadError(
                        400,
                        _("File exceeds maximum size (%(max_mb)s MB)")
                        % {"max_mb": max_file_size_bytes // (1024 * 1024)},
                    )
                out.write(chunk)
    except _StagingUploadError:
        if path.exists():
            path.unlink()
        raise
    except OSError as exc:
        if path.exists():
            path.unlink()
        raise _StagingUploadError(500, _("Could not save uploaded file")) from exc

    if size == 0:
        if path.exists():
            path.unlink()
        raise _StagingUploadError(400, _("Empty file"))
    return size


async def _save_upload(relative_path: str, upload: UploadFile, max_file_size_bytes: int) -> int:
    try:
        return await asyncio.to_thread(_save_upload_sync, relative_path, upload, max_file_size_bytes)
    except _StagingUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def add_staged_file(
    scope: str,
    upload: UploadFile,
    limits: StagingLimits | AppSettings,
    *,
    max_total_bytes: int | None = None,
) -> StagedFile:
    if not isinstance(limits, StagingLimits):
        limits = StagingLimits.from_settings(limits)

    if not upload.filename:
        raise HTTPException(status_code=400, detail=_("Missing filename"))

    blocklist = parse_blocklist(limits.file_type_blocklist)
    if is_extension_blocked(upload.filename, blocklist):
        raise HTTPException(status_code=400, detail=_("File type not allowed: %(filename)s") % {"filename": upload.filename})

    file_id = str(uuid.uuid4())
    safe_name = _safe_filename(upload.filename)
    storage_path = f"staging/{scope}/{file_id}/{safe_name}"
    storage = get_storage()
    size_bytes = await _save_upload(storage_path, upload, limits.max_file_size_bytes)

    total_limit = max_total_bytes if max_total_bytes is not None else limits.max_file_size_bytes

    def updater(staged: list[StagedFile]) -> tuple[list[StagedFile], StagedFile]:
        total_size = sum(f.size_bytes for f in staged) + size_bytes
        if total_size > total_limit:
            raise HTTPException(status_code=400, detail=_("Total upload exceeds maximum allowed size"))

        staged_file = StagedFile(
            id=file_id,
            original_name=upload.filename,
            storage_path=storage_path,
            size_bytes=size_bytes,
            content_type=upload.content_type,
        )
        staged.append(staged_file)
        return staged, staged_file

    try:
        return await _with_manifest_lock(scope, updater)
    except HTTPException:
        await storage.delete_file(storage_path)
        raise


async def remove_staged_file(scope: str, file_id: str) -> None:
    def updater(staged: list[StagedFile]) -> tuple[list[StagedFile], StagedFile]:
        match = next((f for f in staged if f.id == file_id), None)
        if not match:
            raise HTTPException(status_code=404, detail=_("Staged file not found"))
        return [f for f in staged if f.id != file_id], match

    removed = await _with_manifest_lock(scope, updater)
    storage = get_storage()
    await storage.delete_file(removed.storage_path)


async def clear_staged_files(scope: str) -> None:
    staged_files = await take_staged_files(scope)
    await discard_staged_paths(staged_files)


async def take_staged_files(scope: str) -> list[StagedFile]:
    return await _with_manifest_lock(scope, lambda staged: ([], list(staged)))


async def restore_staged_files(scope: str, files: list[StagedFile]) -> None:
    if not files:
        return

    def updater(staged: list[StagedFile]) -> tuple[list[StagedFile], None]:
        existing_ids = {f.id for f in staged}
        merged = staged + [f for f in files if f.id not in existing_ids]
        return merged, None

    await _with_manifest_lock(scope, updater)


async def discard_staged_paths(files: list[StagedFile]) -> None:
    if not files:
        return

    storage = get_storage()
    scopes: set[str] = set()
    for staged in files:
        parts = staged.storage_path.split("/")
        if len(parts) >= 3 and parts[0] == "staging":
            scopes.add(parts[1])
        await storage.delete_file(staged.storage_path)

    for scope in scopes:
        await storage.delete_directory(f"staging/{scope}")
