from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

UNLOCK_MAX_ATTEMPTS = 5
UNLOCK_LOCKOUT = timedelta(minutes=15)


@dataclass
class _LockoutState:
    failed_attempts: int = 0
    locked_until: datetime | None = None


_store: dict[str, _LockoutState] = {}
_lock = asyncio.Lock()


def _lockout_key(kind: str, token: str) -> str:
    return f"{kind}:{token}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def is_unlock_locked(kind: str, token: str) -> bool:
    async with _lock:
        state = _store.get(_lockout_key(kind, token))
        if not state or not state.locked_until:
            return False
        if state.locked_until <= _utcnow():
            state.locked_until = None
            state.failed_attempts = 0
            return False
        return True


async def record_failed_unlock(kind: str, token: str) -> bool:
    """Record a failed unlock attempt. Returns True if the share is now locked."""
    async with _lock:
        key = _lockout_key(kind, token)
        state = _store.setdefault(key, _LockoutState())
        state.failed_attempts += 1
        if state.failed_attempts >= UNLOCK_MAX_ATTEMPTS:
            state.locked_until = _utcnow() + UNLOCK_LOCKOUT
            return True
        return False


async def reset_unlock_lockout(kind: str, token: str) -> None:
    async with _lock:
        _store.pop(_lockout_key(kind, token), None)


def clear_unlock_lockout_store() -> None:
    """Test helper."""
    _store.clear()
