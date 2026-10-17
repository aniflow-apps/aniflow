"""When a torrent was first noticed missing from the client.

"The torrent is no longer in qBittorrent" was raised on the first sync pass
that could not find it, which is a few seconds after queueing - and a torrent
being added, checked, categorised and picked up by qBittorrent is not
instantaneous. The warning was therefore usually about something that was
about to work, and it arrived while the user was still watching the download
start.

The column records when the torrent was *first* missed. Nothing is reported
until it has been missing for a few minutes; a torrent that reappears clears
the timestamp and nobody ever hears about it. A torrent that really is gone is
still reported, a little later, which is the right trade for a warning nobody
can act on in the first minute anyway.

Nullable and empty: a torrent that is present has no such moment.

Revision ID: 20260908_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260908_01"
down_revision = "20260907_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("downloads", sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("downloads", "missing_since")
