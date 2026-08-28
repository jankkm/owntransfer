from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.file_request import (
    effective_request_max_total_bytes,
    ensure_max_total_within_limit,
)


def test_effective_request_max_total_bytes_uses_lower_of_request_and_system():
    req = SimpleNamespace(max_total_bytes=500 * 1024 * 1024)
    app_settings = SimpleNamespace(max_file_size_bytes=10 * 1024 * 1024)
    assert effective_request_max_total_bytes(req, app_settings) == 10 * 1024 * 1024


def test_effective_request_max_total_bytes_allows_request_below_system():
    req = SimpleNamespace(max_total_bytes=5 * 1024 * 1024)
    app_settings = SimpleNamespace(max_file_size_bytes=10 * 1024 * 1024)
    assert effective_request_max_total_bytes(req, app_settings) == 5 * 1024 * 1024


def test_ensure_max_total_within_limit_rejects_above_system():
    app_settings = SimpleNamespace(max_file_size_bytes=10 * 1024 * 1024)
    with pytest.raises(HTTPException) as exc:
        ensure_max_total_within_limit(11 * 1024 * 1024, app_settings)
    assert exc.value.status_code == 400
    assert "10 MB" in exc.value.detail


def test_ensure_max_total_within_limit_allows_at_system_limit():
    app_settings = SimpleNamespace(max_file_size_bytes=10 * 1024 * 1024)
    ensure_max_total_within_limit(10 * 1024 * 1024, app_settings)
