"""Airing schedule and a user-supplied cover.

``air_slot_at`` stores one instant a known episode airs, in UTC, rather than a
weekday and a time: the weekly schedule derives both from it in the configured
timezone, and a stored "Tuesday 17:00" would be quietly wrong for anyone whose
TZ is not the one it was written in.

``custom_cover`` is a filename under ``<data_dir>/covers``; the file is deleted
with the anime.

Revision ID: 20260903_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260903_01"
down_revision = "20260902_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anime", sa.Column("air_start_date", sa.Date(), nullable=True))
    op.add_column("anime", sa.Column("air_slot_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("anime", sa.Column("custom_cover", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("anime", "custom_cover")
    op.drop_column("anime", "air_slot_at")
    op.drop_column("anime", "air_start_date")
