from __future__ import annotations

import errno
import os
import queue
import threading
import zipfile
from pathlib import Path
from typing import Iterator

CHUNK_SIZE = 1024 * 1024


def _is_broken_pipe_error(exc: BaseException) -> bool:
    if isinstance(exc, BrokenPipeError):
        return True
    return isinstance(exc, OSError) and exc.errno in (errno.EPIPE, errno.ECONNRESET)


def stream_zip(entries: list[tuple[Path, str]]) -> Iterator[bytes]:
    """Build a ZIP archive on the fly and stream it as it is being built.

    A background thread compresses files and writes the ZIP data into one
    end of a pipe. The generator reads from the other end, yielding chunks
    as soon as they become available — the HTTP response starts streaming
    almost immediately instead of waiting for the whole archive.

    Memory usage stays low because data flows chunk-by-chunk:
    disk → compression → pipe buffer (~64 KB) → network.
    """
    read_fd, write_fd = os.pipe()
    err_queue: queue.Queue[BaseException | None] = queue.Queue()

    def _build() -> None:
        try:
            with os.fdopen(write_fd, "wb") as pipe_writer:
                with zipfile.ZipFile(pipe_writer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for src, arcname in entries:
                        zf.write(src, arcname=arcname)
        except BaseException as exc:
            err_queue.put(exc)
        else:
            err_queue.put(None)

    thread = threading.Thread(target=_build, daemon=True)
    thread.start()
    completed = False

    try:
        with os.fdopen(read_fd, "rb") as pipe_reader:
            while True:
                chunk = pipe_reader.read(CHUNK_SIZE)
                if not chunk:
                    completed = True
                    break
                yield chunk
    finally:
        # Closing the read end unblocks a writer stuck on a full pipe.
        try:
            os.close(read_fd)
        except OSError:
            pass

        thread.join()

        try:
            exc = err_queue.get_nowait()
        except queue.Empty:
            exc = None

        # Ignore broken-pipe errors when the consumer stopped early.
        if exc and (completed or not _is_broken_pipe_error(exc)):
            if isinstance(exc, OSError):
                raise exc
            raise RuntimeError("ZIP build failed") from exc
