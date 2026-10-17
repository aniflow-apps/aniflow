"""Where the queue pass keeps its memory, so Clear all can empty the list (135).

Reported as *"the downloads are fully back, can you please take a look why the
downloads is not clearing."*

The pass that queues MATCHED releases asks for *"a release with no download
row"*, and it had no other memory: a release it had considered and found
nothing to do for was recorded by **creating a download row** - the
"skipped: duplicate" entry with no action on it. That made the row
load-bearing. Clear all deletes every row, which deletes the memory, and the
next pass re-creates one for every release it had just finished with.

``settled_at`` is that memory, on the release where it belongs. It is set when
the episode already has this version, when a newer one is on disk, and when
somebody clears the release's download row - because clearing a row is a
person saying they have dealt with it, which is the same instruction build 128
wrote down for a cleared torrent.

Existing rows get NULL, which means "not considered yet" - the behaviour every
row has today. An already-imported episode's release will be settled the first
time the pass sees it again, and will stop coming back after that.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260917_02"
down_revision = "20260917_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("releases", sa.Column("settled_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("releases", "settled_at")
