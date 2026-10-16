"""AniDB's own id and its own names, on the anime (build 116).

Two columns for two different reasons.

``anidb_id`` is already known - ``provider_ids["anidb"]`` has held it since
build 83 - but it is held as a string inside a JSON map, which means it cannot
be indexed, cannot be joined on and cannot be asked "which anime is aid
19242". Shoko Server states that id for everything it holds, the numbering
resolver walks prequel chains by it, and the setup dialog now sends it, so it
has earned a column. The JSON entry stays written as well: nothing that reads
it has to change, and a value corrected by hand there still counts.

``anidb_titles`` is new. The weekly dump carries every spelling of a show with
the language it belongs to, and none of it was ever selected: an anime adopted
through AniList arrives with AniList's three titles, which for a sequel are
routinely a different name than the one the release groups use. This is the
bag of names as AniDB files them - ``main`` (romaji, and what Shoko shows),
``en``, ``ja``, and whatever official language the dump happens to have. A
JSON map rather than four columns, because it is honestly a bag of names and a
fifth language should not need a migration.

Empty and null are both the ordinary state for an anime added before this, and
the backfill pass fills them in on the next dump.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_01"
down_revision = "20260913_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anime", sa.Column("anidb_id", sa.Integer(), nullable=True))
    op.add_column(
        "anime",
        sa.Column("anidb_titles", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_anime_anidb_id", "anime", ["anidb_id"])


def downgrade() -> None:
    op.drop_index("ix_anime_anidb_id", table_name="anime")
    op.drop_column("anime", "anidb_titles")
    op.drop_column("anime", "anidb_id")
