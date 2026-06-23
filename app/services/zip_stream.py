from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Iterator

CHUNK_SIZE = 1024 * 1024


def stream_zip(entries: list[tuple[Path, str]]) -> Iterator[bytes]:
    """Build a ZIP archive on disk and stream it in chunks.

    The archive is written to a temporary file (bounded by disk, not RAM) and
    yielded incrementally, then removed. This avoids holding the entire archive
    — potentially many gigabytes — in process memory.
    """
    tmp = tempfile.NamedTemporaryFile(prefix="owntransfer-zip-", suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for src, arcname in entries:
                zf.write(src, arcname=arcname)
        tmp.flush()
        tmp.close()
        with open(tmp_path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    finally:
        try:
            tmp.close()
        except Exception:
            pass
        try:
            tmp_path.unlink()
        except OSError:
            pass
