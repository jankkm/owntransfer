from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.file_request import (
    effective_request_max_total_bytes,
    ensure_max_total_is_positive,
)


def test_effective_request_max_total_bytes_is_independent_of_per_file_limit():
    req = SimpleNamespace(max_total_bytes=500 * 1024 * 1024)
    app_settings = SimpleNamespace(max_file_size_bytes=10 * 1024 * 1024)
    assert effective_request_max_total_bytes(req, app_settings) == 500 * 1024 * 1024


def test_effective_request_max_total_bytes_allows_small_request_total():
    req = SimpleNamespace(max_total_bytes=5 * 1024 * 1024)
    app_settings = SimpleNamespace(max_file_size_bytes=10 * 1024 * 1024)
    assert effective_request_max_total_bytes(req, app_settings) == 5 * 1024 * 1024


def test_ensure_max_total_is_positive_rejects_zero():
    with pytest.raises(HTTPException) as exc:
        ensure_max_total_is_positive(0)
    assert exc.value.status_code == 400
    assert "greater than zero" in exc.value.detail


def test_ensure_max_total_is_positive_allows_above_per_file_limit():
    ensure_max_total_is_positive(500 * 1024 * 1024)
