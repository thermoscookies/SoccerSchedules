"""allow multiple selectable GotSport groups per event"""
from alembic import op
import sqlalchemy as sa

revision = "h8i9j0k1l2"
down_revision = "g7h8i9j0k1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("gotsport_group_id", sa.String(length=50), nullable=True))
    op.drop_index("ix_events_gotsport_event_id", table_name="events")
    op.create_index("ix_events_gotsport_event_id", "events", ["gotsport_event_id"], unique=False)
    op.create_index("ix_events_gotsport_event_group", "events", ["gotsport_event_id", "gotsport_group_id"], unique=True)
    op.execute("UPDATE events SET gotsport_group_id = '501305' WHERE id = 1 AND gotsport_event_id = '51974'")
    op.execute("UPDATE events SET gotsport_group_id = '522955' WHERE id = 2 AND gotsport_event_id = '48745'")
    op.execute("""
        INSERT INTO events (gotsport_event_id, gotsport_group_id, name, location, start_date, end_date, url, status,
                            last_scraped_at, provider, source_url, last_imported_at, created_at, updated_at)
        SELECT gotsport_event_id, '522950', 'SOCAL - GU15 - F2 North', location, start_date, end_date,
               'https://system.gotsport.com/org_event/events/48745/schedules?date=All&group=522950', status,
               NULL, provider, 'https://system.gotsport.com/org_event/events/48745/schedules?date=All&group=522950',
               last_imported_at, created_at, updated_at
        FROM events WHERE id = 2 AND gotsport_event_id = '48745'
    """)
    op.execute("UPDATE divisions SET event_id = (SELECT id FROM events WHERE gotsport_event_id = '48745' AND gotsport_group_id = '522950') WHERE id = 3")
    op.execute("UPDATE browser_imports SET event_id = (SELECT id FROM events WHERE gotsport_event_id = '48745' AND gotsport_group_id = '522950') WHERE source_url LIKE '%group=522950%'")
    op.execute("UPDATE divisions SET display_name = 'GU15 F1 North' WHERE gotsport_division_id = '522955'")
    op.execute("UPDATE divisions SET display_name = 'SOCAL - GU15 - F2 North' WHERE gotsport_division_id = '522950'")


def downgrade() -> None:
    op.execute("UPDATE browser_imports SET event_id = 2 WHERE source_url LIKE '%group=522950%'")
    op.execute("UPDATE divisions SET event_id = 2 WHERE gotsport_division_id = '522950'")
    op.execute("DELETE FROM events WHERE gotsport_event_id = '48745' AND gotsport_group_id = '522950'")
    op.drop_index("ix_events_gotsport_event_group", table_name="events")
    op.drop_index("ix_events_gotsport_event_id", table_name="events")
    op.create_index("ix_events_gotsport_event_id", "events", ["gotsport_event_id"], unique=True)
    op.drop_column("events", "gotsport_group_id")
