from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.services.zip_stream import stream_zip


def _collect(entries: list[tuple[Path, str]]) -> bytes:
    return b"".join(stream_zip(entries))


def test_stream_zip_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.txt").write_text("world")

    data = _collect(
        [
            (tmp_path / "a.txt", "a.txt"),
            (tmp_path / "nested" / "b.txt", "nested/b.txt"),
        ]
    )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.read("a.txt") == b"hello"
        assert zf.read("nested/b.txt") == b"world"


def test_stream_zip_empty() -> None:
    data = _collect([])
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == []


def test_stream_zip_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _collect([(tmp_path / "missing.txt", "missing.txt")])


def test_stream_zip_consumer_disconnect(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))

    gen = stream_zip([(tmp_path / "big.bin", "big.bin")])
    next(gen)
    gen.close()
