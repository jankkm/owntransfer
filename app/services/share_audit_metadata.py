from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.i18n import _
from app.services.datetime_display import format_datetime_with_tz


def serialize_file_row(name: str, size_bytes: int, content_type: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "size_bytes": size_bytes}
    if content_type:
        row["content_type"] = content_type
    return row


def serialize_transfer_files(files: list) -> list[dict[str, Any]]:
    return [
        serialize_file_row(f.original_name, f.size_bytes, f.content_type)
        for f in files
    ]


def _display_text(value: Any) -> str:
    if value is None or value == "":
        return _("(empty)")
    return str(value)


def _enabled_label(enabled: bool) -> str:
    return _("Yes") if enabled else _("No")


def _password_label(protected: bool) -> str:
    return _("Protected") if protected else _("Not protected")


def _format_expiry(expires_at: datetime) -> str:
    return format_datetime_with_tz(expires_at)


def append_change(
    changes: list[dict[str, Any]],
    *,
    field: str,
    label: str,
    old: Any,
    new: Any,
) -> None:
    if old == new:
        return
    changes.append(
        {
            "field": field,
            "label": label,
            "old": _display_text(old),
            "new": _display_text(new),
        }
    )


def build_transfer_update_changes(
    *,
    old_title: str,
    new_title: str,
    old_message: str | None,
    new_message: str | None,
    old_expires_at: datetime,
    new_expires_at: datetime,
    old_max_downloads: int,
    new_max_downloads: int,
    old_notify_on_download: bool,
    new_notify_on_download: bool,
    had_password: bool,
    remove_password: bool,
    new_password: str | None,
    old_enabled: bool,
    enabled: bool | None,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    append_change(changes, field="title", label=_("Title"), old=old_title, new=new_title)
    append_change(
        changes,
        field="message",
        label=_("Message"),
        old=old_message or "",
        new=new_message or "",
    )
    append_change(
        changes,
        field="expires_at",
        label=_("Expiry date"),
        old=_format_expiry(old_expires_at),
        new=_format_expiry(new_expires_at),
    )
    append_change(
        changes,
        field="max_downloads",
        label=_("Max downloads"),
        old=old_max_downloads,
        new=new_max_downloads,
    )
    append_change(
        changes,
        field="notify_on_download",
        label=_("Notify on download"),
        old=_enabled_label(old_notify_on_download),
        new=_enabled_label(new_notify_on_download),
    )

    if remove_password and had_password:
        append_change(
            changes,
            field="password",
            label=_("Password protection"),
            old=_password_label(True),
            new=_password_label(False),
        )
    elif new_password and not had_password:
        append_change(
            changes,
            field="password",
            label=_("Password protection"),
            old=_password_label(False),
            new=_password_label(True),
        )
    elif new_password and had_password:
        append_change(
            changes,
            field="password",
            label=_("Password protection"),
            old=_password_label(True),
            new=_("Protected (changed)"),
        )

    if enabled is not None:
        new_enabled = enabled
        if old_enabled != new_enabled:
            append_change(
                changes,
                field="enabled",
                label=_("Active"),
                old=_enabled_label(old_enabled),
                new=_enabled_label(new_enabled),
            )

    return changes


def build_owner_change_metadata(
    *,
    previous_owner_email: str | None,
    new_owner_email: str,
    previous_owner_id: uuid.UUID,
    new_owner_id: uuid.UUID,
) -> dict[str, Any]:
    old = previous_owner_email or str(previous_owner_id)
    return {
        "previous_owner_id": str(previous_owner_id),
        "new_owner_id": str(new_owner_id),
        "previous_owner_email": previous_owner_email,
        "new_owner_email": new_owner_email,
        "changes": [
            {
                "field": "owner",
                "label": _("Owner"),
                "old": old,
                "new": new_owner_email,
            }
        ],
    }


def build_file_request_update_changes(
    *,
    old_title: str,
    new_title: str,
    old_instructions: str | None,
    new_instructions: str | None,
    old_expires_at: datetime,
    new_expires_at: datetime,
    old_max_uploads: int,
    new_max_uploads: int,
    old_max_total_bytes: int,
    new_max_total_bytes: int,
    had_password: bool,
    remove_password: bool,
    new_password: str | None,
    old_enabled: bool,
    enabled: bool | None,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    append_change(changes, field="title", label=_("Title"), old=old_title, new=new_title)
    append_change(
        changes,
        field="instructions",
        label=_("Instructions"),
        old=old_instructions or "",
        new=new_instructions or "",
    )
    append_change(
        changes,
        field="expires_at",
        label=_("Expiry date"),
        old=_format_expiry(old_expires_at),
        new=_format_expiry(new_expires_at),
    )
    append_change(
        changes,
        field="max_uploads",
        label=_("Max uploads"),
        old=old_max_uploads,
        new=new_max_uploads,
    )
    old_mb = old_max_total_bytes // (1024 * 1024)
    new_mb = new_max_total_bytes // (1024 * 1024)
    append_change(
        changes,
        field="max_total_bytes",
        label=_("Max total size (MB)"),
        old=old_mb,
        new=new_mb,
    )

    if remove_password and had_password:
        append_change(
            changes,
            field="password",
            label=_("Password protection"),
            old=_password_label(True),
            new=_password_label(False),
        )
    elif new_password and not had_password:
        append_change(
            changes,
            field="password",
            label=_("Password protection"),
            old=_password_label(False),
            new=_password_label(True),
        )
    elif new_password and had_password:
        append_change(
            changes,
            field="password",
            label=_("Password protection"),
            old=_password_label(True),
            new=_("Protected (changed)"),
        )

    if enabled is not None:
        new_enabled = enabled
        if old_enabled != new_enabled:
            append_change(
                changes,
                field="enabled",
                label=_("Active"),
                old=_enabled_label(old_enabled),
                new=_enabled_label(new_enabled),
            )

    return changes
