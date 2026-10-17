"""Remember what was handed to Shoko Server, so it can be found again.

Shoko renames the file and moves it into a folder of its own choosing, and its
naming comes from AniDB while AniFlow's comes from AniList - the two disagree
exactly where it matters. So the file is identified rather than matched: the
inode recorded here survives Shoko's move (a move inside one filesystem is a
rename), and the size covers the case where Shoko copied across a filesystem
boundary instead.

``import_started_at`` is what separates "Shoko is working on it" from "nobody
is working on it": a file still in the import folder hours later was not
recognised, and that is worth telling the user about.

Nothing is backfilled. These columns describe a hand-over that only happens in
Shoko Server mode, and no existing row has ever been through one.

Revision ID: 20260905_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260905_01"
down_revision = "20260904_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episodes", sa.Column("import_inode", sa.BigInteger(), nullable=True))
    op.add_column("episodes", sa.Column("import_size_bytes", sa.Integer(), nullable=True))
    op.add_column(
        "episodes",
        sa.Column("import_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("episodes", "import_started_at")
    op.drop_column("episodes", "import_size_bytes")
    op.drop_column("episodes", "import_inode")
