"""The offset a group's numbering carries (build 112).

Some release groups number from the first episode of the franchise and never
restart, so the same episode is "01" to one group and "67" to another. Which
one a watchlist entry follows is decided once, in the setup dialog, and has to
survive it: the weekly episode arrives through the RSS scan months later, and
by then nothing else remembers that SubsPlease counts from 67.

Zero means what it has always meant - the group counts this season from one -
so every existing row keeps behaving exactly as it did.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260913_01"
down_revision = "20260911_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "anime",
        sa.Column("episode_offset", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("anime", "episode_offset")
