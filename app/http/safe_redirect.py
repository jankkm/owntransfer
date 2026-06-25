from __future__ import annotations

from urllib.parse import urlparse

from starlette.requests import Request


def safe_redirect_target(request: Request, *, fallback: str = "/") -> str:
    """Return a same-origin path from Referer, or fallback for external/missing values."""
    referer = request.headers.get("referer")
    if not referer:
        return fallback

    ref = urlparse(referer)
    base = urlparse(str(request.base_url))
    if ref.scheme != base.scheme or ref.netloc != base.netloc:
        return fallback

    path = ref.path or "/"
    if ref.query:
        path = f"{path}?{ref.query}"
    return path
