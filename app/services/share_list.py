from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar

from app.models import FileRequest, Transfer
from app.services.share_lifecycle import (
    file_request_deletion_pending,
    file_request_expiry_pending,
    transfer_deletion_pending,
    transfer_expiry_pending,
)
from app.services.share_status import (
    file_request_is_accessible,
    file_request_is_expired,
    transfer_is_accessible,
    transfer_is_expired,
)

DEFAULT_SORT = "created_desc"

VALID_SORTS = frozenset({
    "created_desc",
    "created_asc",
    "expires_desc",
    "expires_asc",
    "title_asc",
    "title_desc",
})

VALID_STATUS = frozenset({
    "all",
    "active",
    "inactive",
    "expired",
    "disabled",
    "deletion_pending",
    "expiry_pending",
})

T = TypeVar("T")


@dataclass(frozen=True)
class ShareListQuery:
    q: str
    status: str
    sort: str

    @property
    def is_filtered(self) -> bool:
        return bool(self.q) or self.status != "all" or self.sort != DEFAULT_SORT


def parse_share_list_query(*, q: str = "", status: str = "all", sort: str = DEFAULT_SORT) -> ShareListQuery:
    return ShareListQuery(
        q=(q or "").strip(),
        status=status if status in VALID_STATUS else "all",
        sort=sort if sort in VALID_SORTS else DEFAULT_SORT,
    )


def _contains(haystack: str | None, needle: str) -> bool:
    return bool(haystack and needle in haystack.casefold())


def _matches_search_transfer(transfer: Transfer, q: str) -> bool:
    if not q:
        return True
    needle = q.casefold()
    return needle in transfer.title.casefold() or _contains(transfer.message, needle)


def _matches_search_request(req: FileRequest, q: str) -> bool:
    if not q:
        return True
    needle = q.casefold()
    return needle in req.title.casefold() or _contains(req.instructions, needle)


def _matches_status_transfer(
    transfer: Transfer,
    status: str,
    *,
    now: datetime,
    purge_grace_days: int,
) -> bool:
    if status == "all":
        return True
    if status == "active":
        return transfer_is_accessible(transfer, now)
    if status == "inactive":
        return not transfer_is_accessible(transfer, now)
    if status == "expired":
        return transfer_is_expired(transfer, now)
    if status == "disabled":
        return transfer.is_disabled
    if status == "deletion_pending":
        return transfer_deletion_pending(transfer, purge_grace_days, now)
    if status == "expiry_pending":
        return transfer_expiry_pending(transfer, now)
    return True


def _matches_status_request(
    req: FileRequest,
    status: str,
    *,
    now: datetime,
    purge_grace_days: int,
) -> bool:
    if status == "all":
        return True
    if status == "active":
        return file_request_is_accessible(req, now)
    if status == "inactive":
        return not file_request_is_accessible(req, now)
    if status == "expired":
        return file_request_is_expired(req, now)
    if status == "disabled":
        return req.is_disabled
    if status == "deletion_pending":
        return file_request_deletion_pending(req, purge_grace_days, now)
    if status == "expiry_pending":
        return file_request_expiry_pending(req, now)
    return True


def _sort_key(sort: str, *, title: Callable[[T], str], created_at: Callable[[T], datetime], expires_at: Callable[[T], datetime]):
    field, direction = sort.rsplit("_", 1)
    reverse = direction == "desc"
    if field == "title":
        key: Callable[[T], object] = lambda item: title(item).casefold()
    elif field == "expires":
        key = lambda item: expires_at(item)
    else:
        key = lambda item: created_at(item)
    return key, reverse


def _sort_items(items: list[T], sort: str, *, title: Callable[[T], str], created_at: Callable[[T], datetime], expires_at: Callable[[T], datetime]) -> list[T]:
    key, reverse = _sort_key(sort, title=title, created_at=created_at, expires_at=expires_at)
    return sorted(items, key=key, reverse=reverse)


def apply_transfer_list_query(
    transfers: list[Transfer],
    query: ShareListQuery,
    *,
    now: datetime,
    purge_grace_days: int,
) -> list[Transfer]:
    items = [
        transfer
        for transfer in transfers
        if _matches_search_transfer(transfer, query.q)
        and _matches_status_transfer(transfer, query.status, now=now, purge_grace_days=purge_grace_days)
    ]
    return _sort_items(
        items,
        query.sort,
        title=lambda transfer: transfer.title,
        created_at=lambda transfer: transfer.created_at,
        expires_at=lambda transfer: transfer.expires_at,
    )


def apply_request_list_query(
    requests: list[FileRequest],
    query: ShareListQuery,
    *,
    now: datetime,
    purge_grace_days: int,
) -> list[FileRequest]:
    items = [
        req
        for req in requests
        if _matches_search_request(req, query.q)
        and _matches_status_request(req, query.status, now=now, purge_grace_days=purge_grace_days)
    ]
    return _sort_items(
        items,
        query.sort,
        title=lambda req: req.title,
        created_at=lambda req: req.created_at,
        expires_at=lambda req: req.expires_at,
    )
