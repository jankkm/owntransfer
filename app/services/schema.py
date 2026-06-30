from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.models import Base


def _apply_migrations(sync_conn) -> None:
    insp = inspect(sync_conn)
    if "app_settings" not in insp.get_table_names():
        return
    columns = {col["name"] for col in insp.get_columns("app_settings")}
    if "upload_concurrency" not in columns:
        sync_conn.execute(
            text("ALTER TABLE app_settings ADD COLUMN upload_concurrency INTEGER NOT NULL DEFAULT 5")
        )
        sync_conn.commit()

    if "transfers" in insp.get_table_names():
        transfer_cols = {col["name"] for col in insp.get_columns("transfers")}
        if "is_preparing" not in transfer_cols:
            sync_conn.execute(
                text("ALTER TABLE transfers ADD COLUMN is_preparing BOOLEAN NOT NULL DEFAULT FALSE")
            )
            sync_conn.commit()

    if "users" in insp.get_table_names():
        user_columns = {col["name"] for col in insp.get_columns("users")}
        if "locale" not in user_columns:
            sync_conn.execute(text("ALTER TABLE users ADD COLUMN locale VARCHAR(8)"))
            sync_conn.commit()


async def ensure_schema(conn: AsyncConnection) -> None:
    await conn.run_sync(Base.metadata.create_all)
    await conn.run_sync(_apply_migrations)
