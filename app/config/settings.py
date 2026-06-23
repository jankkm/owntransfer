from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.env_files import resolve_env_file_variables

DbBackend = Literal["sqlite", "postgres"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="OwnTransfer", alias="APP_NAME")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    db_backend: DbBackend = Field(default="sqlite", alias="DB_BACKEND")
    database_url: Optional[str] = Field(default=None, alias="DATABASE_URL")
    sqlite_path: str = Field(default="/data/owntransfer.db", alias="SQLITE_PATH")
    postgres_host: str = Field(default="db", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_user: str = Field(default="owntransfer", alias="POSTGRES_USER")
    postgres_password: str = Field(default="owntransfer", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="owntransfer", alias="POSTGRES_DB")
    upload_dir: str = Field(default="/data/uploads", alias="UPLOAD_DIR")
    base_url: str = Field(default="http://localhost:8080", alias="BASE_URL")
    trust_proxy_headers: bool = Field(default=False, alias="TRUST_PROXY_HEADERS")
    trusted_proxy_hops: int = Field(default=1, alias="TRUSTED_PROXY_HOPS")
    trusted_proxy_ips: str = Field(default="", alias="TRUSTED_PROXY_IPS")
    display_timezone: str = Field(default="UTC", alias="DISPLAY_TIMEZONE")

    primary_color: str = Field(default="#2563eb", alias="PRIMARY_COLOR")
    accent_color: str = Field(default="#7c3aed", alias="ACCENT_COLOR")
    max_file_size_mb: int = Field(default=2048, alias="MAX_FILE_SIZE_MB")
    default_expiry_days: int = Field(default=7, alias="DEFAULT_EXPIRY_DAYS")
    max_share_expiry_days: int = Field(default=365, alias="MAX_SHARE_EXPIRY_DAYS")
    max_downloads_default: int = Field(default=10, alias="MAX_DOWNLOADS_DEFAULT")
    allow_local_login: bool = Field(default=True, alias="ALLOW_LOCAL_LOGIN")
    allow_user_share_emails: bool = Field(default=True, alias="ALLOW_USER_SHARE_EMAILS")
    purge_grace_hours: int = Field(default=24, alias="PURGE_GRACE_HOURS")
    purge_grace_days: int = Field(default=7, alias="PURGE_GRACE_DAYS")
    purge_notify_days: int = Field(default=0, alias="PURGE_NOTIFY_DAYS")

    entra_tenant_id: Optional[str] = Field(default=None, alias="ENTRA_TENANT_ID")
    entra_client_id: Optional[str] = Field(default=None, alias="ENTRA_CLIENT_ID")
    entra_client_secret: Optional[str] = Field(default=None, alias="ENTRA_CLIENT_SECRET")

    smtp_host: Optional[str] = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: Optional[str] = Field(default=None, alias="SMTP_USER")
    smtp_password: Optional[str] = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from: Optional[str] = Field(default=None, alias="SMTP_FROM")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("db_backend")
    @classmethod
    def normalize_db_backend(cls, value: str) -> str:
        normalized = value.lower().strip()
        if normalized in ("postgresql", "postgres"):
            return "postgres"
        return "sqlite"

    def get_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.db_backend == "postgres":
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        path = Path(self.sqlite_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path}"

    @property
    def is_sqlite(self) -> bool:
        return self.get_database_url().startswith("sqlite")

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def trusted_proxy_ip_list(self) -> list[str]:
        if not self.trusted_proxy_ips.strip():
            return []
        return [part.strip() for part in self.trusted_proxy_ips.split(",") if part.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    resolve_env_file_variables()
    return Settings()


settings = get_settings()
