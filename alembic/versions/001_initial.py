"""Initial schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("oauth_provider", sa.String(64)),
        sa.Column("oauth_sub", sa.String(255)),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_name", sa.String(255), nullable=False),
        sa.Column("logo_path", sa.String(1024)),
        sa.Column("primary_color", sa.String(32), nullable=False),
        sa.Column("accent_color", sa.String(32), nullable=False),
        sa.Column("max_file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("default_expiry_days", sa.Integer(), nullable=False),
        sa.Column("max_downloads_default", sa.Integer(), nullable=False),
        sa.Column("smtp_host", sa.String(255)),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_user", sa.String(255)),
        sa.Column("smtp_password", sa.String(255)),
        sa.Column("smtp_from", sa.String(320)),
        sa.Column("smtp_use_tls", sa.Boolean(), nullable=False),
        sa.Column("allow_local_login", sa.Boolean(), nullable=False),
        sa.Column("file_type_blocklist", sa.Text()),
        sa.Column("purge_grace_hours", sa.Integer(), nullable=False),
        sa.Column("setup_completed", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("users")
