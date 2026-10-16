"""An anime remembers whether it was added as a tracker or as a download.

AniFlow with no indexer configured cannot download anything, and a screen full
of subgroups, qualities and release names is then worse than useless - it
describes machinery that is not there. In that state it is a watchlist and a
calendar, and an anime added to it is a name and a lifecycle, nothing more.

The column records which of the two an anime was created as. It exists because
the answer cannot be derived later: an anime with no release name might be a
tracker entry, or it might be a download entry somebody never finished setting
up, and those need opposite treatment when an indexer appears.

Everything already in the library is ``advanced``: it was added when an
indexer was the only way in, and it has the fields to prove it.

Revision ID: 20260907_03
"""
import sqlalchemy as sa
from alembic import op

revision = "20260907_03"
down_revision = "20260907_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "anime",
        sa.Column("entry_mode", sa.String(length=16), nullable=False, server_default="advanced"),
    )


def downgrade() -> None:
    op.drop_column("anime", "entry_mode")
