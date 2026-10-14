"""add editable division display names"""
from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1"
down_revision = "f6g7h8i9j0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("divisions", sa.Column("display_name", sa.String(length=255), nullable=True))
    op.execute("UPDATE divisions SET display_name = 'GU14 DPL' WHERE gotsport_division_id = '501305'")
    op.execute("UPDATE divisions SET display_name = 'GU15 F1 North' WHERE gotsport_division_id = '522955'")


def downgrade() -> None:
    op.drop_column("divisions", "display_name")
