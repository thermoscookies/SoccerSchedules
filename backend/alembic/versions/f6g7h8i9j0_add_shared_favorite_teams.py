"""add shared favorite teams"""
from alembic import op
import sqlalchemy as sa

revision = "f6g7h8i9j0"
down_revision = "e5f6g7h8i9j0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "favorite_teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("division_id", sa.Integer(), nullable=False),
        sa.Column("team_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["division_id"], ["divisions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_favorite_teams_id", "favorite_teams", ["id"])
    op.create_index("ix_favorite_teams_division_id", "favorite_teams", ["division_id"])
    op.create_index(
        "ix_favorite_teams_division_name", "favorite_teams",
        ["division_id", "normalized_name"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_favorite_teams_division_name", table_name="favorite_teams")
    op.drop_index("ix_favorite_teams_division_id", table_name="favorite_teams")
    op.drop_index("ix_favorite_teams_id", table_name="favorite_teams")
    op.drop_table("favorite_teams")
