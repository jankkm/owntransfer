from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.oauth_providers import get_oauth_providers
from app.i18n import _
from app.models import User, UserOAuthLinkGrant


def configured_provider_keys() -> set[str]:
    return {provider.key for provider in get_oauth_providers()}


def provider_label(provider_key: str) -> str:
    for provider in get_oauth_providers():
        if provider.key == provider_key:
            return provider.name
    return provider_key


async def get_grants_for_user(db: AsyncSession, user_id: uuid.UUID) -> set[str]:
    result = await db.execute(
        select(UserOAuthLinkGrant.provider_key).where(UserOAuthLinkGrant.user_id == user_id)
    )
    return set(result.scalars().all())


async def get_grants_for_users(
    db: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    if not user_ids:
        return {}
    result = await db.execute(
        select(UserOAuthLinkGrant.user_id, UserOAuthLinkGrant.provider_key).where(
            UserOAuthLinkGrant.user_id.in_(user_ids)
        )
    )
    grants: dict[uuid.UUID, set[str]] = {user_id: set() for user_id in user_ids}
    for user_id, provider_key in result.all():
        grants[user_id].add(provider_key)
    return grants


async def clear_user_grants(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(delete(UserOAuthLinkGrant).where(UserOAuthLinkGrant.user_id == user_id))


async def set_user_grants(
    db: AsyncSession,
    user: User,
    provider_keys: set[str],
    admin: User,
) -> None:
    if user.oauth_provider:
        raise HTTPException(
            status_code=400,
            detail=_("This account is already linked to an identity provider"),
        )

    configured = configured_provider_keys()
    invalid = provider_keys - configured
    if invalid:
        raise HTTPException(status_code=400, detail=_("One or more providers are not configured"))

    await clear_user_grants(db, user.id)
    for provider_key in sorted(provider_keys):
        db.add(
            UserOAuthLinkGrant(
                user_id=user.id,
                provider_key=provider_key,
                granted_by=admin.id,
            )
        )


def user_may_link_provider(user: User, provider_key: str, grants: set[str]) -> bool:
    if user.oauth_provider:
        return user.oauth_provider == provider_key
    return provider_key in grants


def apply_oauth_profile_updates(user: User, email: str, display_name: str | None) -> None:
    if display_name and not user.display_name:
        user.display_name = display_name
    if user.email != email:
        user.email = email


def link_oauth_to_user(
    user: User,
    *,
    provider: str,
    sub: str,
    display_name: str | None,
) -> None:
    user.oauth_provider = provider
    user.oauth_sub = sub
    if display_name and not user.display_name:
        user.display_name = display_name


def unlink_oauth(user: User) -> Optional[str]:
    previous = user.oauth_provider
    user.oauth_provider = None
    user.oauth_sub = None
    return previous


async def sub_bound_to_other_user(
    db: AsyncSession,
    *,
    provider: str,
    sub: str,
    exclude_user_id: uuid.UUID | None = None,
) -> bool:
    query = select(User.id).where(User.oauth_provider == provider, User.oauth_sub == sub)
    if exclude_user_id is not None:
        query = query.where(User.id != exclude_user_id)
    result = await db.execute(query)
    return result.scalar_one_or_none() is not None


class OAuthLinkError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def resolve_oauth_user(
    db: AsyncSession,
    *,
    provider: str,
    sub: str,
    email: str,
    display_name: str | None,
) -> tuple[User, bool]:
    """Resolve or create a user for an OAuth callback. Returns (user, linked_now)."""
    result = await db.execute(
        select(User).where(User.oauth_provider == provider, User.oauth_sub == sub)
    )
    user = result.scalar_one_or_none()
    if user:
        if not user.is_active:
            raise OAuthLinkError(403, _("This account is not active"))
        if user.email != email:
            raise OAuthLinkError(
                403,
                _("This identity provider account is already linked to another user"),
            )
        apply_oauth_profile_updates(user, email, display_name)
        return user, False

    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        if not existing.is_active:
            raise OAuthLinkError(403, _("This account is not active"))

        if existing.oauth_provider and existing.oauth_provider != provider:
            raise OAuthLinkError(
                403,
                _(
                    "An account with this email already exists. Sign in with your "
                    "existing method, or ask an administrator to link your account."
                ),
            )

        if existing.oauth_provider == provider:
            if await sub_bound_to_other_user(db, provider=provider, sub=sub, exclude_user_id=existing.id):
                raise OAuthLinkError(
                    403,
                    _("This identity provider account is already linked to another user"),
                )
            existing.oauth_sub = sub
            apply_oauth_profile_updates(existing, email, display_name)
            return existing, False

        grants = await get_grants_for_user(db, existing.id)
        if not user_may_link_provider(existing, provider, grants):
            raise OAuthLinkError(
                403,
                _(
                    "An account with this email already exists. Sign in with your "
                    "existing method, or ask an administrator to link your account."
                ),
            )

        if await sub_bound_to_other_user(db, provider=provider, sub=sub):
            raise OAuthLinkError(
                403,
                _("This identity provider account is already linked to another user"),
            )

        link_oauth_to_user(existing, provider=provider, sub=sub, display_name=display_name)
        await clear_user_grants(db, existing.id)
        return existing, True

    if await sub_bound_to_other_user(db, provider=provider, sub=sub):
        raise OAuthLinkError(
            403,
            _("This identity provider account is already linked to another user"),
        )

    user = User(
        email=email,
        oauth_provider=provider,
        oauth_sub=sub,
        display_name=display_name,
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    return user, False
