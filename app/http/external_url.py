from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from app.config.settings import settings


def effective_public_scheme() -> str:
    if settings.public_scheme:
        return settings.public_scheme
    return urlparse(settings.base_url).scheme or "http"


def external_url(path: str) -> str:
    """Build a public-facing URL using BASE_URL host and PUBLIC_SCHEME."""
    parsed = urlparse(settings.base_url)
    normalized = path if path.startswith("/") else f"/{path}"
    return urlunparse((effective_public_scheme(), parsed.netloc, normalized, "", "", ""))
