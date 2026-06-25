from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.middleware.security_headers import security_headers


def test_security_headers_include_baseline():
    headers = security_headers(hsts=False)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in headers


def test_security_headers_include_hsts_when_enabled():
    headers = security_headers(hsts=True)
    assert headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


@pytest.mark.asyncio
async def test_html_responses_include_security_headers(client: AsyncClient):
    response = await client.get("/auth/login")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_logo_endpoint_keeps_restrictive_csp(client: AsyncClient):
    response = await client.get("/branding/logo")
    assert response.status_code == 307
    assert response.headers["location"] == "/static/logo.svg"

    static = await client.get("/static/logo.svg")
    assert static.status_code == 200
    assert static.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in static.headers["content-security-policy"]
