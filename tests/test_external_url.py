from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from app.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_external_url_uses_base_url_host(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://transfer.example.com")
    monkeypatch.delenv("PUBLIC_SCHEME", raising=False)

    from app.http.external_url import external_url

    assert external_url("/auth/oauth/entra/callback") == "https://transfer.example.com/auth/oauth/entra/callback"


def test_external_url_uses_public_scheme_override(monkeypatch):
    monkeypatch.setenv("BASE_URL", "http://transfer.example.com")
    monkeypatch.setenv("PUBLIC_SCHEME", "https")

    from app.http.external_url import external_url

    assert external_url("/auth/oauth/entra/callback") == "https://transfer.example.com/auth/oauth/entra/callback"


def test_public_scheme_validator_rejects_invalid(monkeypatch):
    monkeypatch.setenv("PUBLIC_SCHEME", "ftp")

    from app.config.settings import Settings

    with pytest.raises(ValueError, match="PUBLIC_SCHEME must be http or https"):
        Settings()
