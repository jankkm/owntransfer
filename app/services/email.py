from __future__ import annotations

import html
import logging
import re
from email.message import EmailMessage
from typing import Optional, TypedDict

import aiosmtplib

logger = logging.getLogger(__name__)

from app.config import settings
from app.i18n import _, activate
from app.models import AppSettings
from app.services.email_templates import render_email_subject, render_email_template


class SmtpOverrides(TypedDict, total=False):
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str


def _resolve_smtp(
    app_settings: AppSettings,
    *,
    overrides: SmtpOverrides | None = None,
) -> tuple[str, int, str | None, str | None, str, bool] | None:
    if overrides is not None:
        host = (overrides.get("smtp_host") or "").strip() or None
        port = overrides.get("smtp_port") or app_settings.smtp_port or settings.smtp_port
        user = (overrides.get("smtp_user") or "").strip() or None
        password = (
            overrides.get("smtp_password")
            or app_settings.smtp_password
            or settings.smtp_password
        )
        from_addr = (
            (overrides.get("smtp_from") or "").strip()
            or app_settings.smtp_from
            or settings.smtp_from
            or "noreply@owntransfer.local"
        )
    else:
        host = app_settings.smtp_host or settings.smtp_host
        port = app_settings.smtp_port or settings.smtp_port
        user = app_settings.smtp_user or settings.smtp_user
        password = app_settings.smtp_password or settings.smtp_password
        from_addr = app_settings.smtp_from or settings.smtp_from or "noreply@owntransfer.local"

    if not host:
        return None

    return host, port, user, password, from_addr, app_settings.smtp_use_tls


def _html_to_plain_text(html_body: str) -> str:
    text = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"\2 (\1)",
        html_body,
        flags=re.I | re.S,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>\s*", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _wrap_email_html(body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<style>strong,b{font-weight:700;}</style>\n"
        "</head>\n"
        '<body style="font-family: sans-serif; line-height: 1.5; color: #111;">\n'
        f"{body}\n"
        "</body>\n"
        "</html>"
    )


async def _deliver_email(
    *,
    to: str | list[str],
    subject: str,
    body_html: str,
    body_text: Optional[str],
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    from_addr: str,
    use_tls: bool,
) -> None:
    recipients = [to] if isinstance(to, str) else to
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    plain = body_text if body_text is not None else _html_to_plain_text(body_html)
    msg.set_content(plain)
    msg.add_alternative(_wrap_email_html(body_html), subtype="html")

    await aiosmtplib.send(
        msg,
        hostname=host,
        port=port,
        username=username,
        password=password,
        start_tls=use_tls,
    )


async def send_email(
    app_settings: AppSettings,
    *,
    to: str | list[str],
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
) -> bool:
    resolved = _resolve_smtp(app_settings)
    if not resolved:
        return False

    host, port, user, password, from_addr, use_tls = resolved
    try:
        await _deliver_email(
            to=to,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            host=host,
            port=port,
            username=user,
            password=password,
            from_addr=from_addr,
            use_tls=use_tls,
        )
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False
    return True


async def send_smtp_test_email(
    app_settings: AppSettings,
    *,
    to: str,
    overrides: SmtpOverrides,
) -> None:
    resolved = _resolve_smtp(app_settings, overrides=overrides)
    if not resolved:
        raise ValueError(_("SMTP host is required"))

    host, port, user, password, from_addr, use_tls = resolved
    subject = _("SMTP test — %(app_name)s") % {"app_name": app_settings.app_name}
    body_html = f"<p>{_('This is a test email from your SMTP configuration.')}</p>"
    await _deliver_email(
        to=to,
        subject=subject,
        body_html=body_html,
        body_text=None,
        host=host,
        port=port,
        username=user,
        password=password,
        from_addr=from_addr,
        use_tls=use_tls,
    )


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
    activate(settings.default_locale)
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
    activate(settings.default_locale)
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
