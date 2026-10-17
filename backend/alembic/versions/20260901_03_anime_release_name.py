"""anime release name

How releases name an anime, e.g. "Youjo Senki S2". Feed items are matched
against this instead of against the metadata provider's titles: the parser
splits the season marker off a release name, so comparing "Youjo Senki"
against "Youjo Senki S2"/"Youjo Senki II"/"Saga of Tanya the Evil Season 2"
never matched and every item was IGNORED as untracked_title.

Backfilled from ``media_folder``, which already held exactly this value - it is
derived from a real release name when the anime is added. The two are separate
columns because the media folder may be renamed later (Shoko Server/AniDB) and that
must not stop new episodes from being found.

Revision ID: 20260901_03
Revises: 20260901_02
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_03"
down_revision = "20260901_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anime", sa.Column("release_name", sa.String(length=255), nullable=True))
    # media_folder is the release-derived folder, so it is the correct seed.
    # Fall back to local_name for rows where it was never set.
    op.execute(
        "UPDATE anime SET release_name = COALESCE(media_folder, local_name) "
        "WHERE release_name IS NULL"
    )


def downgrade() -> None:
    op.drop_column("anime", "release_name")
