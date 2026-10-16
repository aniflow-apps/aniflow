"""Media compatibility on each episode (build 87).

Whether the media server can play the imported file as it is, or has to
re-encode it first. Read from the file itself - see services/media_probe.py -
and kept here so the watchlist can report it without opening two hundred
files.

Nullable throughout: every existing episode starts unscanned, and unscanned is
a real state rather than a missing value to be filled in with a guess.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260910_01"
down_revision = "20260908_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("episodes") as batch:
        batch.add_column(sa.Column("media_playback", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("media_summary", sa.String(length=200), nullable=True))
        batch.add_column(
            sa.Column(
                "media_findings",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(sa.Column("media_scanned_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("episodes") as batch:
        batch.drop_column("media_scanned_at")
        batch.drop_column("media_findings")
        batch.drop_column("media_summary")
        batch.drop_column("media_playback")
