from __future__ import annotations

import re

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.auth.users import uses_local_auth
from app.database import async_session
from app.models import User, AppSettings
from app.services.oauth_linking import (
    OAuthLinkError,
    clear_user_grants,
    get_grants_for_user,
    resolve_oauth_user,
    set_user_grants,
    unlink_oauth,
)


async def _admin_user(session) -> User:
    result = await session.execute(select(User).where(User.email == "admin@test.com"))
    return result.scalar_one()


@pytest.mark.asyncio
async def test_oauth_login_does_not_overwrite_existing_display_name():
    async with async_session() as session:
        user = User(
            email="named@example.com",
            display_name="Admin Set Name",
            oauth_provider="entra",
            oauth_sub="sub-named",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        resolved, linked_now = await resolve_oauth_user(
            session,
            provider="entra",
            sub="sub-named",
            email="named@example.com",
            display_name="IdP Name",
        )
        await session.commit()
        await session.refresh(resolved)

        assert linked_now is False
        assert resolved.display_name == "Admin Set Name"


@pytest.mark.asyncio
async def test_oauth_login_fills_empty_display_name():
    async with async_session() as session:
        user = User(
            email="empty@example.com",
            oauth_provider="entra",
            oauth_sub="sub-empty",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        resolved, _ = await resolve_oauth_user(
            session,
            provider="entra",
            sub="sub-empty",
            email="empty@example.com",
            display_name="IdP Name",
        )
        await session.commit()
        await session.refresh(resolved)

        assert resolved.display_name == "IdP Name"


@pytest.mark.asyncio
async def test_local_user_without_grant_cannot_link():
    async with async_session() as session:
        user = User(
            email="local@example.com",
            password_hash=hash_password("password123"),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        with pytest.raises(OAuthLinkError) as exc:
            await resolve_oauth_user(
                session,
                provider="entra",
                sub="sub-local",
                email="local@example.com",
                display_name="Local User",
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_local_user_with_grant_links_and_clears_grants():
    async with async_session() as session:
        admin = await _admin_user(session)
        user = User(
            email="grant@example.com",
            password_hash=hash_password("password123"),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        await set_user_grants(session, user, {"entra"}, admin)
        await session.commit()

        linked_user, linked_now = await resolve_oauth_user(
            session,
            provider="entra",
            sub="sub-grant",
            email="grant@example.com",
            display_name="Grant User",
        )
        await session.commit()
        await session.refresh(linked_user)

        assert linked_now is True
        assert linked_user.oauth_provider == "entra"
        assert linked_user.oauth_sub == "sub-grant"
        assert linked_user.password_hash is not None
        assert await get_grants_for_user(session, user.id) == set()


@pytest.mark.asyncio
async def test_unknown_email_auto_provisions():
    async with async_session() as session:
        user, linked_now = await resolve_oauth_user(
            session,
            provider="entra",
            sub="sub-new",
            email="new@example.com",
            display_name="New User",
        )
        await session.commit()
        await session.refresh(user)

        assert linked_now is False
        assert user.email == "new@example.com"
        assert user.oauth_provider == "entra"
        assert user.password_hash is None


@pytest.mark.asyncio
async def test_sub_already_bound_to_other_user_rejected():
    async with async_session() as session:
        admin = await _admin_user(session)
        other = User(
            email="other@example.com",
            oauth_provider="entra",
            oauth_sub="shared-sub",
            is_active=True,
        )
        session.add(other)
        local = User(
            email="local2@example.com",
            password_hash=hash_password("password123"),
            is_active=True,
        )
        session.add(local)
        await session.commit()
        await session.refresh(local)

        await set_user_grants(session, local, {"entra"}, admin)
        await session.commit()

        with pytest.raises(OAuthLinkError) as exc:
            await resolve_oauth_user(
                session,
                provider="entra",
                sub="shared-sub",
                email="local2@example.com",
                display_name=None,
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_set_grants_blocked_when_already_linked():
    async with async_session() as session:
        admin = await _admin_user(session)
        user = User(
            email="linked@example.com",
            oauth_provider="entra",
            oauth_sub="sub-linked",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        with pytest.raises(HTTPException) as exc:
            await set_user_grants(session, user, {"entra"}, admin)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_unlink_preserves_password_and_totp():
    async with async_session() as session:
        user = User(
            email="hybrid@example.com",
            password_hash=hash_password("password123"),
            oauth_provider="entra",
            oauth_sub="sub-hybrid",
            totp_secret="SECRET123",
            totp_enabled=True,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        previous = unlink_oauth(user)
        assert previous == "entra"
        assert user.oauth_provider is None
        assert user.oauth_sub is None
        assert user.password_hash is not None
        assert user.totp_secret == "SECRET123"
        assert user.totp_enabled is True


def test_uses_local_auth_for_hybrid_user():
    user = User(
        email="hybrid@example.com",
        password_hash=hash_password("password123"),
        oauth_provider="entra",
        oauth_sub="sub-hybrid",
    )
    assert uses_local_auth(user) is True


def test_uses_local_auth_false_for_oauth_only_user():
    user = User(
        email="oauth@example.com",
        oauth_provider="entra",
        oauth_sub="sub-oauth",
    )
    assert uses_local_auth(user) is False


async def _login_admin(client: AsyncClient) -> str:
    login_page = await client.get("/auth/login")
    match = re.search(r'name="csrf-token" content="([^"]+)"', login_page.text)
    token = match.group(1)
    await client.post(
        "/auth/login/local",
        data={"email": "admin@test.com", "password": "password123", "csrf_token": token},
        follow_redirects=False,
    )
    admin_page = await client.get("/admin")
    match = re.search(r'name="csrf-token" content="([^"]+)"', admin_page.text)
    return match.group(1)


@pytest.mark.asyncio
async def test_admin_can_update_user_display_name(client: AsyncClient):
    async with async_session() as session:
        user = User(
            email="rename@example.com",
            password_hash=hash_password("password123"),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    token = await _login_admin(client)
    save = await client.post(
        f"/admin/users/{user_id}/display-name",
        data={"display_name": "Renamed User", "csrf_token": token},
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/admin/users?user_display_name_saved=1"

    async with async_session() as session:
        user = await session.get(User, user_id)
        assert user.display_name == "Renamed User"


@pytest.mark.asyncio
async def test_admin_can_save_and_unlink_oauth_grants(client: AsyncClient, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "entra_tenant_id", "tenant-id")
    monkeypatch.setattr(settings, "entra_client_id", "client-id")
    monkeypatch.setattr(settings, "entra_client_secret", "client-secret")

    async with async_session() as session:
        user = User(
            email="managed@example.com",
            password_hash=hash_password("password123"),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    token = await _login_admin(client)
    save = await client.post(
        f"/admin/users/{user_id}/oauth-grants",
        data={"grant_entra": "1", "csrf_token": token},
        follow_redirects=False,
    )
    assert save.status_code == 303
    assert save.headers["location"] == "/admin/users?oauth_grants_saved=1"

    async with async_session() as session:
        assert await get_grants_for_user(session, user_id) == {"entra"}

        user = await session.get(User, user_id)
        user.oauth_provider = "entra"
        user.oauth_sub = "sub-managed"
        await clear_user_grants(session, user_id)
        await session.commit()

    admin_page = await client.get("/admin")
    match = re.search(r'name="csrf-token" content="([^"]+)"', admin_page.text)
    token = match.group(1)
    unlink = await client.post(
        f"/admin/users/{user_id}/oauth/unlink",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert unlink.status_code == 303
    assert unlink.headers["location"] == "/admin/users?oauth_unlinked=1"

    async with async_session() as session:
        user = await session.get(User, user_id)
        assert user.oauth_provider is None
        assert user.password_hash is not None


@pytest.mark.asyncio
async def test_admin_cannot_unlink_own_sso_when_local_login_disabled(client: AsyncClient):
    token = await _login_admin(client)

    async with async_session() as session:
        admin = await _admin_user(session)
        admin.oauth_provider = "entra"
        admin.oauth_sub = "sub-admin"
        settings = await session.get(AppSettings, 1)
        settings.allow_local_login = False
        await session.commit()
        admin_id = admin.id

    admin_page = await client.get("/admin")
    assert "Unlink SSO" not in admin_page.text

    match = re.search(r'name="csrf-token" content="([^"]+)"', admin_page.text)
    token = match.group(1)
    unlink = await client.post(
        f"/admin/users/{admin_id}/oauth/unlink",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert unlink.status_code == 303
    assert "error=" in unlink.headers["location"]

    async with async_session() as session:
        admin = await session.get(User, admin_id)
        assert admin.oauth_provider == "entra"


@pytest.mark.asyncio
async def test_admin_can_remove_user_totp(client: AsyncClient):
    async with async_session() as session:
        user = User(
            email="totp@example.com",
            password_hash=hash_password("password123"),
            totp_secret="SECRET123",
            totp_enabled=True,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    token = await _login_admin(client)
    remove = await client.post(
        f"/admin/users/{user_id}/totp/remove",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert remove.status_code == 303
    assert "user_totp_removed=1" in remove.headers["location"]

    async with async_session() as session:
        user = await session.get(User, user_id)
        assert user.totp_enabled is False
        assert user.totp_secret is None


@pytest.mark.asyncio
async def test_admin_users_search_and_filter(client: AsyncClient):
    async with async_session() as session:
        session.add(
            User(
                email="alice@example.com",
                display_name="Alice Example",
                password_hash=hash_password("password123"),
                is_active=True,
            )
        )
        session.add(
            User(
                email="bob@example.com",
                oauth_provider="entra",
                oauth_sub="sub-bob",
                is_active=True,
            )
        )
        await session.commit()

    await _login_admin(client)

    by_name = await client.get("/admin/users?q=alice")
    assert by_name.status_code == 200
    assert "alice@example.com" in by_name.text
    assert "bob@example.com" not in by_name.text

    by_sso = await client.get("/admin/users?sign_in=sso")
    assert by_sso.status_code == 200
    assert "bob@example.com" in by_sso.text
    assert "alice@example.com" not in by_sso.text
