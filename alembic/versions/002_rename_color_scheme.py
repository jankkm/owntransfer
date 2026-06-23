"""Rename primary_color to color_scheme and drop accent_color."""

from alembic import op
import sqlalchemy as sa

revision = "002_rename_color_scheme"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE app_settings RENAME COLUMN primary_color TO color_scheme")
    op.drop_column("app_settings", "accent_color")


def downgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("accent_color", sa.String(32), nullable=False, server_default="#7c3aed"),
    )
    op.alter_column("app_settings", "color_scheme", new_column_name="primary_color")
