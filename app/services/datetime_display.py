from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from app.config import settings


@lru_cache(maxsize=1)
def display_timezone() -> ZoneInfo:
    name = settings.display_timezone.strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(display_timezone())


def ensure_utc(dt: datetime) -> datetime:
    """Normalize datetimes from SQLite (naive UTC) and Postgres (aware) for comparison."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_datetime(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return "—"
    return _to_local(value).strftime(fmt)


def format_date(value: datetime | None, fmt: str = "%Y-%m-%d") -> str:
    if value is None:
        return "—"
    return _to_local(value).strftime(fmt)


def format_datetime_with_tz(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return "—"
    local = _to_local(value)
    tz_label = local.tzname() or settings.display_timezone
    return f"{local.strftime(fmt)} {tz_label}"


def default_expiry_date(default_days: int) -> str:
    local = datetime.now(display_timezone()) + timedelta(days=default_days)
    return local.strftime("%Y-%m-%d")


def today_date() -> str:
    return datetime.now(display_timezone()).strftime("%Y-%m-%d")


def max_expiry_date(max_days: int) -> str:
    return (datetime.now(display_timezone()) + timedelta(days=max_days)).strftime("%Y-%m-%d")


def ensure_expiry_within_limit(expires_at: datetime, max_days: int) -> None:
    if max_days < 1:
        return
    expires_local = _to_local(expires_at).date()
    limit_local = (datetime.now(display_timezone()) + timedelta(days=max_days)).date()
    if expires_local > limit_local:
        raise HTTPException(
            status_code=400,
            detail=f"Expiry cannot be more than {max_days} days in the future",
        )


def parse_expiry_date(expires_at: str) -> datetime:
    try:
        return datetime.fromisoformat(expires_at).replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(expires_at, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
