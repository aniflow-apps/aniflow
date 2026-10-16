"""Discovered releases can be cleared from the list without being forgotten.

The releases table is also the feed's de-duplication memory: a scan skips a
guid it has already seen. Deleting rows to tidy the list would therefore make
every item in the feed window look new again on the next scan - and each
already-imported episode would produce a fresh "skipped: duplicate" download
row. Clearing hides a row instead, exactly like Clear on the Downloads page,
which leaves the torrent alone.

Revision ID: 20260902_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260902_01"
down_revision = "20260901_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("releases", sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("releases", "cleared_at")
