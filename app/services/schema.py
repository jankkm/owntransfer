from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.models import Base


async def ensure_schema(conn: AsyncConnection) -> None:
    await conn.run_sync(Base.metadata.create_all)

    def _existing_columns(sync_conn) -> set[str]:
        return {col["name"] for col in inspect(sync_conn).get_columns("app_settings")}

    columns = await conn.run_sync(_existing_columns)
    dialect = conn.dialect.name

    if "allow_user_share_emails" not in columns:
        if dialect == "sqlite":
            await conn.execute(
                text(
                    "ALTER TABLE app_settings "
                    "ADD COLUMN allow_user_share_emails BOOLEAN NOT NULL DEFAULT 1"
                )
            )
        else:
            await conn.execute(
                text(
                    "ALTER TABLE app_settings "
                    "ADD COLUMN allow_user_share_emails BOOLEAN NOT NULL DEFAULT TRUE"
                )
            )

    if "logo_data" not in columns:
        col_type = "BLOB" if dialect == "sqlite" else "BYTEA"
        await conn.execute(text(f"ALTER TABLE app_settings ADD COLUMN logo_data {col_type}"))

    if "logo_content_type" not in columns:
        await conn.execute(
            text("ALTER TABLE app_settings ADD COLUMN logo_content_type VARCHAR(128)")
        )

    if "impressum_enabled" not in columns:
        default = "1" if dialect == "sqlite" else "TRUE"
        await conn.execute(
            text(
                f"ALTER TABLE app_settings "
                f"ADD COLUMN impressum_enabled BOOLEAN NOT NULL DEFAULT {default}"
            )
        )

    if "impressum_markdown" not in columns:
        await conn.execute(text("ALTER TABLE app_settings ADD COLUMN impressum_markdown TEXT"))

    columns = await conn.run_sync(_existing_columns)
    if "privacy_policy_enabled" not in columns:
        default = "0" if dialect == "sqlite" else "FALSE"
        await conn.execute(
            text(
                f"ALTER TABLE app_settings "
                f"ADD COLUMN privacy_policy_enabled BOOLEAN NOT NULL DEFAULT {default}"
            )
        )
    columns = await conn.run_sync(_existing_columns)
    if "privacy_policy_markdown" not in columns:
        await conn.execute(text("ALTER TABLE app_settings ADD COLUMN privacy_policy_markdown TEXT"))

    if "max_share_expiry_days" not in columns:
        await conn.execute(
            text("ALTER TABLE app_settings ADD COLUMN max_share_expiry_days INTEGER NOT NULL DEFAULT 365")
        )

    columns = await conn.run_sync(_existing_columns)
    if "purge_grace_days" not in columns:
        await conn.execute(
            text("ALTER TABLE app_settings ADD COLUMN purge_grace_days INTEGER NOT NULL DEFAULT 7")
        )
        if "purge_grace_hours" in columns:
            if dialect == "sqlite":
                migrate_days = (
                    "UPDATE app_settings SET purge_grace_days = MAX(1, purge_grace_hours / 24) WHERE id = 1"
                )
            else:
                migrate_days = (
                    "UPDATE app_settings SET purge_grace_days = GREATEST(1, purge_grace_hours / 24) WHERE id = 1"
                )
            await conn.execute(text(migrate_days))

    columns = await conn.run_sync(_existing_columns)
    if "purge_notify_days" not in columns:
        await conn.execute(
            text("ALTER TABLE app_settings ADD COLUMN purge_notify_days INTEGER NOT NULL DEFAULT 0")
        )

    for col in (
        "email_tpl_share",
        "email_tpl_request",
        "email_tpl_upload_notify",
        "email_tpl_download_notify",
        "email_tpl_expired_unused",
        "email_tpl_purge_reminder",
    ):
        columns = await conn.run_sync(_existing_columns)
        if col not in columns:
            await conn.execute(text(f"ALTER TABLE app_settings ADD COLUMN {col} TEXT"))

    for col in (
        "email_subj_share",
        "email_subj_request",
        "email_subj_upload_notify",
        "email_subj_download_notify",
        "email_subj_expired_unused",
        "email_subj_purge_reminder",
    ):
        columns = await conn.run_sync(_existing_columns)
        if col not in columns:
            await conn.execute(text(f"ALTER TABLE app_settings ADD COLUMN {col} VARCHAR(512)"))

    def _request_upload_columns(sync_conn) -> set[str]:
        return {col["name"] for col in inspect(sync_conn).get_columns("request_uploads")}

    upload_columns = await conn.run_sync(_request_upload_columns)
    if "ip_address" not in upload_columns:
        await conn.execute(text("ALTER TABLE request_uploads ADD COLUMN ip_address VARCHAR(64)"))

    def _table_columns(sync_conn, table: str) -> set[str]:
        return {col["name"] for col in inspect(sync_conn).get_columns(table)}

    for table in ("transfers", "file_requests"):
        cols = await conn.run_sync(_table_columns, table)
        if "is_disabled" not in cols:
            default = "0" if dialect == "sqlite" else "FALSE"
            await conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN is_disabled BOOLEAN NOT NULL DEFAULT {default}")
            )
        cols = await conn.run_sync(_table_columns, table)
        for col in ("expired_unused_notified", "purge_warned"):
            if col not in cols:
                default = "0" if dialect == "sqlite" else "FALSE"
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col} BOOLEAN NOT NULL DEFAULT {default}")
                )

    def _user_columns(sync_conn) -> set[str]:
        return {col["name"] for col in inspect(sync_conn).get_columns("users")}

    user_cols = await conn.run_sync(_user_columns)
    if "totp_secret" not in user_cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)"))
    user_cols = await conn.run_sync(_user_columns)
    if "totp_enabled" not in user_cols:
        default = "0" if dialect == "sqlite" else "FALSE"
        await conn.execute(
            text(f"ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT {default}")
        )
    user_cols = await conn.run_sync(_user_columns)
    if "display_name" not in user_cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(255)"))
