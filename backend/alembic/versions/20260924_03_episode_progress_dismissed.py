"""When an episode was last dismissed from the In Progress page (164).

Backfilled to "now" for every existing episode: the point of the column is
telling a *newly* finished episode apart from one that has sat COMPLETE for
months, and without a backfill every episode the library already had would
render as freshly finished the moment this ships. `refresh_episode_status`
clears the column the next time an episode's status genuinely changes, so
anything actually in flight at deploy time reappears on its own once it next
moves - the only cost of the blanket backfill is that an episode already
mid-download will not show until *its* next transition either.

Revision ID: 20260924_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260924_03"
down_revision = "20260924_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("episodes") as batch:
        batch.add_column(sa.Column("progress_dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE episodes SET progress_dismissed_at = CURRENT_TIMESTAMP")


def downgrade() -> None:
    with op.batch_alter_table("episodes") as batch:
        batch.drop_column("progress_dismissed_at")
