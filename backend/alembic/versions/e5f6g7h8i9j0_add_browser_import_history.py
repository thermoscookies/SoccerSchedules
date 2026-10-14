"""add provider metadata and browser import history

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6g7h8i9j0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("provider", sa.String(length=50), nullable=True))
    op.add_column("events", sa.Column("source_url", sa.String(length=500), nullable=True))
    op.add_column("events", sa.Column("last_imported_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE events SET provider = 'gotsport' WHERE provider IS NULL")
    op.alter_column("events", "provider", nullable=False, server_default="gotsport")
    op.create_table(
        "browser_imports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("games_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("games_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("games_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_results", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_browser_imports_event_id", "browser_imports", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_browser_imports_event_id", table_name="browser_imports")
    op.drop_table("browser_imports")
    op.drop_column("events", "last_imported_at")
    op.drop_column("events", "source_url")
    op.drop_column("events", "provider")
