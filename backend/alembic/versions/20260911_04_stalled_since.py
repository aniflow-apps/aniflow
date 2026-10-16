"""When a download last started getting nowhere.

qBittorrent says `stalledDL` for a torrent that is connected, wants data, and
is being sent none. Briefly that is normal - every torrent stalls while it
looks for peers - so what is worth reporting is "this has not moved in hours",
and answering that needs to know when it stopped.

Cleared the moment a byte arrives, so no history is kept: the column is a
clock, not a log.

Revision ID: 20260911_04
Revises: 20260911_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260911_04"
down_revision = "20260911_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("downloads") as batch:
        batch.add_column(sa.Column("stalled_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("downloads") as batch:
        batch.drop_column("stalled_since")
