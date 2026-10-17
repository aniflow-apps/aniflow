"""Archive completed episodes; keep their full step history (165).

Two changes, landing together because both come from the same ask - a
completed episode should leave In Progress on its own, and the record of how
it got there should not be lost when it does:

* ``episodes.progress_dismissed_at`` is renamed to ``progress_archived_at``.
  Dismissal used to be the only way a row left the list; now COMPLETE does it
  by itself too, and "dismissed" stopped describing most of what sets it.
  A plain rename - the values already there (backfilled to "now" on
  20260924_03, so the page did not flood on its first deploy) keep meaning
  exactly the same thing under the new name.
* ``episode_progress_events`` is new: one row per real change in what
  episode_view.view_for() reports for an episode, so the In Progress page can
  show the whole story - not just the curated milestones Activity records -
  oldest at the bottom, newest at the top.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260925_01"
down_revision = "20260924_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("episodes") as batch:
        batch.alter_column("progress_dismissed_at", new_column_name="progress_archived_at")

    op.create_table(
        "episode_progress_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "episode_id",
            sa.String(length=36),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(length=40), nullable=True),
        sa.Column("to_state", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_episode_progress_events_episode_id", "episode_progress_events", ["episode_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_episode_progress_events_episode_id", table_name="episode_progress_events")
    op.drop_table("episode_progress_events")
    with op.batch_alter_table("episodes") as batch:
        batch.alter_column("progress_archived_at", new_column_name="progress_dismissed_at")
