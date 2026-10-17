"""Which episode an activity entry is about.

Mirrors ``attention_episode`` (20260911_01) for the same reason: the table
could previously only be filed against an anime, which is too coarse once an
anime can have several episodes moving through the pipeline at once - the one
in-progress page needs to tell "episode 84 went from downloading to complete"
apart from "episode 85 did the same" for the same show. Nullable, because most
existing entries are not about a single episode at all.

Revision ID: 20260924_02
"""
import sqlalchemy as sa
from alembic import op

revision = "20260924_02"
down_revision = "20260924_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("activity") as batch:
        batch.add_column(sa.Column("episode_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_activity_episode",
            "episodes",
            ["episode_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_activity_episode_id", "activity", ["episode_id"])


def downgrade() -> None:
    op.drop_index("ix_activity_episode_id", table_name="activity")
    with op.batch_alter_table("activity") as batch:
        batch.drop_constraint("fk_activity_episode", type_="foreignkey")
        batch.drop_column("episode_id")
