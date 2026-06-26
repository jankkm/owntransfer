from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.models import FileRequest, Transfer
from app.services.share_list import (
    DEFAULT_SORT,
    apply_request_list_query,
    apply_transfer_list_query,
    parse_share_list_query,
)


def _now() -> datetime:
    return datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc)


def _transfer(*, title: str, message: str = "", created_offset: int = 0, expires_offset: int = 7) -> Transfer:
    now = _now()
    transfer = Transfer(
        public_token=f"token-{title}",
        created_by=uuid.uuid4(),
        title=title,
        message=message,
        created_at=now + timedelta(days=created_offset),
        expires_at=now + timedelta(days=expires_offset),
        is_disabled=False,
        is_expired=False,
        download_count=0,
        max_downloads=10,
    )
    transfer.files = []
    return transfer


def _request(*, title: str, instructions: str = "", created_offset: int = 0, expires_offset: int = 7) -> FileRequest:
    now = _now()
    req = FileRequest(
        public_token=f"token-{title}",
        created_by=uuid.uuid4(),
        title=title,
        instructions=instructions,
        created_at=now + timedelta(days=created_offset),
        expires_at=now + timedelta(days=expires_offset),
        is_disabled=False,
        is_expired=False,
        upload_count=0,
        max_uploads=5,
        max_total_bytes=1_000_000,
    )
    req.uploads = []
    return req


def test_parse_share_list_query_normalizes_values():
    query = parse_share_list_query(q="  hello ", status="nope", sort="bad")
    assert query.q == "hello"
    assert query.status == "all"
    assert query.sort == DEFAULT_SORT


def test_transfer_search_matches_title_and_message():
    transfers = [_transfer(title="Alpha", message="notes"), _transfer(title="Beta")]
    query = parse_share_list_query(q="notes")
    result = apply_transfer_list_query(transfers, query, now=_now(), purge_grace_days=7)
    assert [item.title for item in result] == ["Alpha"]


def test_transfer_filter_active_and_sort_title():
    transfers = [
        _transfer(title="Zulu", expires_offset=-1),
        _transfer(title="Alpha"),
        _transfer(title="Bravo", expires_offset=30),
    ]
    transfers[0].is_expired = True
    query = parse_share_list_query(status="active", sort="title_asc")
    result = apply_transfer_list_query(transfers, query, now=_now(), purge_grace_days=7)
    assert [item.title for item in result] == ["Alpha", "Bravo"]


def test_request_search_and_created_sort():
    requests = [
        _request(title="Older", created_offset=-2, instructions="find me"),
        _request(title="Newer", created_offset=0),
    ]
    query = parse_share_list_query(q="find", sort="created_desc")
    result = apply_request_list_query(requests, query, now=_now(), purge_grace_days=7)
    assert [item.title for item in result] == ["Older"]


def test_transfer_filter_expiry_pending():
    transfers = [
        _transfer(title="Soon", expires_offset=3),
        _transfer(title="Later", expires_offset=30),
        _transfer(title="Expired", expires_offset=-1),
    ]
    transfers[2].is_expired = True
    query = parse_share_list_query(status="expiry_pending")
    result = apply_transfer_list_query(transfers, query, now=_now(), purge_grace_days=7)
    assert [item.title for item in result] == ["Soon"]


def test_request_filter_expiry_pending():
    requests = [
        _request(title="Soon", expires_offset=2),
        _request(title="Later", expires_offset=14),
    ]
    query = parse_share_list_query(status="expiry_pending")
    result = apply_request_list_query(requests, query, now=_now(), purge_grace_days=7)
    assert [item.title for item in result] == ["Soon"]
