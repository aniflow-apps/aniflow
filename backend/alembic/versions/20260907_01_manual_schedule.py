"""What the user says about the schedule, kept apart from what the provider says.

A show can air on a day AniList does not know, at a time it never reports, or
run for more episodes than it lists - and until now the only way to fix that
was to be wrong. The two new columns hold the user's answer; the existing
``air_slot_at`` and ``episode_count`` keep the provider's, untouched, so
"reset to the provider" is a matter of clearing an override rather than
fetching anything again.

Both are nullable and both start NULL: every existing anime behaves exactly as
it did, and nothing needs backfilling.

Revision ID: 20260907_01
"""
import sqlalchemy as sa
from alembic import op

revision = "20260907_01"
down_revision = "20260906_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The instant, not a weekday and an hour: the same shape as the provider's
    # own slot, so one set of helpers derives the weekday, the time and the
    # sort order from either.
    op.add_column("anime", sa.Column("air_slot_override_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("anime", sa.Column("episode_count_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("anime", "episode_count_override")
    op.drop_column("anime", "air_slot_override_at")
