from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator, BinaryIO


class StorageBackend(ABC):
    @abstractmethod
    async def save_file(self, relative_path: str, data: bytes) -> str:
        ...

    @abstractmethod
    async def save_stream(self, relative_path: str, stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
        ...

    @abstractmethod
    async def open_file(self, relative_path: str) -> AsyncIterator[bytes]:
        ...

    @abstractmethod
    async def delete_file(self, relative_path: str) -> None:
        ...

    @abstractmethod
    async def delete_directory(self, relative_path: str) -> None:
        ...

    @abstractmethod
    def absolute_path(self, relative_path: str) -> Path:
        ...


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        base = self.base_dir.resolve()
        full = (base / relative_path).resolve()
        # Use proper path containment (not string prefix) so that sibling
        # directories sharing a name prefix (e.g. /data/uploads vs
        # /data/uploads_evil) cannot be reached via ".." traversal.
        if full != base and base not in full.parents:
            raise ValueError("Invalid storage path")
        return full

    async def save_file(self, relative_path: str, data: bytes) -> str:
        path = self._resolve(relative_path)
        await asyncio.to_thread(self._write_bytes, path, data)
        return relative_path

    @staticmethod
    def _write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def save_stream(self, relative_path: str, stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
        path = self._resolve(relative_path)
        await asyncio.to_thread(self._write_stream, path, stream, chunk_size)
        return relative_path

    @staticmethod
    def _write_stream(path: Path, stream: BinaryIO, chunk_size: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)

    async def open_file(self, relative_path: str) -> AsyncIterator[bytes]:
        import aiofiles

        path = self._resolve(relative_path)
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    async def delete_file(self, relative_path: str) -> None:
        path = self._resolve(relative_path)
        if path.exists():
            path.unlink()

    async def delete_directory(self, relative_path: str) -> None:
        import shutil

        path = self._resolve(relative_path)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def absolute_path(self, relative_path: str) -> Path:
        return self._resolve(relative_path)


def get_storage() -> StorageBackend:
    from app.config import settings

    return LocalStorage(settings.upload_dir)
