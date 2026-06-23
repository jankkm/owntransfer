from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, BinaryIO

from app.services.storage.local import StorageBackend


class S3Storage(StorageBackend):
    """Placeholder for future S3/MinIO integration in Kubernetes."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        self.endpoint = endpoint
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        raise NotImplementedError("S3 storage is not implemented yet; use LocalStorage")

    async def save_file(self, relative_path: str, data: bytes) -> str:
        raise NotImplementedError

    async def save_stream(self, relative_path: str, stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
        raise NotImplementedError

    async def open_file(self, relative_path: str) -> AsyncIterator[bytes]:
        raise NotImplementedError
        yield b""

    async def delete_file(self, relative_path: str) -> None:
        raise NotImplementedError

    async def delete_directory(self, relative_path: str) -> None:
        raise NotImplementedError

    def absolute_path(self, relative_path: str) -> Path:
        raise NotImplementedError
