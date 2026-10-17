"""When metadata was last asked about, and when to stop asking (build 123).

Two columns, both about the same thing from opposite ends.

``metadata_checked_at`` is how the backfill knows where it got to. Without it
the pass has only "does this anime still have a gap", which cannot tell an
entry nobody has asked about from one the provider has already answered "I do
not know either" for - and a pass that cannot tell those apart re-asks the
second kind for ever. See services/metadata_fill.py.

``releases_ignored`` is the user's answer to the opposite problem:

    *"I downloaded this years ago, I do not care" - AniFlow will not scan or
    look for this file.*

An old series imported from somewhere else has files, no real release name and
no future episodes, and every pass that hunts for releases will keep finding
nothing for it. This says stop - for that anime, and reversibly.

Both default to the behaviour every existing row already has: never checked,
and not ignored.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260916_01"
down_revision = "20260915_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anime", sa.Column("metadata_checked_at", sa.DateTime(timezone=True)))
    op.add_column(
        "anime",
        sa.Column(
            "releases_ignored", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("anime", "releases_ignored")
    op.drop_column("anime", "metadata_checked_at")
