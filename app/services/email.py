from __future__ import annotations

from email.message import EmailMessage
from typing import Optional

import aiosmtplib

from app.config import settings
from app.models import AppSettings
from app.services.email_templates import render_email_subject, render_email_template


async def send_email(
    app_settings: AppSettings,
    *,
    to: str | list[str],
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
) -> bool:
    host = app_settings.smtp_host or settings.smtp_host
    if not host:
        return False

    recipients = [to] if isinstance(to, str) else to
    msg = EmailMessage()
    msg["From"] = app_settings.smtp_from or settings.smtp_from or "noreply@owntransfer.local"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body_text or body_html)
    msg.add_alternative(body_html, subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=host,
        port=app_settings.smtp_port or settings.smtp_port,
        username=app_settings.smtp_user or settings.smtp_user,
        password=app_settings.smtp_password or settings.smtp_password,
        start_tls=app_settings.smtp_use_tls,
    )
    return True


async def send_share_email(
    app_settings: AppSettings,
    *,
    recipients: list[str],
    title: str,
    message: str | None,
    link: str,
    password: str | None,
    expires_at: str,
) -> bool:
    ctx = {
        "app_name": app_settings.app_name,
        "title": title,
        "message": message,
        "link": link,
        "password": password,
        "expires_at": expires_at,
    }
    return await send_email(
        app_settings,
        to=recipients,
        subject=render_email_subject(app_settings, "share", **ctx),
        body_html=render_email_template(app_settings, "share", **ctx),
    )


async def send_request_email(
    app_settings: AppSettings,
    *,
    recipients: list[str],
    sender: str,
    title: str,
    instructions: str | None,
    link: str,
    password: str | None,
    expires_at: str,
) -> bool:
    ctx = {
        "app_name": app_settings.app_name,
        "sender": sender,
        "title": title,
        "instructions": instructions,
        "link": link,
        "password": password,
        "expires_at": expires_at,
    }
    return await send_email(
        app_settings,
        to=recipients,
        subject=render_email_subject(app_settings, "request", **ctx),
        body_html=render_email_template(app_settings, "request", **ctx),
    )


async def send_upload_notify(
    app_settings: AppSettings,
    *,
    to: str,
    title: str,
    dashboard_link: str,
) -> bool:
    ctx = {
        "app_name": app_settings.app_name,
        "title": title,
        "dashboard_link": dashboard_link,
    }
    return await send_email(
        app_settings,
        to=to,
        subject=render_email_subject(app_settings, "upload_notify", **ctx),
        body_html=render_email_template(app_settings, "upload_notify", **ctx),
    )


async def send_download_notify(
    app_settings: AppSettings,
    *,
    to: str,
    title: str,
    download_count: int,
    max_downloads: int | str,
) -> bool:
    ctx = {
        "app_name": app_settings.app_name,
        "title": title,
        "download_count": download_count,
        "max_downloads": max_downloads,
    }
    return await send_email(
        app_settings,
        to=to,
        subject=render_email_subject(app_settings, "download_notify", **ctx),
        body_html=render_email_template(app_settings, "download_notify", **ctx),
    )


async def send_expired_unused(
    app_settings: AppSettings,
    *,
    to: str,
    title: str,
    resource_label: str,
    expires_at: str,
    edit_link: str,
) -> bool:
    ctx = {
        "app_name": app_settings.app_name,
        "title": title,
        "resource_label": resource_label,
        "expires_at": expires_at,
        "edit_link": edit_link,
    }
    return await send_email(
        app_settings,
        to=to,
        subject=render_email_subject(app_settings, "expired_unused", **ctx),
        body_html=render_email_template(app_settings, "expired_unused", **ctx),
    )


async def send_purge_reminder(
    app_settings: AppSettings,
    *,
    to: str,
    title: str,
    resource_label: str,
    expires_at: str,
    edit_link: str,
    purge_at: str,
    days_until_purge: int,
) -> bool:
    ctx = {
        "app_name": app_settings.app_name,
        "title": title,
        "resource_label": resource_label,
        "expires_at": expires_at,
        "edit_link": edit_link,
        "purge_at": purge_at,
        "days_until_purge": days_until_purge,
    }
    return await send_email(
        app_settings,
        to=to,
        subject=render_email_subject(app_settings, "purge_reminder", **ctx),
        body_html=render_email_template(app_settings, "purge_reminder", **ctx),
    )
