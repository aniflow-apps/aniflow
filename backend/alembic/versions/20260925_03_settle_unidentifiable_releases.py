"""Settle every release an EPISODE_NOT_IDENTIFIABLE attention is about (170).

Reported live: dismissed one this morning, it was back again a few hours
later. 20260925_02 reclassified the *attention* records out of
AMBIGUOUS_RELEASE, but left the *release* rows behind them exactly as they
were - still pending, `settled_at` still null, because that column did not
exist as a stop for this case until the same build (168) that shipped
alongside it. So an installation that had already hit this bug, upgraded, and
dismissed the reclassified record still had the queue pass pick the release
back up on its very next run and raise a fresh one - dismissing only closes
the old record, by design, for the kinds where a condition can legitimately
recur (see services/attention.py:actions_for). A batch's name is not one of
those; it is never going to reparse itself.

This backfill is what a fresh EPISODE_NOT_IDENTIFIABLE raise now does for
itself (services/downloads.py, the same build) - just applied once, here, to
whatever the previous migration already reclassified, open or dismissed,
so nobody has to wait out one more queue pass - or dismiss a duplicate - to
be done with it.
"""
from alembic import op

revision = "20260925_03"
down_revision = "20260925_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE releases SET settled_at = CURRENT_TIMESTAMP "
        "WHERE settled_at IS NULL AND id IN ("
        "  SELECT release_id FROM attention "
        "  WHERE kind = 'EPISODE_NOT_IDENTIFIABLE' AND release_id IS NOT NULL"
        ")"
    )


def downgrade() -> None:
    # Not reversible in the other direction: which of these releases were
    # already settled before this ran (a real download landed on them since,
    # say) cannot be told apart from the ones this backfilled. Leaving
    # settled_at as it is is the safer of two wrong answers - re-opening a
    # release that has since actually settled is worse than one that stays
    # settled a downgrade early.
    pass
