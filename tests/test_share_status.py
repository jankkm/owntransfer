from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models import FileRequest, Transfer
from app.services.share_status import (
    file_request_is_accessible,
    file_request_is_active,
    file_request_is_enabled,
    file_request_is_expired,
    transfer_is_accessible,
    transfer_is_active,
    transfer_is_enabled,
    transfer_is_expired,
    transfer_inactive_reason_code,
)


def _now() -> datetime:
    return datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)


def _transfer(*, expires_offset: int = 7, is_disabled: bool = False, is_expired: bool = False) -> Transfer:
    now = _now()
    return Transfer(
        public_token="token",
        created_by=uuid.uuid4(),
        title="Test",
        created_at=now,
        expires_at=now + timedelta(days=expires_offset),
        is_disabled=is_disabled,
        is_expired=is_expired,
        download_count=0,
        max_downloads=10,
    )


def test_enabled_expired_transfer_stays_active_but_not_accessible():
    transfer = _transfer(expires_offset=-1, is_expired=True)

    assert transfer_is_enabled(transfer) is True
    assert transfer_is_active(transfer, _now()) is True
    assert transfer_is_expired(transfer, _now()) is True
    assert transfer_is_accessible(transfer, _now()) is False
    assert transfer_inactive_reason_code(transfer, _now()) == "expired"


def test_disabled_transfer_is_not_active():
    transfer = _transfer(is_disabled=True)

    assert transfer_is_enabled(transfer) is False
    assert transfer_is_active(transfer, _now()) is False
    assert transfer_is_accessible(transfer, _now()) is False
    assert transfer_inactive_reason_code(transfer, _now()) == "disabled"


def test_file_request_enabled_expired_same_rules():
    now = _now()
    req = FileRequest(
        public_token="token",
        created_by=uuid.uuid4(),
        title="Test",
        created_at=now,
        expires_at=now - timedelta(days=1),
        is_disabled=False,
        is_expired=True,
        upload_count=0,
        max_uploads=5,
        max_total_bytes=1_000,
    )

    assert file_request_is_enabled(req) is True
    assert file_request_is_active(req, now) is True
    assert file_request_is_expired(req, now) is True
    assert file_request_is_accessible(req, now) is False
