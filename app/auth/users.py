from __future__ import annotations

from app.models import User


def uses_local_auth(user: User) -> bool:
    return not user.oauth_provider
