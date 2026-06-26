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


def safe_next_path(request: Request, candidate: str | None, *, fallback: str = "/") -> str:
    """Return a same-origin relative path for post-login redirects."""
    if not candidate:
        return fallback

    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return fallback

    path = candidate if candidate.startswith("/") else f"/{candidate}"
    if path.startswith("//"):
        return fallback
    if path.startswith("/auth/login"):
        return fallback
    return path
