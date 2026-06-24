from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sessions import SESSION_COOKIE, load_session_token
from app.database import async_session, get_db
from app.i18n import _
from app.models import User


async def require_user_id(request: Request) -> uuid.UUID:
    """Resolve the authenticated user without holding a DB session for the request."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail=_("Not authenticated"))
    data = load_session_token(token)
    if not data:
        raise HTTPException(status_code=401, detail=_("Not authenticated"))
    user_id = uuid.UUID(data["uid"])
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
        user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail=_("Not authenticated"))
    return user.id


async def get_current_user_optional(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    data = load_session_token(token)
    if not data:
        return None
    user_id = uuid.UUID(data["uid"])
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    return result.scalar_one_or_none()


async def get_current_user(user: Optional[User] = Depends(get_current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail=_("Not authenticated"))
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail=_("Admin access required"))
    return user
